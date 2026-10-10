"""Read-only share links: percentages and allocation only, never amounts. Links can expire."""
from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import delete, insert, select

from core.db import as_utc, get_engine, share_links, utcnow

EXPIRY_CHOICES = {"7 days": 7, "30 days": 30, "90 days": 90, "Never": None}


def info(user_id: int) -> dict | None:
    """The member's active link: token, created_at, expires_at (None = never). Expired links are removed."""
    with get_engine().connect() as conn:
        row = conn.execute(select(share_links).where(share_links.c.user_id == user_id)).mappings().first()
    if not row:
        return None
    if row.get("expires_at") and as_utc(row["expires_at"]) < utcnow():
        revoke(user_id, log=False)
        return None
    return dict(row)


def get_token(user_id: int) -> str | None:
    row = info(user_id)
    return row["token"] if row else None


def create(user_id: int, days: int | None = 30) -> str:
    from core.security import record
    revoke(user_id, log=False)
    token = secrets.token_urlsafe(24)
    with get_engine().begin() as conn:
        conn.execute(insert(share_links).values(token=token, user_id=user_id, created_at=utcnow(),
                                                expires_at=utcnow() + timedelta(days=days) if days else None))
    record("share_created", user_id, None, f"expires in {days} days" if days else "never expires")
    return token


def revoke(user_id: int, log: bool = True) -> None:
    with get_engine().begin() as conn:
        removed = conn.execute(delete(share_links).where(share_links.c.user_id == user_id)).rowcount
    if log and removed:
        from core.security import record
        record("share_removed", user_id)


def owner(token: str | None) -> int | None:
    if not token or len(token) > 64:
        return None
    with get_engine().connect() as conn:
        row = conn.execute(select(share_links).where(share_links.c.token == token)).mappings().first()
    if not row or (row.get("expires_at") and as_utc(row["expires_at"]) < utcnow()):
        return None
    return row["user_id"]
