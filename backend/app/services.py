import hashlib
import posixpath
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditLog, Bookmark, BookmarkSource, Job, JobBatch


TRACKING_PARAMS = {"fbclid", "gclid", "msclkid"}


def aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def normalize_url(url: str) -> str:
    value = url.strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"}:
        return value
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    path = posixpath.normpath(path)
    if parts.path.endswith("/") and not path.endswith("/"):
        path += "/"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
        ),
        doseq=True,
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def bookmark_to_dict(bookmark: Bookmark) -> dict:
    return {
        "id": bookmark.id,
        "url": bookmark.url,
        "title": bookmark.title,
        "description": bookmark.description,
        "favicon": bookmark.favicon,
        "image": bookmark.image,
        "display_image_url": bookmark.favicon_cache_url or bookmark.image_cache_url or "/assets/default.svg",
        "ai_category": bookmark.ai_category,
        "manual_category": bookmark.manual_category,
        "final_category": bookmark.final_category,
        "tags": bookmark.tags or [],
        "ai_importance": bookmark.ai_importance,
        "manual_importance": bookmark.manual_importance,
        "importance": bookmark.importance,
        "notes": bookmark.notes,
        "summary": bookmark.summary,
        "outline": bookmark.outline or [],
        "keywords": bookmark.keywords or [],
        "key_info": bookmark.key_info or {},
        "manual_override_fields": bookmark.manual_override_fields or [],
        "is_crawled": bookmark.is_crawled,
        "is_ai_classified": bookmark.is_ai_classified,
        "crawl_status": bookmark.crawl_status,
        "ai_status": bookmark.ai_status,
        "crawl_error": bookmark.crawl_error,
        "ai_error": bookmark.ai_error,
        "favorited_at": bookmark.favorited_at,
        "created_at": bookmark.created_at,
        "updated_at": bookmark.updated_at,
        "last_crawled_at": bookmark.last_crawled_at,
        "last_ai_processed_at": bookmark.last_ai_processed_at,
        "deleted_at": bookmark.deleted_at,
        "local_copy_url": f"/api/files/{bookmark.id}" if bookmark.local_copy_path else "",
        "local_copy_name": bookmark.local_copy_name,
        "local_copy_mime": bookmark.local_copy_mime,
        "local_copy_size": bookmark.local_copy_size,
        "sources": bookmark.sources,
    }


def bookmark_compact_dict(bookmark: Bookmark) -> dict:
    folders = sorted(
        {
            source.folder_path
            for source in bookmark.sources
            if source.is_active and source.folder_path
        }
    )
    return {
        "id": bookmark.id,
        "url": bookmark.url,
        "title": bookmark.title,
        "description": bookmark.description,
        "summary": bookmark.summary,
        "display_image_url": bookmark.favicon_cache_url or bookmark.image_cache_url or "/assets/default.svg",
        "final_category": bookmark.final_category,
        "manual_category": bookmark.manual_category,
        "tags": bookmark.tags or [],
        "importance": bookmark.importance,
        "notes": bookmark.notes,
        "favorited_at": bookmark.favorited_at,
        "updated_at": bookmark.updated_at,
        "crawl_status": bookmark.crawl_status,
        "ai_status": bookmark.ai_status,
        "local_copy_url": f"/api/files/{bookmark.id}" if bookmark.local_copy_path else "",
        "folder_paths": folders,
    }


def get_or_create_bookmark(db: Session, url: str, title: str = "", favorited_at=None) -> tuple[Bookmark, bool]:
    normalized = normalize_url(url)
    bookmark = db.scalar(select(Bookmark).where(Bookmark.normalized_url == normalized))
    if bookmark:
        if not bookmark.title and title:
            bookmark.title = title
        if favorited_at and (
            not bookmark.favorited_at or aware_utc(favorited_at) < aware_utc(bookmark.favorited_at)
        ):
            bookmark.favorited_at = favorited_at
        bookmark.deleted_at = None
        return bookmark, False
    bookmark = Bookmark(
        url=url,
        normalized_url=normalized,
        title=title or url,
        favorited_at=favorited_at,
    )
    db.add(bookmark)
    db.flush()
    return bookmark, True


def upsert_source(
    db: Session,
    bookmark: Bookmark,
    source_type: str,
    external_id: str,
    folder_path: str = "",
    title: str = "",
    url: str = "",
    favorited_at=None,
    batch_id: str = "",
) -> BookmarkSource:
    source = db.scalar(
        select(BookmarkSource).where(
            BookmarkSource.source_type == source_type,
            BookmarkSource.external_id == external_id,
        )
    )
    if not source:
        source = BookmarkSource(source_type=source_type, external_id=external_id)
        db.add(source)
    source.bookmark = bookmark
    source.folder_path = folder_path
    source.source_title = title
    source.source_url = url
    source.favorited_at = favorited_at
    source.last_sync_batch = batch_id
    source.is_active = True
    return source


def create_job_batch(db: Session, name: str, job_type: str, created_by: str = "") -> JobBatch:
    batch = JobBatch(name=name, job_type=job_type, created_by=created_by)
    db.add(batch)
    db.flush()
    return batch


def queue_job(
    db: Session,
    bookmark_id: str,
    job_type: str,
    force: bool = False,
    batch_id: str | None = None,
) -> Job:
    existing = db.scalar(
        select(Job).where(
            Job.bookmark_id == bookmark_id,
            Job.job_type == job_type,
            Job.status.in_(["queued", "running"]),
        )
    )
    if existing and not force:
        return existing
    job = Job(bookmark_id=bookmark_id, batch_id=batch_id, job_type=job_type, payload={"force": force})
    db.add(job)
    return job


def audit(db: Session, event_type: str, message: str, actor: str = "", level: str = "info", **details):
    db.add(AuditLog(event_type=event_type, message=message, actor=actor, level=level, details=details))
