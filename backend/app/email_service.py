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
    code_block = " ".join(code)
    message.set_content(
        f"你的收藏导航{purpose_text}验证码是：\n\n"
        f"{code}\n\n"
        f"请在 {settings.email_code_ttl_minutes} 分钟内使用。"
        f"如果不是你本人操作，请忽略这封邮件。"
    )
    message.add_alternative(
        f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:28px;background:#f5f7fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;color:#172033">
    <div style="max-width:520px;margin:auto;background:#fff;border:1px solid #e5e9f2;border-radius:18px;padding:28px;box-shadow:0 16px 40px rgba(31,41,70,.08)">
      <p style="margin:0 0 10px;color:#69748a;font-size:13px">收藏导航验证码</p>
      <h1 style="margin:0 0 18px;font-size:22px;color:#172033">{purpose_text}确认</h1>
      <p style="margin:0 0 14px;color:#4a5568;line-height:1.7">请使用下面的 6 位验证码完成{purpose_text}：</p>
      <div style="margin:20px 0;padding:18px 20px;border-radius:14px;background:#eef0ff;color:#4243c9;font-size:34px;font-weight:800;letter-spacing:.22em;text-align:center">{code_block}</div>
      <p style="margin:0;color:#69748a;line-height:1.7">验证码 {settings.email_code_ttl_minutes} 分钟内有效。如果不是你本人操作，请忽略这封邮件。</p>
    </div>
  </body>
</html>
""",
        subtype="html",
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
