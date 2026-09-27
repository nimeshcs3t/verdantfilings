"""Holdings with value and gain/loss, and price alerts."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, insert, select, update

from core.db import custom_symbols, get_engine, holdings, price_alerts, targets, transactions, utcnow, watchlist
from sources import get_source

from . import prices

log = logging.getLogger(__name__)
CURRENCY = {"KR": "KRW", "US": "USD", "AU": "AUD", "PL": "PLN", "JP": "JPY", "IL": "ILS"}
KINDS = {"move": "Daily move of at least", "above": "Price rises above", "below": "Price falls below"}


OTHER = "X"          # market code for holdings added by Yahoo Finance symbol
SUFFIX_COUNTRY = {"NS": "India", "BO": "India", "L": "UK", "IL": "UK", "DE": "Germany", "F": "Germany", "PA": "France",
                  "AS": "Netherlands", "BR": "Belgium", "MI": "Italy", "MC": "Spain", "LS": "Portugal", "SW": "Switzerland",
                  "ST": "Sweden", "OL": "Norway", "CO": "Denmark", "HE": "Finland", "IR": "Ireland", "VI": "Austria",
                  "WA": "Poland", "PR": "Czechia", "AT": "Greece", "IS": "Turkey", "TO": "Canada", "V": "Canada",
                  "NE": "Canada", "HK": "Hong Kong", "SS": "China", "SZ": "China", "T": "Japan", "KS": "South Korea",
                  "KQ": "South Korea", "TW": "Taiwan", "TWO": "Taiwan", "SI": "Singapore", "KL": "Malaysia",
                  "BK": "Thailand", "JK": "Indonesia", "AX": "Australia", "NZ": "New Zealand", "SA": "Brazil",
                  "MX": "Mexico", "BA": "Argentina", "SN": "Chile", "JO": "South Africa", "TA": "Israel",
                  "SR": "Saudi Arabia", "QA": "Qatar", "AE": "UAE"}
MINOR_UNITS = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}


def add_symbol(symbol: str) -> tuple[dict | None, str | None]:
    """Look up a Yahoo Finance symbol from any market and remember its name, currency and country."""
    sym = (symbol or "").strip().upper()
    if not sym or len(sym) > 16:
        return None, "Enter a Yahoo Finance symbol, e.g. RELIANCE.NS, VOD.L or 0700.HK."
    with get_engine().connect() as conn:
        row = conn.execute(select(custom_symbols).where(custom_symbols.c.symbol == sym)).mappings().first()
    if row:
        return dict(row), None
    details = prices.symbol_details(sym)
    if not details:
        return None, f"Yahoo Finance doesn't recognise {sym}. Check the symbol on finance.yahoo.com."
    currency, scale = MINOR_UNITS.get(details["currency"], (details["currency"].upper(), 1.0))
    suffix = sym.rsplit(".", 1)[1] if "." in sym else ""
    country = SUFFIX_COUNTRY.get(suffix, "USA" if not suffix else details["exchange"] or "Other")
    row = {"symbol": sym, "name": details["name"], "currency": currency, "scale": scale, "country": country}
    with get_engine().begin() as conn:
        conn.execute(insert(custom_symbols).values(**row))
    return row, None


def symbol_info(symbol: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(custom_symbols).where(custom_symbols.c.symbol == symbol)).mappings().first()
    return dict(row) if row else None


def currency_for(market: str, ticker: str) -> str:
    if market == OTHER:
        info = symbol_info(ticker)
        return info["currency"] if info else "USD"
    return CURRENCY.get(market, "USD")


def price_history(market: str, ticker: str, years: int = 1) -> list:
    """Daily closes in the holding's currency (any-market symbols are converted from pence and similar)."""
    if market != OTHER:
        return prices.history(market, ticker, years)
    info = symbol_info(ticker) or {"scale": 1.0}
    return [(d, v / (info["scale"] or 1.0)) for d, v in prices.symbol_history(ticker, years)]


def hide_amounts(user_id: int) -> bool:
    from core.usage import get_state
    return get_state(f"hide_amt:{user_id}", "0") == "1"


def set_hide_amounts(user_id: int, hidden: bool) -> None:
    from core.usage import set_state
    set_state(f"hide_amt:{user_id}", "1" if hidden else "0")


HOME_CURRENCIES = ["USD", "AUD", "KRW", "INR", "EUR", "GBP", "JPY", "PLN", "ILS", "SGD", "HKD", "CAD", "CHF", "CNY"]


# ---- transactions -----------------------------------------------------------------------
def list_transactions(user_id: int) -> list[dict]:
    migrate_holdings(user_id)
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(select(transactions).where(transactions.c.user_id == user_id)
                                              .order_by(transactions.c.tx_date.desc(), transactions.c.id.desc())).mappings()]


def add_transaction(user_id: int, market: str, ticker: str, kind: str, tx_date, shares: float, price: float,
                    fees: float = 0.0) -> str | None:
    if kind not in ("buy", "sell") or shares <= 0 or price < 0 or fees < 0:
        return "Enter a positive number of shares, and a price and fees of zero or more."
    if kind == "sell":
        from .portfolio_calc import positions
        held = positions(list_transactions(user_id), until=tx_date).get((market, ticker), {}).get("shares", 0)
        if shares > held + 1e-9:
            return f"You held {held:,.4g} shares on that date, so you can't sell {shares:,.4g}."
    with get_engine().begin() as conn:
        conn.execute(insert(transactions).values(user_id=user_id, market=market, ticker=ticker, kind=kind, tx_date=tx_date,
                                                 shares=shares, price=price, fees=fees, created_at=utcnow()))
    return None


def remove_transaction(user_id: int, tx_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(transactions).where(transactions.c.id == tx_id, transactions.c.user_id == user_id))


def migrate_holdings(user_id: int) -> None:
    """One-time: turn holdings saved before transactions existed into buys dated when they were added."""
    with get_engine().connect() as conn:
        old = [dict(r) for r in conn.execute(select(holdings).where(holdings.c.user_id == user_id)).mappings()]
    if not old:
        return
    with get_engine().begin() as conn:
        for h in old:
            day = (h["created_at"] or utcnow()).date()
            conn.execute(insert(transactions).values(user_id=user_id, market=h["market"], ticker=h["ticker"], kind="buy",
                                                     tx_date=day, shares=h["shares"], price=h["avg_price"], fees=0,
                                                     created_at=utcnow()))
        conn.execute(delete(holdings).where(holdings.c.user_id == user_id))


def current_holdings(user_id: int) -> list[dict]:
    """Holdings derived from transactions: shares and average price per company."""
    from .portfolio_calc import positions
    return [{"market": m, "ticker": t, "shares": p["shares"], "avg_price": p["avg"]}
            for (m, t), p in positions(list_transactions(user_id)).items() if p["shares"] > 1e-9]


# ---- targets and currency ---------------------------------------------------------------
def get_targets(user_id: int) -> dict[tuple[str, str], float]:
    with get_engine().connect() as conn:
        return {(r[0], r[1]): r[2] for r in conn.execute(select(targets.c.market, targets.c.ticker, targets.c.pct)
                                                          .where(targets.c.user_id == user_id))}


def save_targets(user_id: int, values: dict[tuple[str, str], float | None]) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(targets).where(targets.c.user_id == user_id))
        for (m, t), pct in values.items():
            if pct is not None and pct > 0:
                conn.execute(insert(targets).values(user_id=user_id, market=m, ticker=t, pct=float(pct)))


def home_currency(user_id: int) -> str:
    from core.usage import get_state
    return get_state(f"home_ccy:{user_id}", "USD") or "USD"


def set_home_currency(user_id: int, currency: str) -> None:
    from core.usage import set_state
    set_state(f"home_ccy:{user_id}", currency if currency in HOME_CURRENCIES else "USD")


# ---- holdings (kept for older data; new entries are transactions) -----------------------
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
    cur = currency_for(h["market"], h["ticker"])
    last = hist[-1][1] if hist else None
    cost = h["shares"] * h["avg_price"]
    worth = h["shares"] * last if last is not None else None
    return {**h, "last": last, "cost": cost, "value": worth,
            "gain": (worth - cost) if worth is not None else None,
            "gain_pct": ((worth / cost - 1) if worth is not None and cost else None),
            "day": prices.last_move(hist) if hist else None, "currency": cur}


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
