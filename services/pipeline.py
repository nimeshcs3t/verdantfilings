"""Fetch filings from regulators, store them, translate, summarize and send alerts.

Two modes:
  worker (DIRECT_FETCH=true, set by worker.py): talks to regulators and fills the database.
  website (default): only reads the database, and queues work for the worker.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from core.config import direct_fetch, get_secret
from core.db import (as_utc, companies, enrich_queue, filings, get_engine, listed_companies, users, utcnow,
                     watchlist)
from sources import Company, SourceBusy, configured_sources, get_source

from . import telegram
from .summarize import summarize
from .translate import translate_text, translate_title

log = logging.getLogger(__name__)
SYNC_INTERVAL = timedelta(minutes=10)
LISTING_MAX_AGE = timedelta(hours=24)
MAX_OVERVIEWS_PER_RUN = 20


# ---- company listings --------------------------------------------------------------------
def refresh_listings(force: bool = False) -> int:
    """Worker only: copy each exchange's full company list into the database once a day."""
    total = 0
    for src in configured_sources():
        with get_engine().connect() as conn:
            newest = conn.execute(select(func.max(listed_companies.c.updated_at))
                                  .where(listed_companies.c.market == src.market)).scalar()
        if not force and newest and utcnow() - as_utc(newest) < LISTING_MAX_AGE:
            continue
        now = utcnow()
        rows = [dict(market=c.market, ticker=c.ticker, source_id=c.source_id, name_local=c.name_local,
                     name_en=c.name_en, updated_at=now) for c in src.all_listed()]
        if not rows:
            continue
        with get_engine().begin() as conn:
            conn.execute(delete(listed_companies).where(listed_companies.c.market == src.market))
            conn.execute(insert(listed_companies), rows)
        total += len(rows)
    return total


def listings_ready(market: str) -> bool:
    with get_engine().connect() as conn:
        return conn.execute(select(listed_companies.c.ticker)
                            .where(listed_companies.c.market == market).limit(1)).first() is not None


def _listed(market: str, ticker: str) -> Company | None:
    with get_engine().connect() as conn:
        r = conn.execute(select(listed_companies).where(listed_companies.c.market == market,
                                                        listed_companies.c.ticker == ticker)).mappings().first()
    return Company(r["market"], r["ticker"], r["source_id"], r["name_local"], r["name_en"]) if r else None


def search_companies(market: str, query: str, limit: int = 8) -> list[Company]:
    src = get_source(market)
    q = query.strip()
    if src is None or len(q) < 2:
        return []
    if direct_fetch():
        return src.search(q, limit)
    ql = q.lower()
    with get_engine().connect() as conn:
        rows = conn.execute(select(listed_companies).where(
            listed_companies.c.market == market,
            or_(listed_companies.c.ticker == q.upper(),
                func.lower(listed_companies.c.name_en).contains(ql, autoescape=True),
                listed_companies.c.name_local.contains(q, autoescape=True))).limit(60)).mappings().all()
    exact = {ql, q.upper()}
    ranked = sorted(rows, key=lambda r: (not ({(r["name_en"] or "").lower(), r["name_local"], r["ticker"]} & exact),
                                         len(r["name_local"] or "")))
    return [Company(r["market"], r["ticker"], r["source_id"], r["name_local"], r["name_en"]) for r in ranked[:limit]]


def get_company(market: str, ticker: str, resolve: bool = True) -> dict | None:
    src = get_source(market)
    if src is None:
        return None
    ticker = src.normalize_ticker(ticker) or ticker
    with get_engine().connect() as conn:
        row = conn.execute(select(companies).where(
            companies.c.market == market, companies.c.ticker == ticker)).mappings().first()
    if row:
        return dict(row)
    if not resolve:
        return None
    comp = src.resolve(ticker) if direct_fetch() else _listed(market, ticker)
    if comp is None:
        return None
    values = dict(market=comp.market, ticker=comp.ticker, source_id=comp.source_id,
                  name_local=comp.name_local, name_en=comp.name_en, last_synced=None)
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(companies).values(**values))
    except IntegrityError:
        pass
    return values


# ---- filings -----------------------------------------------------------------------------
def sync_company(market: str, ticker: str, force: bool = False) -> int:
    """Worker only: pull recent filings for one company. Returns the number of new filings stored."""
    if not direct_fetch():
        return 0
    comp = get_company(market, ticker)
    if comp is None:
        return 0
    last = as_utc(comp.get("last_synced"))
    if not force and last and utcnow() - last < SYNC_INTERVAL:
        return 0
    src = get_source(market)
    today = src.today()
    start = today - timedelta(days=src.incremental_days if last else src.backfill_days)
    company = Company(comp["market"], comp["ticker"], comp["source_id"], comp["name_local"], comp["name_en"])
    try:
        found = src.list_filings(company, start, today)
    except SourceBusy:
        return 0          # usage budget: try again on a later run, without marking the company synced

    uids = [f.uid for f in found]
    existing = set()
    if uids:
        with get_engine().connect() as conn:
            existing = set(conn.execute(select(filings.c.uid).where(filings.c.uid.in_(uids))).scalars())
    new = 0
    for f in found:
        if f.uid in existing:
            continue
        row = dict(uid=f.uid, market=f.market, ticker=f.ticker, company_name=comp["name_en"] or f.company_name,
                   filed_date=f.filed_date, title_local=f.title_local,
                   title_en=f.title_en or translate_title(f.title_local, src.source_lang), filer=f.filer, url=f.url,
                   notified=False, created_at=utcnow())
        try:
            with get_engine().begin() as conn:
                conn.execute(insert(filings).values(**row))
            new += 1
        except IntegrityError:
            pass
    with get_engine().begin() as conn:
        conn.execute(update(companies).where(companies.c.market == market, companies.c.ticker == comp["ticker"])
                     .values(last_synced=utcnow()))
    return new


def get_filing(uid: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(filings).where(filings.c.uid == uid)).mappings().first()
    return dict(row) if row else None


def request_overview(uid: str) -> None:
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(enrich_queue).values(uid=uid, requested_at=utcnow()))
    except IntegrityError:
        pass


def is_queued(uid: str) -> bool:
    with get_engine().connect() as conn:
        return conn.execute(select(enrich_queue.c.uid).where(enrich_queue.c.uid == uid)).first() is not None


def enrich_filing(uid: str, force: bool = False) -> dict | None:
    """Download the document, translate the opening part, and write an overview.
    On the website this only queues the filing for the worker."""
    row = get_filing(uid)
    if row is None or (row["summary_en"] and not force):
        return row
    if not direct_fetch():
        request_overview(uid)
        row["enrich_pending"] = True
        return row
    src = get_source(row["market"])
    try:
        text = src.fetch_document_text(uid)
    except Exception as exc:
        log.warning("document fetch failed for %s: %s", uid, exc)
        text = ""
    if not text:
        row["summary_en"] = None
        row["enrich_error"] = "The regulator didn't return the document text. Open the original filing instead."
        return row
    limit = int(get_secret("BODY_TRANSLATE_CHARS", 6000))
    body_en = translate_text(text[:limit], src.source_lang)
    summary = summarize(text, body_en, row["title_en"], row["company_name"])
    with get_engine().begin() as conn:
        conn.execute(update(filings).where(filings.c.uid == uid).values(summary_en=summary, body_en=body_en))
    row.update(summary_en=summary, body_en=body_en)
    return row


def process_overviews(limit: int = MAX_OVERVIEWS_PER_RUN) -> int:
    """Worker only: requested overviews first, then today's filings from watched companies."""
    done = 0
    with get_engine().connect() as conn:
        queued = list(conn.execute(select(enrich_queue.c.uid).order_by(enrich_queue.c.requested_at)
                                   .limit(limit)).scalars())
    for uid in queued:
        row = enrich_filing(uid)
        with get_engine().begin() as conn:
            conn.execute(delete(enrich_queue).where(enrich_queue.c.uid == uid))
        done += bool(row and row.get("summary_en"))
    pairs = watched_pairs()
    if pairs and done < limit:
        since = min(get_source(m).today() for m, _ in pairs) - timedelta(days=1)
        cond = or_(*[and_(filings.c.market == m, filings.c.ticker == t) for m, t in pairs])
        with get_engine().connect() as conn:
            recent = list(conn.execute(select(filings.c.uid).where(cond, filings.c.filed_date >= since,
                                                                   filings.c.summary_en.is_(None))
                                       .order_by(filings.c.uid.desc()).limit(limit - done)).scalars())
        for uid in recent:
            row = enrich_filing(uid)
            done += bool(row and row.get("summary_en"))
    return done


def filings_for(pairs: list[tuple[str, str]], since: date, limit: int = 300) -> list[dict]:
    if not pairs:
        return []
    cond = or_(*[and_(filings.c.market == m, filings.c.ticker == t) for m, t in pairs])
    with get_engine().connect() as conn:
        rows = conn.execute(select(filings).where(cond, filings.c.filed_date >= since)
                            .order_by(filings.c.filed_date.desc(), filings.c.uid.desc()).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def watched_pairs() -> list[tuple[str, str]]:
    with get_engine().connect() as conn:
        return [tuple(r) for r in conn.execute(select(watchlist.c.market, watchlist.c.ticker).distinct())]


def tracked_pairs() -> list[tuple[str, str]]:
    """Every company someone has added or looked up."""
    with get_engine().connect() as conn:
        return [tuple(r) for r in conn.execute(select(companies.c.market, companies.c.ticker))]


# ---- alerts ------------------------------------------------------------------------------
def notify_pending() -> int:
    """Send Telegram alerts for filings from the last day that haven't been announced yet."""
    with get_engine().connect() as conn:
        pending = [dict(r) for r in conn.execute(
            select(filings).where(filings.c.notified == False).order_by(filings.c.uid)).mappings()]  # noqa: E712
    sent = 0
    channel = get_secret("TELEGRAM_CHANNEL_ID")
    for row in pending:
        src = get_source(row["market"])
        recent = src is not None and row["filed_date"] >= src.today() - timedelta(days=1)
        if recent and telegram.enabled():
            if not row["summary_en"] and direct_fetch():
                row = enrich_filing(row["uid"]) or row
            with get_engine().connect() as conn:
                chats = set(conn.execute(
                    select(users.c.telegram_chat_id).select_from(
                        users.join(watchlist, users.c.id == watchlist.c.user_id))
                    .where(watchlist.c.market == row["market"], watchlist.c.ticker == row["ticker"],
                           watchlist.c.notify == True, users.c.is_active == True,  # noqa: E712
                           users.c.telegram_chat_id.is_not(None))).scalars())
            if channel:
                chats.add(channel)
            message = telegram.format_filing(row)
            for chat in chats:
                if telegram.send_message(chat, message):
                    sent += 1
                time.sleep(0.05)
        with get_engine().begin() as conn:
            conn.execute(update(filings).where(filings.c.uid == row["uid"]).values(notified=True))
    return sent


def run_once() -> dict:
    """One full worker cycle. Used by worker.py."""
    stats = {"listed": 0, "companies": 0, "new": 0, "summaries": 0, "errors": 0, "alerts": 0}
    try:
        stats["listed"] = refresh_listings()
    except Exception as exc:
        stats["errors"] += 1
        log.warning("listing refresh failed: %s", exc)
    for market, ticker in tracked_pairs():
        try:
            stats["new"] += sync_company(market, ticker, force=True)
            stats["companies"] += 1
        except Exception as exc:
            stats["errors"] += 1
            log.warning("sync failed for %s:%s: %s", market, ticker, exc)
    stats["summaries"] = process_overviews()
    stats["alerts"] = notify_pending()
    return stats
