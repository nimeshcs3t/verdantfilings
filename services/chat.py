"""Company discussion rooms."""
from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import delete, insert, select

from core.db import as_utc, get_engine, messages, utcnow

MAX_LEN = 1000
MIN_GAP = timedelta(seconds=10)


def recent(market: str, ticker: str, limit: int = 100) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(messages).where(messages.c.market == market, messages.c.ticker == ticker)
                            .order_by(messages.c.created_at.desc()).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def post(user: dict, market: str, ticker: str, body: str) -> str | None:
    body = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", body or "").strip()
    if not body:
        return "Write something first."
    if len(body) > MAX_LEN:
        return f"Keep messages under {MAX_LEN} characters."
    with get_engine().connect() as conn:
        last = conn.execute(select(messages.c.created_at).where(messages.c.user_id == user["id"])
                            .order_by(messages.c.created_at.desc()).limit(1)).scalar()
    if last and utcnow() - as_utc(last) < MIN_GAP:
        return "You're posting too quickly. Wait a few seconds."
    with get_engine().begin() as conn:
        conn.execute(insert(messages).values(market=market, ticker=ticker, user_id=user["id"],
                                             username=user["username"], body=body, created_at=utcnow()))
    return None


def remove(message_id: int, user: dict) -> None:
    cond = messages.c.id == message_id
    if user["role"] != "admin":
        cond = cond & (messages.c.user_id == user["id"])
    with get_engine().begin() as conn:
        conn.execute(delete(messages).where(cond))
