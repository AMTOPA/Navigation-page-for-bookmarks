import hashlib
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .crawler import fetch_public
from .models import Bookmark, MediaAsset, utcnow
from .services import aware_utc


ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/x-icon", "image/vnd.microsoft.icon"}
EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
}


def cache_media(db: Session, bookmark: Bookmark, field: str) -> dict:
    source_url = getattr(bookmark, field, "") or ""
    cache_field = f"{field}_cache_url"
    if not source_url.startswith(("http://", "https://")):
        setattr(bookmark, cache_field, "/assets/default.svg")
        return {"default": True}
    asset = db.scalar(select(MediaAsset).where(MediaAsset.source_url == source_url))
    if asset and asset.status == "success" and Path(asset.local_path).is_file():
        setattr(bookmark, cache_field, asset.public_url)
        return {"cached": True}
    if asset and asset.retry_after and aware_utc(asset.retry_after) > utcnow():
        setattr(bookmark, cache_field, "/assets/default.svg")
        return {"deferred": True}
    if not asset:
        asset = MediaAsset(source_url=source_url, source_hash=hashlib.sha256(source_url.encode()).hexdigest())
        db.add(asset)
        db.flush()
    try:
        response = fetch_public(source_url)
        if len(response.content) > 5 * 1024 * 1024:
            raise ValueError("图片超过 5MB 限制")
        media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if media_type not in ALLOWED_IMAGE_TYPES:
            raise ValueError(f"不支持的图片类型：{media_type or '未知'}")
        digest = hashlib.sha256(response.content).hexdigest()
        root = get_settings().data_dir / "media"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{digest}{EXTENSIONS[media_type]}"
        if not path.exists():
            path.write_bytes(response.content)
        asset.content_hash = digest
        asset.local_path = str(path.resolve())
        asset.public_url = f"/api/media/{asset.id}"
        asset.media_type = media_type
        asset.status = "success"
        asset.error = ""
        asset.retry_after = None
        setattr(bookmark, cache_field, asset.public_url)
        return {"cached": False}
    except Exception as exc:
        asset.status = "failed"
        asset.error = str(exc)[:1000]
        asset.retry_after = utcnow() + timedelta(days=7)
        setattr(bookmark, cache_field, "/assets/default.svg")
        return {"default": True, "error": asset.error}
