"""One place to send a member something: Telegram and/or email, according to their settings."""
from __future__ import annotations

import html as _html
import re

from core.auth import get_user

from . import emailer, settings, telegram


EMAIL = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">
<style>
 body {{ margin:0; padding:0; background:#F2F5F3; }}
 .wrap {{ background:#F2F5F3; padding:24px 12px; }}
 .card {{ max-width:640px; margin:0 auto; background:#FFFFFF; border:1px solid #E3E7E5; border-radius:10px; overflow:hidden; }}
 .bar {{ background:#1F6B4F; color:#FFFFFF; padding:14px 22px; font:700 16px -apple-system,Segoe UI,Roboto,Arial,sans-serif; }}
 .body {{ padding:20px 22px; color:#1B2430; font:15px/1.55 -apple-system,Segoe UI,Roboto,Arial,sans-serif; }}
 .body a {{ color:#1F6B4F; }}
 .foot {{ padding:12px 22px 18px; color:#5F6B7A; font:12px -apple-system,Segoe UI,Roboto,Arial,sans-serif; }}
 @media (prefers-color-scheme: dark) {{
  body, .wrap {{ background:#0F1413 !important; }}
  .card {{ background:#141A18 !important; border-color:#2A322F !important; }}
  .bar {{ background:#15302A !important; color:#5DBE93 !important; }}
  .body {{ color:#E4E9E6 !important; }} .body a {{ color:#5DBE93 !important; }}
  .foot {{ color:#9AA6A0 !important; }}
 }}
 [data-ogsc] .card {{ background:#141A18 !important; }} [data-ogsc] .body {{ color:#E4E9E6 !important; }}
</style></head><body><div class="wrap"><div class="card"><div class="bar">Verdant Filings</div>
<div class="body">{body}</div><div class="foot">{foot}</div></div></div></body></html>"""


def telegram_to_html(text: str) -> str:
    """Telegram-style messages (<b>, <a>, line breaks) in the app's email design, light or dark to match the reader."""
    from core.config import get_secret
    app = (get_secret("APP_URL") or "").rstrip("/")
    foot = (f'<a href="{app}" style="color:inherit">Open the app</a> · ' if app else "") + "Change what you receive on the Account page."
    return EMAIL.format(body=text.replace("\n", "<br>"), foot=foot)


def send(user_id: int, subject: str, text: str, kind: str = "brief", attachment: tuple[str, bytes, str] | None = None) -> int:
    """kind: 'alert' (instant filing alerts), 'brief' (digests, briefs, reports) or 'security' (always sent, to every
    channel the member has). Returns messages sent."""
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
    wants_email = True if kind == "security" else prefs.get("email_alerts") if kind == "alert" else prefs.get("email_briefs")
    address = prefs.get("email") or (user.get("email") if kind == "security" else None)
    if address and wants_email and emailer.enabled():
        sent += emailer.send(address, subject, telegram_to_html(text), _plain(text),
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
