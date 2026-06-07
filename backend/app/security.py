import base64
import hashlib
import secrets
from datetime import timedelta, timezone

from argon2 import PasswordHasher
from cryptography.fernet import Fernet
from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AdminUser, ApiToken, UserSession, utcnow


password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except Exception:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def get_fernet() -> Fernet:
    settings = get_settings()
    if settings.encryption_key:
        return Fernet(settings.encryption_key.encode("ascii"))
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_value(value: str) -> str:
    return get_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_value(value: str) -> str:
    return get_fernet().decrypt(value.encode("ascii")).decode("utf-8")


def create_session(db: Session, user: AdminUser) -> tuple[str, str]:
    settings = get_settings()
    session_id = new_token()
    csrf_token = new_token()
    db.add(
        UserSession(
            id=hash_token(session_id),
            user_id=user.id,
            csrf_token=csrf_token,
            expires_at=utcnow() + timedelta(days=settings.session_days),
        )
    )
    db.commit()
    return session_id, csrf_token


def get_session(
    db: Session = Depends(get_db),
    session_cookie: str | None = Cookie(default=None, alias="bookmark_session"),
) -> UserSession | None:
    if not session_cookie:
        return None
    session = db.get(UserSession, hash_token(session_cookie))
    if not session:
        return None
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < utcnow():
        return None
    return session


def get_current_principal(
    request: Request,
    db: Session = Depends(get_db),
    session: UserSession | None = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> dict:
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization.split(" ", 1)[1].strip()
        token = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(raw_token)))
        if token and not token.revoked_at:
            token.last_used_at = utcnow()
            db.commit()
            return {"kind": "token", "id": token.id, "scopes": token.scopes}
    if session:
        return {"kind": "session", "id": session.user_id, "csrf": session.csrf_token}
    raise HTTPException(401, "需要登录")


def require_write(
    request: Request,
    principal: dict = Depends(get_current_principal),
    x_csrf_token: str | None = Header(default=None),
) -> dict:
    if principal["kind"] == "session":
        if not x_csrf_token or not secrets.compare_digest(x_csrf_token, principal["csrf"]):
            raise HTTPException(403, "CSRF 校验失败")
    elif "bookmarks:write" not in principal.get("scopes", []):
        raise HTTPException(403, "Token 权限不足")
    return principal


def require_read(principal: dict = Depends(get_current_principal)) -> dict:
    if principal["kind"] == "token" and "bookmarks:read" not in principal.get("scopes", []):
        raise HTTPException(403, "Token 权限不足")
    return principal


def require_sync(principal: dict = Depends(get_current_principal)) -> dict:
    if principal["kind"] != "token" or "sync" not in principal.get("scopes", []):
        raise HTTPException(403, "Token 权限不足")
    return principal


def require_session(principal: dict = Depends(get_current_principal)) -> dict:
    if principal["kind"] != "session":
        raise HTTPException(403, "仅管理员会话可执行此操作")
    return principal


def require_session_write(principal: dict = Depends(require_write)) -> dict:
    if principal["kind"] != "session":
        raise HTTPException(403, "仅管理员会话可执行此操作")
    return principal
