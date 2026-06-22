import smtplib
from email.message import EmailMessage

from .config import get_settings


def _smtp_sender() -> str:
    settings = get_settings()
    return settings.smtp_from or settings.smtp_username


def smtp_configured() -> bool:
    settings = get_settings()
    return bool(settings.smtp_host and settings.smtp_port and _smtp_sender())


def send_verification_code(to_email: str, code: str, purpose: str) -> None:
    settings = get_settings()
    sender = _smtp_sender()
    if not smtp_configured():
        raise RuntimeError("邮件服务未配置")

    purpose_text = "登录" if purpose == "login" else "重置密码"
    message = EmailMessage()
    message["Subject"] = f"收藏导航{purpose_text}验证码"
    message["From"] = sender
    message["To"] = to_email
    message.set_content(
        f"你的收藏导航{purpose_text}验证码是：{code}\n\n"
        f"验证码 {settings.email_code_ttl_minutes} 分钟内有效。如果不是你本人操作，请忽略这封邮件。"
    )

    if settings.smtp_use_ssl:
        client = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds)
    else:
        client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds)
    try:
        client.ehlo()
        if settings.smtp_use_tls and not settings.smtp_use_ssl:
            client.starttls()
            client.ehlo()
        if settings.smtp_username or settings.smtp_password:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)
    finally:
        client.quit()
