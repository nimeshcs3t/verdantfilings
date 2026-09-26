"""Holdings with value and gain/loss, and price alerts."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, insert, select, update

from core.db import get_engine, holdings, price_alerts, utcnow, watchlist
from sources import get_source

from . import prices

log = logging.getLogger(__name__)
CURRENCY = {"KR": "KRW", "US": "USD", "AU": "AUD", "PL": "PLN", "JP": "JPY", "IL": "ILS"}
KINDS = {"move": "Daily move of at least", "above": "Price rises above", "below": "Price falls below"}


# ---- holdings ---------------------------------------------------------------------------
def list_holdings(user_id: int) -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(select(holdings).where(holdings.c.user_id == user_id)
                                              .order_by(holdings.c.market, holdings.c.ticker)).mappings()]


def save_holding(user_id: int, market: str, ticker: str, shares: float, avg_price: float) -> str | None:
    if shares <= 0 or avg_price < 0:
        return "Enter a positive number of shares and a price of zero or more."
    with get_engine().begin() as conn:
        conn.execute(delete(holdings).where(holdings.c.user_id == user_id, holdings.c.market == market,
                                            holdings.c.ticker == ticker))
        conn.execute(insert(holdings).values(user_id=user_id, market=market, ticker=ticker, shares=shares,
                                             avg_price=avg_price, created_at=utcnow()))
    return None


def remove_holding(user_id: int, market: str, ticker: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(holdings).where(holdings.c.user_id == user_id, holdings.c.market == market,
                                            holdings.c.ticker == ticker))


def value(h: dict, hist: list) -> dict:
    last = hist[-1][1] if hist else None
    cost = h["shares"] * h["avg_price"]
    worth = h["shares"] * last if last is not None else None
    return {**h, "last": last, "cost": cost, "value": worth,
            "gain": (worth - cost) if worth is not None else None,
            "gain_pct": ((worth / cost - 1) if worth is not None and cost else None),
            "day": prices.last_move(hist) if hist else None, "currency": CURRENCY.get(h["market"], "")}


# ---- price alerts -----------------------------------------------------------------------
def list_alerts(user_id: int) -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(select(price_alerts).where(price_alerts.c.user_id == user_id)
                                              .order_by(price_alerts.c.id)).mappings()]


def add_alert(user_id: int, market: str, ticker: str, kind: str, value_: float) -> str | None:
    if kind not in KINDS or value_ <= 0:
        return "Enter a positive number."
    if len(list_alerts(user_id)) >= 50:
        return "You can have up to 50 price alerts."
    with get_engine().begin() as conn:
        conn.execute(insert(price_alerts).values(user_id=user_id, market=market, ticker=ticker, kind=kind,
                                                 value=value_, active=True, created_at=utcnow()))
    return None


def remove_alert(user_id: int, alert_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(price_alerts).where(price_alerts.c.id == alert_id, price_alerts.c.user_id == user_id))


def check_alerts() -> int:
    """Worker: send Telegram messages for price alerts that triggered."""
    from core.db import users
    from . import telegram
    if not telegram.enabled():
        return 0
    with get_engine().connect() as conn:
        alerts = [dict(r) for r in conn.execute(select(price_alerts).where(price_alerts.c.active == True)).mappings()]  # noqa: E712
        chats = dict(conn.execute(select(users.c.id, users.c.telegram_chat_id).where(
            users.c.telegram_chat_id.is_not(None), users.c.is_active == True)).all())  # noqa: E712
        wl = {}
        for a in alerts:
            if a["ticker"] == "*" and a["user_id"] not in wl:
                wl[a["user_id"]] = [tuple(r) for r in conn.execute(select(watchlist.c.market, watchlist.c.ticker)
                                                                   .where(watchlist.c.user_id == a["user_id"]))]
    cache: dict[tuple[str, str], list] = {}

    def hist(m: str, t: str) -> list:
        if (m, t) not in cache:
            try:
                cache[(m, t)] = prices.history(m, t)
            except Exception:
                cache[(m, t)] = []
        return cache[(m, t)]

    from core.usage import get_state, set_state
    from .pipeline import get_company
    sent = 0
    for a in alerts:
        chat = chats.get(a["user_id"])
        if not chat:
            continue
        watch_all = a["ticker"] == "*"
        targets = wl.get(a["user_id"], []) if watch_all else [(a["market"], a["ticker"])]
        fired = set()
        for m, t in targets:
            src = get_source(m)
            today = src.today().isoformat() if src else datetime.now(ZoneInfo("UTC")).date().isoformat()
            state_key = f"pa{a['id']}:{m}:{t}"[:40]
            if a["kind"] == "move" and (get_state(state_key) if watch_all else a["last_fired"]) == today:
                continue                      # a move alert fires at most once a day per stock
            h = hist(m, t)
            if len(h) < 2:
                continue
            last, move = h[-1][1], prices.last_move(h)
            text = None
            if a["kind"] == "move" and move is not None and abs(move) * 100 >= a["value"]:
                text = f"{'▲' if move > 0 else '▼'} {abs(move) * 100:.1f}% today, last {last:,.2f}"
            elif a["kind"] == "above" and last > a["value"]:
                text = f"Price {last:,.2f} is above your alert at {a['value']:,.2f}"
            elif a["kind"] == "below" and last < a["value"]:
                text = f"Price {last:,.2f} is below your alert at {a['value']:,.2f}"
            if not text:
                continue
            comp = get_company(m, t, resolve=False) or {"name_en": t}
            sent += telegram.send_message(chat, f"<b>Price alert</b>\n<b>{comp['name_en']}</b>  {t}\n{text}")
            fired.add(today)
            if watch_all:
                set_state(state_key, today)
        if fired and not watch_all:
            values = {"last_fired": max(fired)}
            if a["kind"] in ("above", "below"):
                values["active"] = False          # one-off alerts switch off after firing
            with get_engine().begin() as conn:
                conn.execute(update(price_alerts).where(price_alerts.c.id == a["id"]).values(**values))
    return sent
