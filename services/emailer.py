"""Email delivery over SMTP (e.g. a Gmail app password, Brevo or any mail provider). Optional: only used when the
SMTP_* secrets are set."""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage

from core.config import get_secret

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(get_secret("SMTP_HOST") and get_secret("SMTP_USER") and get_secret("SMTP_PASSWORD"))


def valid_address(address: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", (address or "").strip()))


def send(to: str, subject: str, html: str, text: str = "", attachments: list[tuple[str, bytes, str]] | None = None) -> bool:
    """attachments: (filename, bytes, mime type such as 'application/pdf')."""
    if not enabled() or not valid_address(to):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject[:200]
    msg["From"] = get_secret("SMTP_FROM") or get_secret("SMTP_USER")
    msg["To"] = to.strip()
    msg.set_content(text or re.sub(r"<[^>]+>", "", html.replace("<br>", "\n")))
    msg.add_alternative(html, subtype="html")
    for name, data, mime in attachments or []:
        main, sub = mime.split("/", 1)
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    host, port = get_secret("SMTP_HOST"), int(get_secret("SMTP_PORT", 587) or 587)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
                s.login(get_secret("SMTP_USER"), get_secret("SMTP_PASSWORD"))
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(get_secret("SMTP_USER"), get_secret("SMTP_PASSWORD"))
                s.send_message(msg)
        return True
    except Exception as exc:
        log.warning("email to %s failed: %s", to, exc)
        return False
