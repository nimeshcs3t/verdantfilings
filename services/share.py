"""Read-only share links: percentages and allocation only, never amounts."""
from __future__ import annotations

import secrets

from sqlalchemy import delete, insert, select

from core.db import get_engine, share_links, utcnow


def get_token(user_id: int) -> str | None:
    with get_engine().connect() as conn:
        return conn.execute(select(share_links.c.token).where(share_links.c.user_id == user_id)).scalar()


def create(user_id: int) -> str:
    revoke(user_id)
    token = secrets.token_urlsafe(24)
    with get_engine().begin() as conn:
        conn.execute(insert(share_links).values(token=token, user_id=user_id, created_at=utcnow()))
    return token


def revoke(user_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(share_links).where(share_links.c.user_id == user_id))


def owner(token: str | None) -> int | None:
    if not token or len(token) > 64:
        return None
    with get_engine().connect() as conn:
        return conn.execute(select(share_links.c.user_id).where(share_links.c.token == token)).scalar()
