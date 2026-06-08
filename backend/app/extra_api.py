import hashlib
import mimetypes
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .db import get_db
from .importers import parse_edge_html
from .models import ApiToken, AuditLog, Bookmark, BookmarkSource, Job, LinkHealth, MediaAsset, Setting, SyncRun, utcnow
from .schemas import AISettingsUpdate, EdgeSyncRequest, HealthCheckSettingsUpdate, TokenCreate
from .security import (
    encrypt_value,
    hash_token,
    require_read,
    require_session,
    require_session_write,
    require_sync,
    require_write,
)
from .services import audit, create_job_batch, get_or_create_bookmark, normalize_url, queue_job, upsert_source


router = APIRouter(prefix="/api")
settings = get_settings()


@router.post("/import/edge-html")
async def import_edge_html(
    upload: UploadFile = File(...),
    principal: dict = Depends(require_write),
    db: Session = Depends(get_db),
):
    content = await upload.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="HTML 文件过大")
    records = parse_edge_html(content)
    added = updated = 0
    batch_id = "html-" + secrets.token_hex(8)
    job_batch = create_job_batch(db, f"导入 {upload.filename or 'Edge HTML'}", "import", principal["id"])
    for record in records:
        bookmark, created = get_or_create_bookmark(
            db, record["url"], record["title"], record["favorited_at"]
        )
        added += int(created)
        updated += int(not created)
        if record["icon"] and not bookmark.favicon:
            bookmark.favicon = record["icon"]
        upsert_source(
            db,
            bookmark,
            "edge_html",
            record["external_id"],
            record["folder_path"],
            record["title"],
            record["url"],
            record["favorited_at"],
            batch_id,
        )
        if created or not bookmark.is_crawled:
            queue_job(db, bookmark.id, "crawl", batch_id=job_batch.id)
        if created or not bookmark.is_ai_classified:
            queue_job(db, bookmark.id, "ai", batch_id=job_batch.id)
        queue_job(db, bookmark.id, "search_index", batch_id=job_batch.id)
    audit(
        db,
        "edge_html_import",
        "导入 Edge HTML",
        actor=principal["id"],
        filename=upload.filename,
        total=len(records),
        added=added,
    )
    db.commit()
    return {"total": len(records), "added": added, "updated": updated}


@router.post("/sync/edge")
def sync_edge(
    payload: EdgeSyncRequest,
    principal: dict = Depends(require_sync),
    db: Session = Depends(get_db),
):
    existing_run = db.scalar(select(SyncRun).where(SyncRun.batch_id == payload.batch_id))
    if existing_run:
        return {
            "batch_id": existing_run.batch_id,
            "added": existing_run.added,
            "updated": existing_run.updated,
            "removed": existing_run.removed,
            "duplicate": True,
        }
    added = updated = removed = 0
    job_batch = create_job_batch(db, "Edge 增量同步处理", "sync", principal["id"])
    for item in payload.upserts:
        existing_source = db.scalar(
            select(BookmarkSource).where(
                BookmarkSource.source_type == "edge_sync",
                BookmarkSource.external_id == item.guid,
            )
        )
        if existing_source:
            old_bookmark = existing_source.bookmark
            bookmark = old_bookmark
            created = False
            source_url_changed = existing_source.source_url and existing_source.source_url != item.url
            if source_url_changed and "url" not in set(bookmark.manual_override_fields or []):
                normalized = normalize_url(item.url)
                target = db.scalar(
                    select(Bookmark).where(
                        Bookmark.normalized_url == normalized,
                        Bookmark.id != bookmark.id,
                    )
                )
                if target:
                    existing_source.bookmark = target
                    bookmark = target
                    has_other_source = db.scalar(
                        select(BookmarkSource.id).where(
                            BookmarkSource.bookmark_id == old_bookmark.id,
                            BookmarkSource.is_active.is_(True),
                            BookmarkSource.id != existing_source.id,
                        ).limit(1)
                    )
                    if not has_other_source:
                        old_bookmark.deleted_at = utcnow()
                else:
                    bookmark.url = item.url
                    bookmark.normalized_url = normalized
                    bookmark.is_crawled = False
                    bookmark.is_ai_classified = False
        else:
            bookmark, created = get_or_create_bookmark(db, item.url, item.title, item.favorited_at)
        upsert_source(
            db,
            bookmark,
            "edge_sync",
            item.guid,
            item.folder_path,
            item.title,
            item.url,
            item.favorited_at,
            payload.batch_id,
        )
        added += int(created)
        updated += int(not created)
        if created or not bookmark.is_crawled:
            queue_job(db, bookmark.id, "crawl", batch_id=job_batch.id)
        if created or not bookmark.is_ai_classified:
            queue_job(db, bookmark.id, "ai", batch_id=job_batch.id)
        queue_job(db, bookmark.id, "search_index", batch_id=job_batch.id)
    if payload.removed_guids:
        sources = db.scalars(
            select(BookmarkSource).where(
                BookmarkSource.source_type == "edge_sync",
                BookmarkSource.external_id.in_(payload.removed_guids),
            )
        ).all()
        for source in sources:
            if source.is_active:
                source.is_active = False
                source.last_sync_batch = payload.batch_id
                removed += 1
                active_source = db.scalar(
                    select(BookmarkSource.id).where(
                        BookmarkSource.bookmark_id == source.bookmark_id,
                        BookmarkSource.is_active.is_(True),
                        BookmarkSource.id != source.id,
                    ).limit(1)
                )
                if not active_source:
                    source.bookmark.deleted_at = utcnow()
                    queue_job(db, source.bookmark.id, "search_index", force=True, batch_id=job_batch.id)
    run = SyncRun(
        batch_id=payload.batch_id,
        snapshot_hash=payload.snapshot_hash,
        added=added,
        updated=updated,
        removed=removed,
    )
    db.add(run)
    audit(db, "edge_sync", "Edge 增量同步完成", actor=principal["id"], added=added, updated=updated, removed=removed)
    db.commit()
    return {"batch_id": payload.batch_id, "added": added, "updated": updated, "removed": removed}


@router.post("/sync/attachment")
async def upload_attachment(
    guid: str = Form(...),
    upload: UploadFile = File(...),
    principal: dict = Depends(require_sync),
    db: Session = Depends(get_db),
):
    source = db.scalar(
        select(BookmarkSource).where(
            BookmarkSource.source_type == "edge_sync",
            BookmarkSource.external_id == guid,
        )
    )
    if not source:
        raise HTTPException(status_code=404, detail="未找到对应 Edge 收藏")
    suffix = Path(upload.filename or "").suffix.lower()
    allowed = {item.strip().lower() for item in settings.allowed_attachment_extensions.split(",")}
    if suffix not in allowed:
        raise HTTPException(status_code=415, detail="不允许上传该文件类型")
    content = await upload.read(settings.max_attachment_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_attachment_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="附件过大")
    digest = hashlib.sha256(content).hexdigest()
    attachment_dir = settings.data_dir / "attachments"
    attachment_dir.mkdir(parents=True, exist_ok=True)
    destination = attachment_dir / f"{digest}{suffix}"
    if not destination.exists():
        destination.write_bytes(content)
    bookmark = source.bookmark
    bookmark.local_copy_path = str(destination.resolve())
    bookmark.local_copy_name = Path(upload.filename or f"attachment{suffix}").name
    bookmark.local_copy_mime = upload.content_type or mimetypes.guess_type(bookmark.local_copy_name)[0] or "application/octet-stream"
    bookmark.local_copy_size = len(content)
    audit(db, "attachment_uploaded", "上传本地收藏附件", actor=principal["id"], bookmark_id=bookmark.id, size=len(content))
    db.commit()
    return {"ok": True, "bookmark_id": bookmark.id, "url": f"/api/files/{bookmark.id}"}


@router.get("/files/{bookmark_id}")
def get_attachment(
    bookmark_id: str,
    _principal: dict = Depends(require_read),
    db: Session = Depends(get_db),
):
    bookmark = db.get(Bookmark, bookmark_id)
    if not bookmark or not bookmark.local_copy_path:
        raise HTTPException(status_code=404, detail="附件不存在")
    path = Path(bookmark.local_copy_path).resolve()
    attachment_root = (settings.data_dir / "attachments").resolve()
    if attachment_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="附件文件缺失")
    response = FileResponse(path, media_type=bookmark.local_copy_mime, filename=bookmark.local_copy_name)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if bookmark.local_copy_mime in {"text/html", "image/svg+xml"}:
        response.headers["Content-Disposition"] = f'attachment; filename="{bookmark.local_copy_name}"'
    return response


@router.get("/media/{asset_id}")
def get_media(
    asset_id: str,
    _principal: dict = Depends(require_read),
    db: Session = Depends(get_db),
):
    asset = db.get(MediaAsset, asset_id)
    if not asset or asset.status != "success" or not asset.local_path:
        raise HTTPException(status_code=404, detail="图片不存在")
    path = Path(asset.local_path).resolve()
    media_root = (settings.data_dir / "media").resolve()
    if media_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="图片文件缺失")
    response = FileResponse(path, media_type=asset.media_type)
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/jobs")
def list_jobs(
    status: str = "",
    job_type: str = "",
    page: int | None = None,
    page_size: int = 20,
    _principal: dict = Depends(require_session),
    db: Session = Depends(get_db),
):
    stmt = select(Job)
    if status:
        stmt = stmt.where(Job.status == status)
    if job_type:
        stmt = stmt.where(Job.job_type == job_type)
    if page is None:
        return db.scalars(stmt.order_by(Job.created_at.desc()).limit(500)).all()
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    count_stmt = select(func.count(Job.id))
    if status:
        count_stmt = count_stmt.where(Job.status == status)
    if job_type:
        count_stmt = count_stmt.where(Job.job_type == job_type)
    total = db.scalar(count_stmt) or 0
    items = db.scalars(
        stmt.order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    _principal: dict = Depends(require_session_write),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    job.status = "queued"
    job.error = ""
    job.attempts = 0
    db.commit()
    return {"ok": True}


@router.get("/logs")
def list_logs(
    event_type: str = "",
    level: str = "",
    page: int | None = None,
    page_size: int = 20,
    _principal: dict = Depends(require_session),
    db: Session = Depends(get_db),
):
    stmt = select(AuditLog)
    if event_type:
        stmt = stmt.where(AuditLog.event_type == event_type)
    if level:
        stmt = stmt.where(AuditLog.level == level)
    if page is None:
        return db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(500)).all()
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    count_stmt = select(func.count(AuditLog.id))
    if event_type:
        count_stmt = count_stmt.where(AuditLog.event_type == event_type)
    if level:
        count_stmt = count_stmt.where(AuditLog.level == level)
    total = db.scalar(count_stmt) or 0
    items = db.scalars(
        stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def _health_dict(health: LinkHealth, bookmark: Bookmark) -> dict:
    return {
        "id": health.id,
        "bookmark_id": bookmark.id,
        "title": bookmark.title,
        "url": bookmark.url,
        "status": health.status,
        "http_status": health.http_status,
        "final_url": health.final_url,
        "latency_ms": health.latency_ms,
        "error": health.error,
        "checked_at": health.checked_at,
    }


@router.get("/health-checks")
def list_health_checks(
    status: str = "",
    page: int = 1,
    page_size: int = 20,
    _principal: dict = Depends(require_session),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    stmt = (
        select(LinkHealth)
        .join(Bookmark, Bookmark.id == LinkHealth.bookmark_id)
        .where(Bookmark.deleted_at.is_(None))
        .options(selectinload(LinkHealth.bookmark))
    )
    count_stmt = (
        select(func.count(LinkHealth.id))
        .join(Bookmark, Bookmark.id == LinkHealth.bookmark_id)
        .where(Bookmark.deleted_at.is_(None))
    )
    if status:
        stmt = stmt.where(LinkHealth.status == status)
        count_stmt = count_stmt.where(LinkHealth.status == status)
    total = db.scalar(count_stmt) or 0
    items = db.scalars(
        stmt.order_by(LinkHealth.checked_at.desc(), LinkHealth.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_health_dict(item, item.bookmark) for item in items],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.post("/health-checks/run")
def run_health_checks(
    bookmark_ids: list[str] | None = None,
    principal: dict = Depends(require_session_write),
    db: Session = Depends(get_db),
):
    stmt = select(Bookmark.id).where(Bookmark.deleted_at.is_(None))
    if bookmark_ids:
        stmt = stmt.where(Bookmark.id.in_(bookmark_ids))
    ids = list(db.scalars(stmt).all())
    batch = create_job_batch(db, "链接健康检查", "health_check", principal["id"])
    for bookmark_id in ids:
        queue_job(db, bookmark_id, "health_check", force=True, batch_id=batch.id)
    audit(db, "health_check_queued", "链接健康检查已加入队列", actor=principal["id"], count=len(ids))
    db.commit()
    return {"ok": True, "count": len(ids), "batch_id": batch.id}


@router.post("/health-checks/{bookmark_id}/run")
def run_single_health_check(
    bookmark_id: str,
    principal: dict = Depends(require_session_write),
    db: Session = Depends(get_db),
):
    bookmark = db.get(Bookmark, bookmark_id)
    if not bookmark or bookmark.deleted_at:
        raise HTTPException(status_code=404, detail="收藏不存在")
    queue_job(db, bookmark.id, "health_check", force=True)
    audit(db, "health_check_queued", "链接健康检查已加入队列", actor=principal["id"], bookmark_id=bookmark.id)
    db.commit()
    return {"ok": True}


@router.get("/settings/health-check")
def get_health_check_settings(
    _principal: dict = Depends(require_session),
    db: Session = Depends(get_db),
):
    enabled = db.get(Setting, "health_check_enabled")
    interval = db.get(Setting, "health_check_interval_days")
    last_run = db.get(Setting, "health_check_last_scheduled_at")
    return {
        "enabled": bool(enabled and enabled.value == "1"),
        "interval_days": int(interval.value) if interval and interval.value.isdigit() else 7,
        "last_scheduled_at": last_run.value if last_run else "",
    }


@router.put("/settings/health-check")
def update_health_check_settings(
    payload: HealthCheckSettingsUpdate,
    principal: dict = Depends(require_session_write),
    db: Session = Depends(get_db),
):
    values = {
        "health_check_enabled": "1" if payload.enabled else "0",
        "health_check_interval_days": str(payload.interval_days),
    }
    for key, value in values.items():
        setting = db.get(Setting, key) or Setting(key=key)
        setting.value = value
        db.add(setting)
    audit(db, "health_check_settings_updated", "更新链接健康检查设置", actor=principal["id"])
    db.commit()
    return {"ok": True}


@router.get("/sync/runs")
def list_sync_runs(
    _principal: dict = Depends(require_session),
    db: Session = Depends(get_db),
):
    return db.scalars(select(SyncRun).order_by(SyncRun.created_at.desc()).limit(200)).all()


@router.get("/settings/ai")
def get_ai_settings(
    _principal: dict = Depends(require_session),
    db: Session = Depends(get_db),
):
    values = {item.key: item.value for item in db.scalars(select(Setting).where(Setting.key.in_(["ai_base_url", "ai_model", "ai_api_key"]))).all()}
    encrypted_key = values.get("ai_api_key", "")
    return {
        "base_url": values.get("ai_base_url", "https://open.bigmodel.cn/api/paas/v4"),
        "model": values.get("ai_model", "glm-4-flash"),
        "api_key_configured": bool(encrypted_key),
        "api_key_masked": "********" if encrypted_key else "",
    }


@router.put("/settings/ai")
def update_ai_settings(
    payload: AISettingsUpdate,
    principal: dict = Depends(require_session_write),
    db: Session = Depends(get_db),
):
    updates = {"ai_base_url": payload.base_url.rstrip("/"), "ai_model": payload.model}
    if payload.api_key and payload.api_key != "********":
        updates["ai_api_key"] = encrypt_value(payload.api_key)
    for key, value in updates.items():
        setting = db.get(Setting, key) or Setting(key=key)
        setting.value = value
        setting.encrypted = key == "ai_api_key"
        db.add(setting)
    if "ai_api_key" in updates:
        blocked_jobs = db.scalars(
            select(Job).where(Job.job_type == "ai", Job.status == "blocked")
        ).all()
        for job in blocked_jobs:
            job.status = "queued"
            job.error = ""
            job.attempts = 0
    audit(db, "settings_updated", "更新 AI 配置", actor=principal["id"])
    db.commit()
    return {"ok": True}


@router.get("/settings/tokens")
def list_tokens(
    _principal: dict = Depends(require_session),
    db: Session = Depends(get_db),
):
    tokens = db.scalars(select(ApiToken).order_by(ApiToken.created_at.desc())).all()
    return [
        {
            "id": token.id,
            "name": token.name,
            "token_prefix": token.token_prefix,
            "scopes": token.scopes,
            "revoked_at": token.revoked_at,
            "last_used_at": token.last_used_at,
            "created_at": token.created_at,
        }
        for token in tokens
    ]


@router.post("/settings/tokens")
def create_token(
    payload: TokenCreate,
    principal: dict = Depends(require_session_write),
    db: Session = Depends(get_db),
):
    raw = "bkm_" + secrets.token_urlsafe(32)
    token = ApiToken(name=payload.name, token_prefix=raw[:12], token_hash=hash_token(raw), scopes=payload.scopes)
    db.add(token)
    audit(db, "token_created", "创建远程 API Token", actor=principal["id"], token_name=payload.name)
    db.commit()
    return {"id": token.id, "token": raw, "prefix": token.token_prefix, "name": token.name}


@router.delete("/settings/tokens/{token_id}")
def revoke_token(
    token_id: str,
    principal: dict = Depends(require_session_write),
    db: Session = Depends(get_db),
):
    token = db.get(ApiToken, token_id)
    if not token:
        raise HTTPException(status_code=404, detail="Token 不存在")
    token.revoked_at = utcnow()
    audit(db, "token_revoked", "撤销远程 API Token", actor=principal["id"], token_id=token_id)
    db.commit()
    return {"ok": True}
