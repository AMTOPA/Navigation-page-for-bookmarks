import ipaddress
import time
from pathlib import Path

from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .config import get_settings


def client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


class Denylist:
    def __init__(self, path: Path):
        self.path = path
        self._mtime = 0.0
        self._loaded_at = 0.0
        self._networks: list[ipaddress._BaseNetwork] = []

    def _load(self) -> None:
        now = time.time()
        if now - self._loaded_at < 5:
            return
        self._loaded_at = now
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = 0.0
            self._networks = []
            return
        if mtime == self._mtime:
            return
        networks: list[ipaddress._BaseNetwork] = []
        for line in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            value = line.split("#", 1)[0].strip()
            if not value:
                continue
            try:
                networks.append(ipaddress.ip_network(value, strict=False))
            except ValueError:
                continue
        self._mtime = mtime
        self._networks = networks

    def contains(self, raw_ip: str) -> bool:
        try:
            address = ipaddress.ip_address(raw_ip)
        except ValueError:
            return False
        self._load()
        return any(address in network for network in self._networks)


class DenylistMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.denylist = Denylist(get_settings().denylist_path)

    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/api/health" and self.denylist.contains(client_ip(request)):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "访问异常"}, status_code=403)
            return HTMLResponse(
                "<!doctype html><meta charset='utf-8'><title>访问异常</title>"
                "<body style='font-family:system-ui,sans-serif;padding:48px'>访问异常</body>",
                status_code=403,
            )
        return await call_next(request)
