"""Small helpers: daily usage counters (reusing the api_usage table) and a key-value store."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from .db import api_usage, bot_state, get_engine


def count(key: str, n: int = 1) -> None:
    """Add to today's counter for `key` (UTC day). Never raises."""
    day = datetime.now(timezone.utc).date().isoformat()
    try:
        with get_engine().begin() as conn:
            done = conn.execute(update(api_usage).where(api_usage.c.market == key[:8], api_usage.c.day == day)
                                .values(calls=api_usage.c.calls + n)).rowcount
            if not done:
                conn.execute(insert(api_usage).values(market=key[:8], day=day, calls=n))
    except Exception:
        pass


def counts_today() -> dict[str, int]:
    day = datetime.now(timezone.utc).date().isoformat()
    with get_engine().connect() as conn:
        return {r[0]: r[1] for r in conn.execute(select(api_usage.c.market, api_usage.c.calls)
                                                  .where(api_usage.c.day == day))}


def get_state(key: str, default: str | None = None) -> str | None:
    with get_engine().connect() as conn:
        value = conn.execute(select(bot_state.c.value).where(bot_state.c.key == key)).scalar()
    return default if value is None else value


def set_state(key: str, value: str) -> None:
    with get_engine().begin() as conn:
        if not conn.execute(update(bot_state).where(bot_state.c.key == key).values(value=value)).rowcount:
            try:
                conn.execute(insert(bot_state).values(key=key, value=value))
            except IntegrityError:
                pass
