import hashlib
import ipaddress
import re
import socket
from io import BytesIO
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura
from bs4 import BeautifulSoup
from charset_normalizer import from_bytes
from pypdf import PdfReader

from .config import get_settings


USER_AGENT = "EdgeBookmarkNavigator/2.0 (+private bookmark metadata fetcher)"
CHARSET_PATTERN = re.compile(
    br"""charset\s*=\s*["']?\s*([a-zA-Z0-9._:+-]+)""",
    re.IGNORECASE,
)
MOJIBAKE_MARKERS = (
    "\u951f\u65a4\u62f7",
    "\u9428\u52e3",
    "\u9225",
    "\u9286",
    "\u93c0\u60f0\u68cc",
    "\u93bc\u6ec5\u50a8",
    "\u95be\u70ac\u5e34",
    "\u9983",
    "\u00c3",
    "\u00c2",
    "\u00e2\u20ac",
    "\u00e4\u00b8",
    "\ufffd",
)


def validate_public_url(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("仅支持公网 HTTP/HTTPS 地址")
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError(f"域名解析失败：{exc}") from exc
    public_ips = []
    for entry in addresses:
        ip = ipaddress.ip_address(entry[4][0])
        if not ip.is_global:
            raise ValueError("目标解析到内网、回环或保留地址")
        public_ips.append(str(ip))
    public_ips.sort(key=lambda value: ipaddress.ip_address(value).version)
    return parts.hostname, public_ips[0]


def fetch_public(url: str, etag: str = "", last_modified: str = "") -> httpx.Response:
    settings = get_settings()
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    current = url
    with httpx.Client(timeout=settings.crawl_timeout_seconds, follow_redirects=False, trust_env=False) as client:
        for _ in range(6):
            hostname, resolved_ip = validate_public_url(current)
            parts = urlsplit(current)
            host_for_url = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
            netloc = f"{host_for_url}:{parts.port}" if parts.port else host_for_url
            response = client.get(
                parts._replace(netloc=netloc).geturl(),
                headers={**headers, "Host": parts.netloc},
                extensions={"sni_hostname": hostname.encode("ascii")},
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("重定向缺少目标地址")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            return response
    raise ValueError("重定向次数过多")


def extract_pdf(content: bytes) -> dict:
    reader = PdfReader(BytesIO(content))
    text = "\n".join((page.extract_text() or "") for page in reader.pages[:80])[:250_000]
    title = str(reader.metadata.title or "") if reader.metadata else ""
    return {
        "title": title,
        "description": "",
        "favicon": "",
        "image": "",
        "text": text,
        "outline": [line.strip() for line in text.splitlines() if 3 < len(line.strip()) < 100][:20],
    }


def _charset_candidates(content: bytes, content_type: str = "") -> list[str]:
    candidates = []
    header_match = CHARSET_PATTERN.search(content_type.encode("ascii", errors="ignore"))
    meta_match = CHARSET_PATTERN.search(content[:8192])
    if content.startswith(b"\xef\xbb\xbf"):
        candidates.append("utf-8-sig")
    elif content.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates.append("utf-16")
    candidates.append("utf-8")
    if header_match:
        candidates.append(header_match.group(1).decode("ascii", errors="ignore"))
    if meta_match:
        candidates.append(meta_match.group(1).decode("ascii", errors="ignore"))
    detected = from_bytes(content).best()
    if detected and detected.encoding:
        candidates.append(detected.encoding)
    candidates.extend(("gb18030", "big5", "windows-1252"))
    return list(dict.fromkeys(value.strip().lower() for value in candidates if value.strip()))


def _decoded_text_score(text: str, position: int) -> float:
    if not text:
        return -10_000
    controls = sum(ord(char) < 32 and char not in "\n\r\t" for char in text)
    suspicious = sum(text.count(marker) for marker in MOJIBAKE_MARKERS)
    printable = sum(char.isprintable() or char in "\n\r\t" for char in text) / len(text)
    # Candidate order is meaningful, but clean text always wins over a declared
    # charset that produces mojibake.
    return printable * 100 - controls * 8 - suspicious * 20 - position * 0.05


def decode_html(content: bytes, content_type: str = "") -> tuple[str, str]:
    decoded = []
    for position, encoding in enumerate(_charset_candidates(content, content_type)):
        try:
            text = content.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
        decoded.append((_decoded_text_score(text, position), text, encoding))
    if not decoded:
        return content.decode("utf-8", errors="replace"), "utf-8-replace"
    _, text, encoding = max(decoded, key=lambda item: item[0])
    return text, encoding


def extract_html(content: bytes, final_url: str, content_type: str = "") -> dict:
    text_value, encoding = decode_html(content, content_type)
    soup = BeautifulSoup(text_value, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    description_tag = soup.find("meta", attrs={"name": lambda value: value and value.lower() == "description"})
    if not description_tag:
        description_tag = soup.find("meta", property="og:description")
    description = description_tag.get("content", "").strip() if description_tag else ""
    icon = soup.find("link", rel=lambda value: value and "icon" in " ".join(value if isinstance(value, list) else [value]).lower())
    favicon = urljoin(final_url, icon.get("href", "")) if icon and icon.get("href") else ""
    image_tag = soup.find("meta", property="og:image")
    image = urljoin(final_url, image_tag.get("content", "")) if image_tag and image_tag.get("content") else ""
    extracted = trafilatura.extract(text_value, include_links=True, include_formatting=False) or ""
    headings = [
        heading.get_text(" ", strip=True)
        for heading in soup.find_all(["h1", "h2", "h3"])
        if heading.get_text(" ", strip=True)
    ][:30]
    return {
        "title": title,
        "description": description,
        "favicon": favicon,
        "image": image,
        "text": extracted[:250_000],
        "outline": headings,
        "encoding": encoding,
    }


def crawl_url(url: str, etag: str = "", last_modified: str = "") -> dict:
    response = fetch_public(url, etag, last_modified)
    if response.status_code == 304:
        return {"not_modified": True}
    if len(response.content) > 15 * 1024 * 1024:
        raise ValueError("页面内容超过 15MB 限制")
    content_type = response.headers.get("content-type", "").lower()
    if "pdf" in content_type or response.url.path.lower().endswith(".pdf"):
        result = extract_pdf(response.content)
    elif "html" in content_type or not content_type:
        result = extract_html(response.content, str(response.url), content_type)
    else:
        raise ValueError(f"不支持的内容类型：{content_type}")
    result.update(
        {
            "not_modified": False,
            "content_hash": hashlib.sha256(response.content).hexdigest(),
            "etag": response.headers.get("etag", ""),
            "last_modified": response.headers.get("last-modified", ""),
            "final_url": str(response.url),
        }
    )
    return result
