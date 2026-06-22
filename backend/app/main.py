import hashlib
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .access_control import DenylistMiddleware, client_ip
from .auth_api import router as auth_router
from .config import get_settings
from .db import Base, SessionLocal, engine, get_db
from .extra_api import router as extra_router
from .models import AIModel, AIRoute, AdminUser, Bookmark, BookmarkSource, Setting, UserSession, utcnow
from .schemas import BatchRequest, BookmarkCreate, BookmarkOut, BookmarkUpdate, LoginRequest
from .search_api import router as search_router
from .security import (
    create_session,
    get_current_principal,
    get_session,
    hash_password,
    require_read,
    require_write,
    verify_password,
)
from .services import (
    aware_utc,
    audit,
    bookmark_compact_dict,
    bookmark_to_dict,
    create_job_batch,
    get_or_create_bookmark,
    normalize_url,
    queue_job,
    upsert_source,
)
from .settings_api import router as settings_router


settings = get_settings()


def bootstrap():
    if settings.secure_cookies and settings.secret_key == "change-me-before-production":
        raise RuntimeError("生产环境必须配置 SECRET_KEY")
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        user = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_username))
        if not user:
            if not settings.admin_password or settings.admin_password == "change-me":
                raise RuntimeError("首次启动必须配置 ADMIN_PASSWORD，创建管理员后可从环境变量删除")
            db.add(AdminUser(username=settings.admin_username, password_hash=hash_password(settings.admin_password)))
            db.flush()
        if not db.get(Setting, "phase2_initial_jobs_queued"):
            batch = create_job_batch(db, "初始化搜索与图片缓存", "phase2_init", "system")
            for bookmark in db.scalars(select(Bookmark).where(Bookmark.deleted_at.is_(None))).all():
                queue_job(db, bookmark.id, "search_index", batch_id=batch.id)
                if bookmark.favicon or bookmark.image:
                    queue_job(db, bookmark.id, "media_cache", batch_id=batch.id)
            db.add(Setting(key="phase2_initial_jobs_queued", value="1"))
        if not db.get(AIRoute, "ai_search"):
            preferred = db.scalar(
                select(AIModel).where(
                    AIModel.enabled.is_(True),
                    AIModel.model_name == "deepseek-v4-flash",
                )
            )
            default_route = db.get(AIRoute, "default")
            db.add(
                AIRoute(
                    task_type="ai_search",
                    model_id=preferred.id if preferred else (default_route.model_id if default_route else None),
                )
            )
        db.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.add_middleware(DenylistMiddleware)
app.include_router(extra_router)
app.include_router(settings_router)
app.include_router(search_router)
app.include_router(auth_router)
login_attempts: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def add_server_timing(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    duration = (time.perf_counter() - started) * 1000
    response.headers["Server-Timing"] = f"app;dur={duration:.1f}"
    return response


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    now = time.time()
    attempts = login_attempts[ip]
    while attempts and now - attempts[0] > 900:
        attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(429, "登录尝试过多，请稍后再试")
    user = db.scalar(select(AdminUser).where(AdminUser.username == payload.username))
    invalid = (
        not user
        or bool(user.locked_until and aware_utc(user.locked_until) > utcnow())
        or not verify_password(user.password_hash, payload.password)
    )
    if invalid:
        attempts.append(now)
        if user:
            user.failed_attempts += 1
            if user.failed_attempts >= 5:
                user.locked_until = utcnow() + timedelta(minutes=15)
            audit(db, "login_failed", "管理员登录失败", actor=payload.username, level="warning", ip=ip)
            db.commit()
        raise HTTPException(401, "用户名或密码错误")
    user.failed_attempts = 0
    user.locked_until = None
    session_id, csrf = create_session(db, user)
    response.set_cookie(
        "bookmark_session",
        session_id,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=settings.session_days * 86400,
        path="/",
    )
    login_attempts.pop(ip, None)
    audit(db, "login_success", "管理员登录成功", actor=user.username, ip=ip)
    db.commit()
    return {"csrf_token": csrf, "username": user.username}


@app.post("/api/auth/logout")
def logout(
    response: Response,
    session: UserSession | None = Depends(get_session),
    _principal: dict = Depends(require_write),
    db: Session = Depends(get_db),
):
    if session:
        db.delete(session)
        db.commit()
    response.delete_cookie("bookmark_session", path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(principal: dict = Depends(get_current_principal), db: Session = Depends(get_db)):
    user = db.get(AdminUser, principal["id"]) if principal["kind"] == "session" else None
    return {
        "authenticated": True,
        "kind": principal["kind"],
        "username": user.username if user else "",
        "csrf_token": principal.get("csrf", ""),
    }


@app.get("/api/bookmarks")
def list_bookmarks(
    q: str = "",
    include_deleted: bool = False,
    limit: int = Query(default=1000, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    compact: bool = False,
    _principal: dict = Depends(require_read),
    db: Session = Depends(get_db),
):
    stmt = select(Bookmark).options(selectinload(Bookmark.sources), selectinload(Bookmark.link_health))
    if not include_deleted:
        stmt = stmt.where(Bookmark.deleted_at.is_(None))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Bookmark.title.ilike(pattern),
                Bookmark.url.ilike(pattern),
                Bookmark.description.ilike(pattern),
                Bookmark.manual_category.ilike(pattern),
                Bookmark.ai_category.ilike(pattern),
            )
        )
    if page is not None or page_size is not None:
        page = page or 1
        page_size = page_size or 20
        count_stmt = select(func.count(Bookmark.id))
        if not include_deleted:
            count_stmt = count_stmt.where(Bookmark.deleted_at.is_(None))
        if q:
            count_stmt = count_stmt.where(
                or_(
                    Bookmark.title.ilike(pattern),
                    Bookmark.url.ilike(pattern),
                    Bookmark.description.ilike(pattern),
                    Bookmark.manual_category.ilike(pattern),
                    Bookmark.ai_category.ilike(pattern),
                )
            )
        total = db.scalar(count_stmt) or 0
        bookmarks = db.scalars(
            stmt.order_by(Bookmark.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        serializer = bookmark_compact_dict if compact else bookmark_to_dict
        return {
            "items": [serializer(bookmark) for bookmark in bookmarks],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max((total + page_size - 1) // page_size, 1),
        }
    bookmarks = db.scalars(stmt.order_by(Bookmark.updated_at.desc()).offset(offset).limit(limit)).all()
    serializer = bookmark_compact_dict if compact else bookmark_to_dict
    return [serializer(bookmark) for bookmark in bookmarks]


@app.get("/api/navigation/snapshot")
def navigation_snapshot(
    request: Request,
    _principal: dict = Depends(require_read),
    db: Session = Depends(get_db),
):
    count = db.scalar(select(func.count(Bookmark.id)).where(Bookmark.deleted_at.is_(None))) or 0
    bookmark_updated = db.scalar(select(func.max(Bookmark.updated_at)).where(Bookmark.deleted_at.is_(None)))
    source_updated = db.scalar(
        select(func.max(BookmarkSource.updated_at))
        .join(Bookmark, Bookmark.id == BookmarkSource.bookmark_id)
        .where(Bookmark.deleted_at.is_(None), BookmarkSource.is_active.is_(True))
    )
    revision_source = f"{count}:{bookmark_updated}:{source_updated}"
    etag = '"' + hashlib.sha256(revision_source.encode("utf-8")).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    bookmarks = db.scalars(
        select(Bookmark)
        .where(Bookmark.deleted_at.is_(None))
        .options(selectinload(Bookmark.sources), selectinload(Bookmark.link_health))
        .order_by(Bookmark.updated_at.desc())
    ).all()
    response = JSONResponse(
        jsonable_encoder(
            {
                "revision": etag.strip('"'),
                "count": count,
                "items": [bookmark_compact_dict(item) for item in bookmarks],
            }
        )
    )
    response.headers.update(headers)
    return response


@app.get("/api/bookmarks/{bookmark_id}", response_model=BookmarkOut)
def get_bookmark(
    bookmark_id: str,
    _principal: dict = Depends(require_read),
    db: Session = Depends(get_db),
):
    bookmark = db.scalar(
        select(Bookmark).where(Bookmark.id == bookmark_id).options(selectinload(Bookmark.sources))
    )
    if not bookmark:
        raise HTTPException(404, "收藏不存在")
    return bookmark_to_dict(bookmark)


@app.post("/api/bookmarks", response_model=BookmarkOut)
def create_bookmark(
    payload: BookmarkCreate,
    principal: dict = Depends(require_write),
    db: Session = Depends(get_db),
):
    bookmark, created = get_or_create_bookmark(db, payload.url, payload.title)
    overrides = set(bookmark.manual_override_fields or [])
    for field, value in {
        "title": payload.title,
        "description": payload.description,
        "favicon": payload.favicon,
        "image": payload.image,
        "tags": payload.tags,
        "notes": payload.notes,
    }.items():
        if value:
            setattr(bookmark, field, value)
            overrides.add(field)
    if payload.category:
        bookmark.manual_category = payload.category
        overrides.add("category")
    if payload.importance != "normal":
        bookmark.manual_importance = payload.importance
        overrides.add("importance")
    bookmark.manual_override_fields = sorted(overrides)
    if created:
        upsert_source(db, bookmark, "manual", bookmark.id, title=bookmark.title, url=bookmark.url)
    queue_job(db, bookmark.id, "crawl")
    queue_job(db, bookmark.id, "ai")
    queue_job(db, bookmark.id, "search_index")
    audit(db, "bookmark_created", "新增收藏", actor=principal["id"], bookmark_id=bookmark.id)
    db.commit()
    db.refresh(bookmark)
    return bookmark_to_dict(bookmark)


@app.patch("/api/bookmarks/{bookmark_id}", response_model=BookmarkOut)
def update_bookmark(
    bookmark_id: str,
    payload: BookmarkUpdate,
    principal: dict = Depends(require_write),
    db: Session = Depends(get_db),
):
    bookmark = db.scalar(select(Bookmark).where(Bookmark.id == bookmark_id).options(selectinload(Bookmark.sources)))
    if not bookmark:
        raise HTTPException(404, "收藏不存在")
    overrides = set(bookmark.manual_override_fields or [])
    overrides.difference_update(payload.unlock_fields)
    values = payload.model_dump(exclude_unset=True, exclude={"unlock_fields"})
    if "url" in values and values["url"]:
        normalized = normalize_url(values["url"])
        duplicate = db.scalar(select(Bookmark).where(Bookmark.normalized_url == normalized, Bookmark.id != bookmark.id))
        if duplicate:
            raise HTTPException(409, "该 URL 已存在")
        bookmark.url = values.pop("url")
        bookmark.normalized_url = normalized
        bookmark.is_crawled = False
        bookmark.is_ai_classified = False
        bookmark.content_hash = ""
        bookmark.etag = ""
        bookmark.last_modified = ""
        bookmark.summary = ""
        bookmark.outline = []
        bookmark.keywords = []
        bookmark.key_info = {}
        bookmark.favicon_cache_url = ""
        bookmark.image_cache_url = ""
        overrides.add("url")
        queue_job(db, bookmark.id, "crawl", force=True)
        queue_job(db, bookmark.id, "ai", force=True)
    if "category" in values:
        bookmark.manual_category = values.pop("category") or ""
        overrides.add("category")
    if "importance" in values:
        bookmark.manual_importance = values.pop("importance") or ""
        overrides.add("importance")
    for field, value in values.items():
        setattr(bookmark, field, value)
        overrides.add(field)
        if field in {"favicon", "image"}:
            setattr(bookmark, f"{field}_cache_url", "")
            queue_job(db, bookmark.id, "media_cache", force=True)
    bookmark.manual_override_fields = sorted(overrides)
    queue_job(db, bookmark.id, "search_index", force=True)
    audit(db, "bookmark_updated", "修改收藏", actor=principal["id"], bookmark_id=bookmark.id)
    db.commit()
    return bookmark_to_dict(bookmark)


@app.delete("/api/bookmarks/{bookmark_id}")
def delete_bookmark(
    bookmark_id: str,
    principal: dict = Depends(require_write),
    db: Session = Depends(get_db),
):
    bookmark = db.get(Bookmark, bookmark_id)
    if not bookmark:
        raise HTTPException(404, "收藏不存在")
    bookmark.deleted_at = utcnow()
    queue_job(db, bookmark.id, "search_index", force=True)
    audit(db, "bookmark_deleted", "软删除收藏", actor=principal["id"], bookmark_id=bookmark.id)
    db.commit()
    return {"ok": True}


@app.post("/api/bookmarks/{bookmark_id}/restore")
def restore_bookmark(
    bookmark_id: str,
    principal: dict = Depends(require_write),
    db: Session = Depends(get_db),
):
    bookmark = db.get(Bookmark, bookmark_id)
    if not bookmark:
        raise HTTPException(404, "收藏不存在")
    bookmark.deleted_at = None
    queue_job(db, bookmark.id, "search_index", force=True)
    audit(db, "bookmark_restored", "恢复收藏", actor=principal["id"], bookmark_id=bookmark.id)
    db.commit()
    return {"ok": True}


@app.post("/api/bookmarks/batch")
def batch_bookmarks(
    payload: BatchRequest,
    principal: dict = Depends(require_write),
    db: Session = Depends(get_db),
):
    if payload.action == "delete" and not payload.confirm:
        raise HTTPException(400, "批量删除需要明确确认")
    bookmarks = db.scalars(select(Bookmark).where(Bookmark.id.in_(payload.ids))).all()
    batch = None
    if payload.action in {"crawl", "ai", "search_index", "health_check"}:
        labels = {
            "crawl": "批量抓取",
            "ai": "批量 AI 处理",
            "search_index": "批量更新搜索索引",
            "health_check": "批量检查链接",
        }
        batch = create_job_batch(db, labels[payload.action], payload.action, principal["id"])
    for bookmark in bookmarks:
        if payload.action == "delete":
            bookmark.deleted_at = utcnow()
            queue_job(db, bookmark.id, "search_index", force=True)
        elif payload.action == "restore":
            bookmark.deleted_at = None
            queue_job(db, bookmark.id, "search_index", force=True)
        else:
            queue_job(db, bookmark.id, payload.action, force=True, batch_id=batch.id)
    audit(db, "bookmark_batch", f"批量操作：{payload.action}", actor=principal["id"], count=len(bookmarks))
    db.commit()
    return {"ok": True, "count": len(bookmarks), "batch_id": batch.id if batch else None}


frontend = settings.frontend_dir
if frontend.exists():
    app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")


@app.get("/")
def root(session: UserSession | None = Depends(get_session)):
    if not session:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(frontend / "index.html")


@app.get("/login")
def login_page():
    return FileResponse(frontend / "login.html")


@app.get("/admin")
def admin_page(session: UserSession | None = Depends(get_session)):
    if not session:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(frontend / "admin.html")
