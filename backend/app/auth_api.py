import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .access_control import client_ip
from .config import get_settings
from .db import get_db
from .email_service import send_verification_code
from .models import AdminUser, EmailVerificationCode, UserSession, utcnow
from .schemas import EmailCodeRequest, EmailLoginRequest, PasswordResetConfirm
from .security import create_session, hash_password
from .services import audit, aware_utc


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _allowed_email(email: str) -> bool:
    return _normalize_email(email) == get_settings().allowed_login_email.strip().lower()


def _hash_code(email: str, purpose: str, code: str) -> str:
    settings = get_settings()
    value = f"{settings.secret_key}:{purpose}:{_normalize_email(email)}:{code}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _generic_error(status_code: int = 400) -> HTTPException:
    return HTTPException(status_code, "请求异常，请稍后再试")


def _first_admin(db: Session) -> AdminUser | None:
    return db.scalar(select(AdminUser).order_by(AdminUser.created_at.asc()))


def _verify_code(db: Session, email: str, purpose: str, code: str) -> EmailVerificationCode:
    now = utcnow()
    record = db.scalar(
        select(EmailVerificationCode)
        .where(
            EmailVerificationCode.email == _normalize_email(email),
            EmailVerificationCode.purpose == purpose,
            EmailVerificationCode.used_at.is_(None),
        )
        .order_by(EmailVerificationCode.created_at.desc())
    )
    if not record or aware_utc(record.expires_at) < now:
        raise HTTPException(400, "验证码无效或已过期")
    if record.attempts >= 5:
        raise HTTPException(429, "验证码尝试次数过多，请重新获取")
    record.attempts += 1
    if not secrets.compare_digest(record.code_hash, _hash_code(email, purpose, code.strip())):
        db.commit()
        raise HTTPException(400, "验证码无效或已过期")
    record.used_at = now
    return record


@router.post("/email-code")
def request_email_code(payload: EmailCodeRequest, request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    email = _normalize_email(payload.email)
    ip = client_ip(request)
    if not _allowed_email(email):
        audit(db, "email_code_rejected", "邮箱验证码请求异常", actor=email, level="warning", ip=ip)
        db.commit()
        raise _generic_error()

    cutoff = utcnow() - timedelta(seconds=settings.email_code_ip_cooldown_seconds)
    recent = db.scalar(
        select(EmailVerificationCode)
        .where(
            EmailVerificationCode.ip == ip,
            EmailVerificationCode.purpose == payload.purpose,
            EmailVerificationCode.created_at >= cutoff,
        )
        .order_by(EmailVerificationCode.created_at.desc())
    )
    if recent:
        raise HTTPException(429, "获取过于频繁，请一分钟后再试")

    code = f"{secrets.randbelow(1_000_000):06d}"
    record = EmailVerificationCode(
        email=email,
        purpose=payload.purpose,
        code_hash=_hash_code(email, payload.purpose, code),
        ip=ip,
        expires_at=utcnow() + timedelta(minutes=settings.email_code_ttl_minutes),
    )
    db.add(record)
    try:
        send_verification_code(email, code, payload.purpose)
    except Exception as exc:
        db.rollback()
        audit(db, "email_code_failed", "邮箱验证码发送失败", actor=email, level="error", ip=ip, error=str(exc)[:200])
        db.commit()
        raise HTTPException(503, "邮件发送失败，请稍后再试") from exc

    audit(db, "email_code_sent", "邮箱验证码已发送", actor=email, ip=ip, purpose=payload.purpose)
    db.commit()
    return {"ok": True, "cooldown_seconds": settings.email_code_ip_cooldown_seconds}


@router.post("/email-login")
def email_login(payload: EmailLoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    email = _normalize_email(payload.email)
    ip = client_ip(request)
    if not _allowed_email(email):
        audit(db, "email_login_rejected", "邮箱登录请求异常", actor=email, level="warning", ip=ip)
        db.commit()
        raise _generic_error(401)
    _verify_code(db, email, "login", payload.code)
    user = _first_admin(db)
    if not user:
        raise HTTPException(500, "管理员账号尚未初始化")
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
    audit(db, "email_login_success", "邮箱验证码登录成功", actor=user.username, ip=ip)
    db.commit()
    return {"csrf_token": csrf, "username": user.username}


@router.post("/password-reset")
def password_reset(payload: PasswordResetConfirm, request: Request, db: Session = Depends(get_db)):
    email = _normalize_email(payload.email)
    ip = client_ip(request)
    if not _allowed_email(email):
        audit(db, "password_reset_rejected", "密码重置请求异常", actor=email, level="warning", ip=ip)
        db.commit()
        raise _generic_error(401)
    _verify_code(db, email, "reset_password", payload.code)
    user = _first_admin(db)
    if not user:
        raise HTTPException(500, "管理员账号尚未初始化")
    user.password_hash = hash_password(payload.new_password)
    user.failed_attempts = 0
    user.locked_until = None
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    audit(db, "password_reset", "通过邮箱验证码重置管理员密码", actor=user.username, ip=ip)
    db.commit()
    return {"ok": True}
