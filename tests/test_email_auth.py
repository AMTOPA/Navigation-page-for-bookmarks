from pathlib import Path

from sqlalchemy import delete, select

from app import auth_api
from app.access_control import Denylist
from app.db import SessionLocal
from app.models import AdminUser, EmailVerificationCode, UserSession
from app.security import hash_password, verify_password


ALLOWED_EMAIL = "3314982394@qq.com"


def clear_codes():
    with SessionLocal() as db:
        db.execute(delete(EmailVerificationCode))
        db.commit()


def test_email_code_rejects_unbound_email_without_sending(client, monkeypatch):
    clear_codes()
    sent = []
    monkeypatch.setattr(auth_api, "send_verification_code", lambda *args: sent.append(args))

    response = client.post(
        "/api/auth/email-code",
        json={"email": "someone@example.com", "purpose": "login"},
        headers={"X-Forwarded-For": "203.0.113.10"},
    )

    assert response.status_code == 400
    assert not sent
    with SessionLocal() as db:
        assert db.scalar(select(EmailVerificationCode)) is None


def test_email_code_login_and_rate_limit(client, monkeypatch):
    clear_codes()
    sent = []
    monkeypatch.setattr(auth_api, "send_verification_code", lambda email, code, purpose: sent.append((email, code, purpose)))

    response = client.post(
        "/api/auth/email-code",
        json={"email": ALLOWED_EMAIL, "purpose": "login"},
        headers={"X-Forwarded-For": "203.0.113.11"},
    )
    assert response.status_code == 200
    assert sent and sent[0][0] == ALLOWED_EMAIL and sent[0][2] == "login"
    code = sent[0][1]
    assert code.isdigit() and len(code) == 6

    second = client.post(
        "/api/auth/email-code",
        json={"email": ALLOWED_EMAIL, "purpose": "login"},
        headers={"X-Forwarded-For": "203.0.113.11"},
    )
    assert second.status_code == 429

    with SessionLocal() as db:
        record = db.scalar(select(EmailVerificationCode).where(EmailVerificationCode.email == ALLOWED_EMAIL))
        assert record is not None
        assert record.code_hash != code

    login = client.post(
        "/api/auth/email-login",
        json={"email": ALLOWED_EMAIL, "code": code},
        headers={"X-Forwarded-For": "203.0.113.11"},
    )
    assert login.status_code == 200
    assert login.json()["csrf_token"]


def test_password_reset_with_email_code_revokes_sessions(client, monkeypatch):
    clear_codes()
    sent = []
    monkeypatch.setattr(auth_api, "send_verification_code", lambda email, code, purpose: sent.append((email, code, purpose)))
    with SessionLocal() as db:
        user = db.scalar(select(AdminUser).where(AdminUser.username == "admin"))
        user.password_hash = hash_password("test-password")
        db.commit()

    auth = client.post("/api/auth/login", json={"username": "admin", "password": "test-password"})
    assert auth.status_code == 200

    request = client.post(
        "/api/auth/email-code",
        json={"email": ALLOWED_EMAIL, "purpose": "reset_password"},
        headers={"X-Forwarded-For": "203.0.113.12"},
    )
    assert request.status_code == 200
    reset = client.post(
        "/api/auth/password-reset",
        json={"email": ALLOWED_EMAIL, "code": sent[-1][1], "new_password": "new-test-password"},
        headers={"X-Forwarded-For": "203.0.113.12"},
    )
    assert reset.status_code == 200

    with SessionLocal() as db:
        user = db.scalar(select(AdminUser).where(AdminUser.username == "admin"))
        assert verify_password(user.password_hash, "new-test-password")
        assert db.scalar(select(UserSession).where(UserSession.user_id == user.id)) is None
        user.password_hash = hash_password("test-password")
        db.commit()


def test_denylist_supports_single_ip_and_cidr(tmp_path: Path):
    deny_file = tmp_path / "denylist.txt"
    deny_file.write_text("203.0.113.44\n198.51.100.0/24\n# comment\n", encoding="utf-8")
    denylist = Denylist(deny_file)

    assert denylist.contains("203.0.113.44")
    assert denylist.contains("198.51.100.99")
    assert not denylist.contains("192.0.2.1")
