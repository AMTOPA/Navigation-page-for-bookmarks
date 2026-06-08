import time
from urllib.parse import urljoin, urlsplit

import httpx

from .config import get_settings
from .crawler import USER_AGENT, validate_public_url


REDIRECT_CODES = {301, 302, 303, 307, 308}
GET_FALLBACK_CODES = {400, 403, 405, 501}
MAX_REDIRECTS = 5
MAX_HEALTH_BYTES = 64 * 1024


def _request(client: httpx.Client, method: str, url: str) -> httpx.Response:
    hostname, resolved_ip = validate_public_url(url)
    parts = urlsplit(url)
    host_for_url = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
    netloc = f"{host_for_url}:{parts.port}" if parts.port else host_for_url
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.8,*/*;q=0.2",
        "Host": parts.netloc,
    }
    request = client.build_request(
        method,
        parts._replace(netloc=netloc).geturl(),
        headers=headers,
        extensions={"sni_hostname": hostname.encode("ascii")},
    )
    return client.send(request, stream=method == "GET")


def _follow(client: httpx.Client, method: str, url: str) -> tuple[httpx.Response, str, bool]:
    current = url
    redirected = False
    for _ in range(MAX_REDIRECTS + 1):
        response = _request(client, method, current)
        if response.status_code not in REDIRECT_CODES:
            return response, current, redirected
        location = response.headers.get("location")
        response.close()
        if not location:
            raise ValueError("重定向缺少目标地址")
        current = urljoin(current, location)
        redirected = True
    raise ValueError("重定向次数过多")


def check_link_health(url: str) -> dict:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return {
            "status": "unsupported",
            "http_status": None,
            "final_url": url,
            "latency_ms": 0,
            "error": "仅支持公网 HTTP/HTTPS 地址",
        }

    started = time.perf_counter()
    settings = get_settings()
    timeout = httpx.Timeout(settings.health_check_timeout_seconds)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            response, final_url, redirected = _follow(client, "HEAD", url)
            if response.status_code in GET_FALLBACK_CODES:
                response.close()
                response, final_url, redirected = _follow(client, "GET", url)
                consumed = 0
                for chunk in response.iter_bytes():
                    consumed += len(chunk)
                    if consumed >= MAX_HEALTH_BYTES:
                        break
            status_code = response.status_code
            response.close()
        if 200 <= status_code < 400:
            status = "redirected" if redirected or final_url != url else "ok"
            error = ""
        else:
            status = "failed"
            error = f"HTTP {status_code}"
        return {
            "status": status,
            "http_status": status_code,
            "final_url": final_url,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": error,
        }
    except httpx.TimeoutException:
        return {
            "status": "timeout",
            "http_status": None,
            "final_url": url,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": "连接超时",
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "status": "failed",
            "http_status": None,
            "final_url": url,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": str(exc)[:500],
        }
