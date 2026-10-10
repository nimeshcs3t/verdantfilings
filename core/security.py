"""Security log, device detection, new-device alerts and the leaked-password check."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import timedelta

from sqlalchemy import and_, delete, func, insert, select

from .db import get_engine, security_events, utcnow

log = logging.getLogger(__name__)

LABELS = {"login": "Signed in", "login_failed": "Failed sign-in", "login_locked": "Sign-in blocked (too many tries)",
          "2fa_failed": "Wrong two-factor code", "2fa_on": "Two-factor sign-in turned on",
          "2fa_off": "Two-factor sign-in turned off", "backup_code_used": "Backup code used",
          "backup_codes_new": "New backup codes made", "password_changed": "Password changed",
          "reset_requested": "Password reset requested", "password_reset": "Password reset by email link",
          "session_ended": "Signed out a device", "sessions_ended": "Signed out everywhere",
          "share_created": "Share link created", "share_removed": "Share link removed",
          "account_deleted": "Account deleted", "admin_change": "Changed by admin", "admin_2fa_reset": "Two-factor reset by admin",
          "new_device": "Sign-in from a new device"}


def client() -> tuple[str | None, str | None]:
    """(ip, device) for the current browser, when Streamlit can see them."""
    ip = device = None
    try:
        import streamlit as st
        headers = st.context.headers or {}
        forwarded = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for") or ""
        ip = (forwarded.split(",")[0].strip() if isinstance(forwarded, str) else "") or getattr(st.context, "ip_address", None)
        ua = headers.get("User-Agent") or headers.get("user-agent") or ""
        device = describe(ua) if isinstance(ua, str) else None
    except Exception:
        pass
    ip = ip if isinstance(ip, str) and ip else None
    device = device if isinstance(device, str) and device else None
    return ip, device


def describe(ua: str) -> str | None:
    """'Chrome on Windows' from a user-agent string."""
    if not ua:
        return None
    browser = next((name for pat, name in ((r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"SamsungBrowser", "Samsung Internet"),
                                           (r"Firefox/|FxiOS", "Firefox"), (r"Chrome/|CriOS", "Chrome"),
                                           (r"Safari/", "Safari")) if re.search(pat, ua)), "Browser")
    system = next((name for pat, name in ((r"iPhone", "iPhone"), (r"iPad", "iPad"), (r"Android", "Android"),
                                          (r"Windows", "Windows"), (r"Mac OS X|Macintosh", "Mac"), (r"CrOS", "ChromeOS"),
                                          (r"Linux", "Linux")) if re.search(pat, ua)), "unknown system")
    return f"{browser} on {system}"


def record(kind: str, user_id: int | None = None, username: str | None = None, detail: str = "",
           ip: str | None = None, device: str | None = None) -> None:
    if ip is None and device is None:
        ip, device = client()
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(security_events).values(user_id=user_id, username=(username or "")[:64] or None, kind=kind,
                                                        detail=(detail or "")[:300] or None, ip=(ip or "")[:64] or None,
                                                        device=(device or "")[:80] or None, ts=utcnow()))
    except Exception as exc:
        log.warning("security log failed: %s", exc)


def events(user_id: int | None = None, limit: int = 50, kinds: list[str] | None = None, since_hours: int | None = None) -> list[dict]:
    q = select(security_events).order_by(security_events.c.ts.desc()).limit(limit)
    if user_id is not None:
        q = q.where(security_events.c.user_id == user_id)
    if kinds:
        q = q.where(security_events.c.kind.in_(kinds))
    if since_hours:
        q = q.where(security_events.c.ts >= utcnow() - timedelta(hours=since_hours))
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(q).mappings()]


def failed_by_username(hours: int = 24) -> list[tuple[str, int]]:
    with get_engine().connect() as conn:
        return [tuple(r) for r in conn.execute(
            select(security_events.c.username, func.count()).where(
                security_events.c.kind.in_(["login_failed", "2fa_failed", "login_locked"]),
                security_events.c.ts >= utcnow() - timedelta(hours=hours))
            .group_by(security_events.c.username).order_by(func.count().desc()).limit(20))]


def cleanup(days: int = 365) -> int:
    with get_engine().begin() as conn:
        return conn.execute(delete(security_events).where(security_events.c.ts < utcnow() - timedelta(days=days))).rowcount or 0


def check_new_device(user: dict, ip: str | None, device: str | None) -> bool:
    """After a successful sign-in: if this browser and system haven't signed in to this account in 180 days (and the
    account has signed in before), tell the member by Telegram and email. Returns True when an alert went out."""
    if not device:
        return False
    since = utcnow() - timedelta(days=180)
    with get_engine().connect() as conn:
        before = conn.execute(select(func.count()).select_from(security_events).where(
            security_events.c.user_id == user["id"], security_events.c.kind == "login")).scalar_one()
        seen = conn.execute(select(func.count()).select_from(security_events).where(and_(
            security_events.c.user_id == user["id"], security_events.c.kind == "login",
            security_events.c.device == device, security_events.c.ts >= since))).scalar_one()
    # 'before' includes the sign-in just recorded, so a first-ever sign-in has before == 1
    if before <= 1 or seen > 1:
        return False
    record("new_device", user["id"], user["username"], device, ip, device)
    try:
        from services import deliver
        deliver.send(user["id"], "New sign-in to Verdant Filings",
                     f"<b>New sign-in to your account</b>\n{device}" + (f", from {ip}" if ip else "")
                     + f"\n{utcnow():%d %b %Y %H:%M} UTC\n\nIf this wasn't you, change your password now and use "
                     "<b>Sign out on all devices</b> on the Account page.", kind="security")
        return True
    except Exception as exc:
        log.warning("new-device alert failed: %s", exc)
        return False


def breached(password: str, timeout: float = 3.0) -> int:
    """How many times this password appears in known data breaches (Have I Been Pwned). Only the first 5 characters
    of the password's SHA-1 hash leave the app, never the password. Returns 0 if the service can't be reached."""
    try:
        import requests
        digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        r = requests.get(f"https://api.pwnedpasswords.com/range/{digest[:5]}", timeout=timeout,
                         headers={"Add-Padding": "true", "User-Agent": "VerdantFilings-password-check"})
        if r.status_code != 200:
            return 0
        for line in r.text.splitlines():
            suffix, _, count = line.partition(":")
            if suffix.strip() == digest[5:]:
                return int(count.strip() or 0)
    except Exception:
        return 0
    return 0
