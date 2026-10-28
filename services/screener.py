"""Screeners.

1. Announcements: every market's latest filings (the same market-wide feeds keyword alerts use), sorted into event
   types by title. Only useful types are kept, for 30 days. No AI.
2. My companies: key figures for everything a member follows or holds, from data the app already has."""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from sqlalchemy import delete, insert, select

from core.db import get_engine, market_events, utcnow

from .classify import categorize, label

log = logging.getLogger(__name__)

EVENT_TYPES = ["buyback", "insider", "ownership", "dividend", "capital", "mna", "contract", "legal"]
KEEP_DAYS = 30

_feed_memo: dict[str, tuple[float, list]] = {}


def market_feed(src) -> list[dict]:
    """A source's market-wide feed, fetched at most once per 10 minutes per run (shared with keyword alerts)."""
    hit = _feed_memo.get(src.market)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    feed = src.market_feed()
    _feed_memo[src.market] = (time.time(), feed)
    return feed


def collect_events() -> int:
    """Worker: store new announcements of the chosen types from every market. Returns how many were added."""
    from sources import configured_sources
    from .connections import NameMatcher, scan_feed
    from .translate import glossary_or_cached
    added = 0
    try:
        matcher = NameMatcher()
    except Exception:
        matcher = None
    for src in configured_sources():
        try:
            feed = market_feed(src)
        except Exception as exc:
            log.warning("market feed failed for %s: %s", src.market, exc)
            continue
        try:                                    # any company on this market naming one you follow
            named = scan_feed(src.market, feed, matcher) if matcher else []
        except Exception as exc:
            log.info("name check failed for %s: %s", src.market, exc)
            named = []
        named_uids = {f["uid"][:64]: hits for f, hits in named if f.get("uid")}
        rows = []
        for f in feed:
            title_en = f.get("title_en") or glossary_or_cached(f.get("title_local") or "", src.source_lang) or ""
            cat = categorize(title_en, f.get("title_local"))
            if f.get("uid") and (cat in EVENT_TYPES or f["uid"][:64] in named_uids):
                rows.append({"uid": f["uid"][:64], "market": src.market, "ticker": (f.get("ticker") or "")[:16] or None,
                             "company": (f.get("company") or "")[:200], "filed_date": f.get("date") or src.today(),
                             "category": cat, "title_local": f.get("title_local") or "", "title_en": title_en,
                             "url": f.get("url") or "", "created_at": utcnow()})
        if not rows:
            continue
        todo = [r for r in rows if not r["title_en"] and r["title_local"]][:150]
        if todo and src.source_lang not in ("en", "auto"):
            try:                                          # free machine translation, no AI
                from .translate import translate_lines
                for r, en in zip(todo, translate_lines([r["title_local"] for r in todo], src.source_lang, allow_ai=False)):
                    r["title_en"] = (en or "")[:500]
            except Exception as exc:
                log.info("title translation failed for %s: %s", src.market, exc)
        with get_engine().begin() as conn:
            known = set(conn.execute(select(market_events.c.uid).where(market_events.c.uid.in_([r["uid"] for r in rows]))).scalars())
            fresh = [r for r in rows if r["uid"] not in known]
            if fresh:
                conn.execute(insert(market_events), fresh)
                added += len(fresh)
            if named_uids:
                from core.db import filing_mentions
                have = set(conn.execute(select(filing_mentions.c.uid, filing_mentions.c.market, filing_mentions.c.ticker)
                                        .where(filing_mentions.c.uid.in_(list(named_uids)))).all())
                links = [{"uid": u, "market": m, "ticker": t, "found_at": utcnow()}
                         for u, hits in named_uids.items() for m, t in hits if (u, m, t) not in have]
                if links:
                    conn.execute(insert(filing_mentions), links)
    with get_engine().begin() as conn:
        conn.execute(delete(market_events).where(market_events.c.filed_date < date.today() - timedelta(days=KEEP_DAYS)))
    return added


def events(markets: list[str] | None = None, types: list[str] | None = None, days: int = 7, text: str = "",
           limit: int = 500) -> list[dict]:
    q = select(market_events).where(market_events.c.filed_date >= date.today() - timedelta(days=days))
    if markets:
        q = q.where(market_events.c.market.in_(markets))
    if types:
        q = q.where(market_events.c.category.in_(types))
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(q.order_by(market_events.c.filed_date.desc(), market_events.c.created_at.desc())
                                              .limit(limit * 3 if text else limit)).mappings()]
    if text.strip():
        t = text.strip().lower()
        rows = [r for r in rows if t in f"{r['company']} {r['ticker'] or ''} {r['title_en']} {r['title_local']}".lower()]
    return rows[:limit]


def event_counts(days: int = 7) -> dict[str, int]:
    from sqlalchemy import func
    with get_engine().connect() as conn:
        return dict(conn.execute(select(market_events.c.category, func.count()).where(
            market_events.c.filed_date >= date.today() - timedelta(days=days)).group_by(market_events.c.category)).all())


def type_label(key: str) -> str:
    return label(key)


# ---- my companies ---------------------------------------------------------------------------------------------------
def my_companies(user_id: int) -> list[dict]:
    """One row per followed or held company with price, valuation, growth, margins and distance to fair value/target."""
    from sources import get_source
    from . import fairvalue, journal, portfolio, prices, valuation, watch
    from .pipeline import get_company
    pairs: dict[tuple[str, str], str] = {}
    for w in watch.get_watchlist(user_id):
        pairs[(w["market"], w["ticker"])] = w["name_en"]
    held = {(h["market"], h["ticker"]) for h in portfolio.current_holdings(user_id)}
    for m, t in held:
        if (m, t) not in pairs:
            pairs[(m, t)] = (get_company(m, t, resolve=False) or {}).get("name_en") or t
    try:
        fair_values = fairvalue.get_all(user_id)
    except Exception:
        fair_values = {}
    out = []
    for (m, t), name in pairs.items():
        src = get_source(m)
        try:
            hist = prices.history(m, t, years=1)
        except Exception:
            hist = []
        try:
            cur = portfolio.currency_for(m, t)
        except Exception:
            cur = ""
        try:
            q = valuation.quick_view(m, t, hist, cur)
        except Exception:
            q = {}
        fair = (fair_values.get((m, t)) or {}).get("value")
        targets = journal.targets(user_id, m, t)
        price = q.get("price")
        out.append({"market": m, "ticker": t, "name": name, "country": src.country if src else m, "held": (m, t) in held,
                    "price": price, "currency": cur, "ret1y": q.get("ret1y"), "market_cap": q.get("market_cap"),
                    "pe": q.get("pe"), "ps": q.get("ps"), "growth": q.get("growth"), "op_margin": q.get("op_margin"),
                    "net_margin": q.get("net_margin"), "drawdown": q.get("drawdown"),
                    "vs_fair": (price / fair - 1) if price and fair else None,
                    "vs_target": (price / targets[0][0] - 1) if price and targets else None})
    return out
