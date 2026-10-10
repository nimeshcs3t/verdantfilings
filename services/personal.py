"""Per-member features: starred filings, private notes, alert preferences, keyword alerts, remember-me tokens."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.db import as_utc, get_engine, keywords, notes, sessions, stars, user_prefs, utcnow, watch_prefs

DEFAULT_PREFS = {"alert_mode": "instant", "digest_hour": 18, "tz": "UTC", "skip_insider": False, "weekly": True,
                 "last_digest": None, "last_weekly": None}
REMEMBER_DAYS = 30


# ---- stars ------------------------------------------------------------------------------
def starred(user_id: int) -> set[str]:
    with get_engine().connect() as conn:
        return set(conn.execute(select(stars.c.uid).where(stars.c.user_id == user_id)).scalars())


def toggle_star(user_id: int, uid: str) -> bool:
    """Returns True if the filing is now starred."""
    with get_engine().begin() as conn:
        gone = conn.execute(delete(stars).where(stars.c.user_id == user_id, stars.c.uid == uid)).rowcount
        if gone:
            return False
        conn.execute(insert(stars).values(user_id=user_id, uid=uid, created_at=utcnow()))
        return True


# ---- notes ------------------------------------------------------------------------------
def get_note(user_id: int, market: str, ticker: str) -> str:
    with get_engine().connect() as conn:
        return conn.execute(select(notes.c.body).where(notes.c.user_id == user_id, notes.c.market == market,
                                                       notes.c.ticker == ticker)).scalar() or ""


def save_note(user_id: int, market: str, ticker: str, body: str) -> None:
    body = (body or "").strip()[:5000]
    with get_engine().begin() as conn:
        conn.execute(delete(notes).where(notes.c.user_id == user_id, notes.c.market == market, notes.c.ticker == ticker))
        if body:
            conn.execute(insert(notes).values(user_id=user_id, market=market, ticker=ticker, body=body,
                                              updated_at=utcnow()))


# ---- alert preferences ------------------------------------------------------------------
def get_prefs(user_id: int) -> dict:
    with get_engine().connect() as conn:
        row = conn.execute(select(user_prefs).where(user_prefs.c.user_id == user_id)).mappings().first()
    return {**DEFAULT_PREFS, **(dict(row) if row else {}), "user_id": user_id}


def save_prefs(user_id: int, **values) -> None:
    allowed = {k: v for k, v in values.items() if k in DEFAULT_PREFS}
    with get_engine().begin() as conn:
        exists = conn.execute(select(user_prefs.c.user_id).where(user_prefs.c.user_id == user_id)).first()
        if exists:
            conn.execute(update(user_prefs).where(user_prefs.c.user_id == user_id).values(**allowed))
        else:
            conn.execute(insert(user_prefs).values(user_id=user_id, **{**DEFAULT_PREFS, **allowed}))


def levels(user_id: int) -> dict[tuple[str, str], str]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(watch_prefs).where(watch_prefs.c.user_id == user_id)).mappings().all()
    return {(r["market"], r["ticker"]): r["level"] for r in rows}


def set_level(user_id: int, market: str, ticker: str, level: str) -> None:
    level = level if level in ("all", "major") else "all"
    with get_engine().begin() as conn:
        conn.execute(delete(watch_prefs).where(watch_prefs.c.user_id == user_id, watch_prefs.c.market == market,
                                               watch_prefs.c.ticker == ticker))
        conn.execute(insert(watch_prefs).values(user_id=user_id, market=market, ticker=ticker, level=level))


# ---- keyword alerts ---------------------------------------------------------------------
def list_keywords(user_id: int) -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(select(keywords).where(keywords.c.user_id == user_id)
                                              .order_by(keywords.c.keyword)).mappings()]


def add_keyword(user_id: int, keyword: str, market: str = "*") -> str | None:
    word = " ".join((keyword or "").split())[:80]
    if len(word) < 3:
        return "Use at least 3 characters."
    if len(list_keywords(user_id)) >= 30:
        return "You can have up to 30 keywords."
    with get_engine().begin() as conn:
        conn.execute(insert(keywords).values(user_id=user_id, market=market or "*", keyword=word, created_at=utcnow()))
    return None


def remove_keyword(user_id: int, keyword_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(keywords).where(keywords.c.id == keyword_id, keywords.c.user_id == user_id))


# ---- remember me ------------------------------------------------------------------------
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


SESSION_HOURS = 12          # sessions without "keep me signed in" end after 12 hours without activity


def create_session(user_id: int, remember: bool = False, device: str | None = None, ip: str | None = None) -> str:
    """Every sign-in gets a session row, so members can see and end their sessions from any device."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    life = timedelta(days=REMEMBER_DAYS) if remember else timedelta(hours=SESSION_HOURS)
    with get_engine().begin() as conn:
        conn.execute(delete(sessions).where(sessions.c.expires_at < now))
        conn.execute(insert(sessions).values(token_hash=_hash(token), user_id=user_id, expires_at=now + life,
                                             created_at=now, last_seen=now, device=(device or "")[:80] or None,
                                             ip=(ip or "")[:64] or None, remember=bool(remember)))
    return token


def session_user(token: str | None) -> int | None:
    if not isinstance(token, str) or not token or len(token) > 100:
        return None
    with get_engine().connect() as conn:
        row = conn.execute(select(sessions).where(sessions.c.token_hash == _hash(token))).mappings().first()
    if not row or as_utc(row["expires_at"]) < utcnow():
        return None
    return row["user_id"]


def touch_session(token: str | None, ip: str | None = None) -> None:
    """Note activity (at most every few minutes); short sessions stay alive while in use."""
    if not token:
        return
    now = utcnow()
    with get_engine().begin() as conn:
        row = conn.execute(select(sessions.c.remember).where(sessions.c.token_hash == _hash(token))).first()
        if row is None:
            return
        values = {"last_seen": now}
        if ip:
            values["ip"] = ip[:64]
        if not row[0]:
            values["expires_at"] = now + timedelta(hours=SESSION_HOURS)
        conn.execute(update(sessions).where(sessions.c.token_hash == _hash(token)).values(**values))


def list_sessions(user_id: int, current: str | None = None) -> list[dict]:
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(select(sessions).where(sessions.c.user_id == user_id, sessions.c.expires_at >= utcnow())
                                              ).mappings()]
    me = _hash(current) if current else None
    for r in rows:
        r["current"] = r["token_hash"] == me
        r["id"] = r["token_hash"][:12]
    return sorted(rows, key=lambda r: (not r["current"], -(as_utc(r.get("last_seen") or r.get("created_at") or r["expires_at"]).timestamp())))


def end_session(token: str | None) -> None:
    if isinstance(token, str) and token:
        with get_engine().begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.token_hash == _hash(token)))


def end_session_by_id(user_id: int, short_id: str) -> bool:
    """End one of the member's own sessions, chosen by the short id shown on the Account page."""
    if not short_id or len(short_id) != 12:
        return False
    with get_engine().begin() as conn:
        rows = conn.execute(select(sessions.c.token_hash).where(sessions.c.user_id == user_id)).scalars().all()
        target = next((h for h in rows if h.startswith(short_id)), None)
        if target:
            conn.execute(delete(sessions).where(sessions.c.token_hash == target, sessions.c.user_id == user_id))
    return bool(target)


def end_all_sessions(user_id: int, keep: str | None = None) -> int:
    """End every session of this member, except the one with token `keep` (the device doing it)."""
    q = delete(sessions).where(sessions.c.user_id == user_id)
    if keep:
        q = q.where(sessions.c.token_hash != _hash(keep))
    with get_engine().begin() as conn:
        return conn.execute(q).rowcount or 0