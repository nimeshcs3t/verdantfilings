"""One place to send a member something: Telegram and/or email, according to their settings."""
from __future__ import annotations

import html as _html
import re

from core.auth import get_user

from . import emailer, settings, telegram


def telegram_to_html(text: str) -> str:
    """Telegram messages use a little HTML (<b>, <a>); turn line breaks into an email-friendly page."""
    body = text.replace("\n", "<br>")
    return (f'<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:15px;line-height:1.5;'
            f'color:#1B2430;max-width:640px">{body}</div>')


def send(user_id: int, subject: str, text: str, kind: str = "brief", attachment: tuple[str, bytes, str] | None = None) -> int:
    """kind: 'alert' (instant filing alerts) or 'brief' (digests, briefs, reports). Returns messages sent."""
    user = get_user(user_id)
    if not user or not user.get("is_active"):
        return 0
    prefs = settings.get(user_id)
    sent = 0
    if user.get("telegram_chat_id") and telegram.enabled():
        for chunk in _chunks(text):
            sent += telegram.send_message(user["telegram_chat_id"], chunk)
        if attachment:
            sent += telegram.send_document(user["telegram_chat_id"], attachment[0], attachment[1], caption=subject)
    wants_email = prefs.get("email_alerts") if kind == "alert" else prefs.get("email_briefs")
    if prefs.get("email") and wants_email and emailer.enabled():
        sent += emailer.send(prefs["email"], subject, telegram_to_html(text), _plain(text),
                             [attachment] if attachment else None)
    return sent


def _plain(text: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", text))


def _chunks(text: str, size: int = 3800) -> list[str]:
    out, chunk = [], ""
    for line in text.split("\n"):
        if len(chunk) + len(line) > size:
            out.append(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        out.append(chunk)
    return out
