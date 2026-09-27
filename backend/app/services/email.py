import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.config import settings
from app.services.reminder import render_template

logger = logging.getLogger(__name__)


def build_email(subject_template: str, body_template: str, customer: dict) -> dict:
    """渲染邮件主题与正文（占位 {{customer_name}} 等）。纯函数。"""
    return {
        "to": customer.get("email") or "",
        "subject": render_template(subject_template, customer),
        "body": render_template(body_template, customer),
    }


def _send_sync(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if settings.SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30)
    else:
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30)
        server.starttls()
    try:
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
    finally:
        server.quit()


async def send_email(to: str, subject: str, body: str) -> None:
    """SMTP 未配置时抛 RuntimeError('SMTP 未配置')，由调用方降级处理。"""
    if not settings.SMTP_HOST:
        raise RuntimeError("SMTP 未配置")
    await asyncio.to_thread(_send_sync, to, subject, body)
