"""Per-user watchlists."""
from __future__ import annotations

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.db import companies, get_engine, utcnow, watchlist
from core.plans import watchlist_limit

from .pipeline import get_company


def get_watchlist(user_id: int) -> list[dict]:
    j = watchlist.join(companies, (watchlist.c.market == companies.c.market) & (watchlist.c.ticker == companies.c.ticker))
    with get_engine().connect() as conn:
        rows = conn.execute(select(watchlist.c.market, watchlist.c.ticker, watchlist.c.notify,
                                   companies.c.name_en, companies.c.name_local)
                            .select_from(j).where(watchlist.c.user_id == user_id)
                            .order_by(companies.c.name_en)).mappings().all()
    return [dict(r) for r in rows]


def is_watching(user_id: int, market: str, ticker: str) -> bool:
    with get_engine().connect() as conn:
        return conn.execute(select(watchlist.c.ticker).where(
            watchlist.c.user_id == user_id, watchlist.c.market == market, watchlist.c.ticker == ticker)).first() is not None


def add(user: dict, market: str, ticker: str) -> tuple[dict | None, str | None]:
    with get_engine().connect() as conn:
        count = conn.execute(select(func.count()).select_from(watchlist)
                             .where(watchlist.c.user_id == user["id"])).scalar_one()
    limit = watchlist_limit(user["plan"])
    if count >= limit:
        return None, f"Your plan allows {limit} companies. Remove one to add another."
    comp = get_company(market, ticker)
    if comp is None:
        return None, f"No listed company found for {ticker}."
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(watchlist).values(user_id=user["id"], market=market, ticker=comp["ticker"],
                                                  notify=True, added_at=utcnow()))
    except IntegrityError:
        return comp, None
    return comp, None


def remove(user_id: int, market: str, ticker: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(watchlist).where(watchlist.c.user_id == user_id, watchlist.c.market == market,
                                             watchlist.c.ticker == ticker))


def set_notify(user_id: int, market: str, ticker: str, on: bool) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(watchlist).where(watchlist.c.user_id == user_id, watchlist.c.market == market,
                                             watchlist.c.ticker == ticker).values(notify=on))
