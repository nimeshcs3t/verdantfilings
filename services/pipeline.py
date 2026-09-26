"""Fetch filings from regulators, store them, translate, summarize and send alerts."""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from core.config import get_secret
from core.db import as_utc, companies, filings, get_engine, users, utcnow, watchlist
from sources import Company, get_source

from . import telegram
from .summarize import summarize
from .translate import translate_text, translate_title

log = logging.getLogger(__name__)
SYNC_INTERVAL = timedelta(minutes=10)


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
    comp = src.resolve(ticker)
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


def sync_company(market: str, ticker: str, force: bool = False) -> int:
    """Pull recent filings for one company. Returns the number of new filings stored."""
    comp = get_company(market, ticker)
    if comp is None:
        return 0
    last = as_utc(comp.get("last_synced"))
    if not force and last and utcnow() - last < SYNC_INTERVAL:
        return 0
    src = get_source(market)
    today = src.today()
    start = today - timedelta(days=7 if last else 90)
    company = Company(comp["market"], comp["ticker"], comp["source_id"], comp["name_local"], comp["name_en"])
    found = src.list_filings(company, start, today)

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
                   title_en=translate_title(f.title_local, src.source_lang), filer=f.filer, url=f.url,
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


def enrich_filing(uid: str, force: bool = False) -> dict | None:
    """Download the document, translate the opening part, and write an overview."""
    row = get_filing(uid)
    if row is None or (row["summary_en"] and not force):
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
            if not row["summary_en"]:
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
    """One full cycle: sync every watched company, then alert. Used by worker.py and the admin page."""
    stats = {"companies": 0, "new": 0, "errors": 0, "alerts": 0}
    for market, ticker in watched_pairs():
        try:
            stats["new"] += sync_company(market, ticker, force=True)
            stats["companies"] += 1
        except Exception as exc:
            stats["errors"] += 1
            log.warning("sync failed for %s:%s: %s", market, ticker, exc)
    stats["alerts"] = notify_pending()
    return stats
