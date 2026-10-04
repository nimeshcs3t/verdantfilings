"""Your own fair value per company, with an alert when the price gets close to (or passes) it."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import delete, insert, select, update

from core.db import fair_values, get_engine


def get_all(user_id: int) -> dict[tuple[str, str], dict]:
    with get_engine().connect() as conn:
        return {(r["market"], r["ticker"]): dict(r) for r in conn.execute(
            select(fair_values).where(fair_values.c.user_id == user_id)).mappings()}


def save(user_id: int, market: str, ticker: str, value: float, alert_pct: float, note: str = "") -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(fair_values).where(fair_values.c.user_id == user_id, fair_values.c.market == market,
                                               fair_values.c.ticker == ticker))
        if value and value > 0:
            conn.execute(insert(fair_values).values(user_id=user_id, market=market, ticker=ticker, value=value,
                                                    alert_pct=max(0.0, alert_pct), note=(note or "")[:2000]))


def upside(price: float | None, fair: float | None) -> float | None:
    return (fair / price - 1) if price and fair else None


def check(price_history) -> list[tuple[int, str]]:
    """Worker: (user_id, message) for prices within alert_pct of a fair value, at most once every 30 days each."""
    from .pipeline import get_company
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(select(fair_values)).mappings()]
    out, today = [], date.today()
    for r in rows:
        if r["alerted_on"] and date.fromisoformat(r["alerted_on"]) > today - timedelta(days=30):
            continue
        hist = price_history(r["market"], r["ticker"])
        if not hist:
            continue
        price = hist[-1][1]
        gap = price / r["value"] - 1
        if gap >= -r["alert_pct"] / 100:
            name = (get_company(r["market"], r["ticker"], resolve=False) or {}).get("name_en", r["ticker"])
            where = "is above" if gap > 0 else f"is within {abs(gap) * 100:.1f}% of"
            out.append((r["user_id"], f"<b>Fair value alert</b>\n<b>{name}</b>  {r['ticker']}\nPrice {price:,.2f} {where} "
                                      f"your fair value of {r['value']:,.2f}." + (f"\nYour note: {r['note'][:300]}" if r["note"] else "")))
            with get_engine().begin() as conn:
                conn.execute(update(fair_values).where(fair_values.c.user_id == r["user_id"], fair_values.c.market == r["market"],
                                                       fair_values.c.ticker == r["ticker"]).values(alerted_on=today.isoformat()))
    return out
