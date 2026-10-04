"""Insider buying alerts and new-listing alerts."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from core.db import get_engine, insider_tx, listed_companies, new_listings, sent_once, watchlist

log = logging.getLogger(__name__)
BIG_BUY_USD = 1_000_000


def once(key: str) -> bool:
    """True the first time a key is seen (so each alert goes out once)."""
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(sent_once).values(key=key[:120]))
        return True
    except IntegrityError:
        return False


def insider_clusters(days: int = 30) -> list[dict]:
    """Companies where 2+ insiders bought within `days`, or a single US purchase worth $1M or more."""
    since = date.today() - timedelta(days=days)
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(select(insider_tx).where(
            insider_tx.c.tx_date >= since, insider_tx.c.code.in_(("P", "+")), insider_tx.c.seq >= 0)).mappings()]
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if (r["shares"] or 0) > 0:
            groups.setdefault((r["market"], r["ticker"]), []).append(r)
    out = []
    for (m, t), items in groups.items():
        people = {i["person"] for i in items}
        value = sum((i["shares"] or 0) * (i["price"] or 0) for i in items)
        if len(people) >= 2 or (m == "US" and value >= BIG_BUY_USD):
            out.append({"market": m, "ticker": t, "people": sorted(people), "value": value,
                        "shares": sum(i["shares"] or 0 for i in items), "items": items})
    return out


def send_insider_alerts() -> int:
    from . import deliver, settings
    from .pipeline import get_company
    sent = 0
    for c in insider_clusters():
        with get_engine().connect() as conn:
            users = {u for (u,) in conn.execute(select(watchlist.c.user_id).where(
                watchlist.c.market == c["market"], watchlist.c.ticker == c["ticker"]))}
        if not users or not once(f"ins:{c['market']}:{c['ticker']}:{date.today():%Y-%m}:{len(c['people'])}"):
            continue
        name = (get_company(c["market"], c["ticker"], resolve=False) or {}).get("name_en", c["ticker"])
        value = f", about ${c['value']:,.0f}" if c["value"] else ""
        text = (f"<b>Insider buying</b>\n<b>{name}</b>  {c['ticker']}\n{len(c['people'])} insider"
                f"{'s' if len(c['people']) != 1 else ''} bought {c['shares']:,.0f} shares in the last 30 days{value}: "
                + ", ".join(c["people"][:5]))
        for uid in users:
            if settings.get(uid).get("insider_alerts", True):
                sent += deliver.send(uid, f"Insider buying: {name}", text)
    return sent


def record_new(market: str, old: set[str], new_rows: list[dict]) -> int:
    """Called when a market's company list is refreshed: remember companies that weren't there before."""
    if not old:
        return 0      # first load of this market: everything is "new", so nothing is reported
    fresh = [r for r in new_rows if r["ticker"] not in old]
    with get_engine().begin() as conn:
        for r in fresh[:200]:
            try:
                conn.execute(insert(new_listings).values(market=market, ticker=r["ticker"], name_en=r["name_en"],
                                                         seen_on=date.today()))
            except IntegrityError:
                pass
    return len(fresh)


def send_new_listing_alerts() -> int:
    from . import deliver, settings
    from core.db import user_settings
    with get_engine().connect() as conn:
        fresh = [dict(r) for r in conn.execute(select(new_listings).where(new_listings.c.seen_on >= date.today() - timedelta(days=2))).mappings()]
        members = [u for (u,) in conn.execute(select(user_settings.c.user_id).where(user_settings.c.key == "new_listing_markets"))]
    if not fresh:
        return 0
    sent = 0
    for uid in set(members):
        markets = settings.get(uid).get("new_listing_markets") or []
        items = [r for r in fresh if r["market"] in markets and once(f"nl:{uid}:{r['market']}:{r['ticker']}")]
        if items:
            lines = ["<b>New listings</b>"] + [f"• {r['name_en']}  {r['ticker']} ({r['market']})" for r in items[:30]]
            sent += deliver.send(uid, "New listings", "\n".join(lines))
    return sent
