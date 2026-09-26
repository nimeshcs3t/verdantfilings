"""Telegram alerts via the Bot API."""
from __future__ import annotations

import html
from functools import lru_cache

import requests

from core.config import get_secret


def _token() -> str | None:
    return get_secret("TELEGRAM_BOT_TOKEN")


def enabled() -> bool:
    return bool(_token())


def _call(method: str, **params) -> dict:
    r = requests.post(f"https://api.telegram.org/bot{_token()}/{method}", json=params, timeout=20)
    return r.json()


def send_message(chat_id: str, text: str) -> bool:
    try:
        return bool(_call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                          link_preview_options={"is_disabled": True}).get("ok"))
    except Exception:
        return False


@lru_cache(maxsize=1)
def bot_username() -> str | None:
    configured = get_secret("TELEGRAM_BOT_USERNAME")
    if configured:
        return configured.lstrip("@")
    try:
        return _call("getMe").get("result", {}).get("username")
    except Exception:
        return None


def find_chat_for_code(code: str) -> str | None:
    """User pressed Start on t.me/<bot>?start=<code>; find their chat id."""
    try:
        updates = _call("getUpdates", limit=100, allowed_updates=["message"]).get("result", [])
    except Exception:
        return None
    for update in reversed(updates):
        msg = update.get("message") or {}
        if (msg.get("text") or "").strip() == f"/start {code}":
            return str(msg["chat"]["id"])
    return None


def format_filing(row: dict) -> str:
    esc = lambda s: html.escape(str(s or ""), quote=False)
    lines = [f"<b>{esc(row['company_name'])}</b>  {esc(row['ticker'])}",
             esc(row["title_en"])]
    if row.get("title_local") and row["title_local"] != row["title_en"]:
        lines.append(f"<i>{esc(row['title_local'])}</i>")
    if row.get("summary_en"):
        lines += ["", esc(row["summary_en"])[:1500]]
    links = f'<a href="{html.escape(row["url"])}">Original filing</a>'
    app_url = get_secret("APP_URL")
    if app_url:
        links += f'   <a href="{html.escape(app_url)}">Open app</a>'
    lines += ["", links]
    try:
        from sources import get_source
        credit = getattr(get_source(row["market"]), "attribution", "")
    except Exception:
        credit = ""
    if credit:
        lines.append(f"<i>{esc(credit)}</i>")
    return "\n".join(lines)
