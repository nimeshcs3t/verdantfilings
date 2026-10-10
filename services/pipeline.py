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
from core.db import (as_utc, companies, enrich_queue, filing_flags, filing_texts, filings, get_engine, listed_companies,
                     users, utcnow, watchlist)
from sources import Company, SourceBusy, configured_sources, get_source

from . import telegram
from .summarize import summarize
from .translate import glossary_or_cached, looks_english, mostly_english, translate_lines, translate_text, translate_title

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
        try:
            rows = [dict(market=c.market, ticker=c.ticker, source_id=c.source_id, name_local=c.name_local,
                         name_en=c.name_en, updated_at=now) for c in src.all_listed()]
        except Exception as exc:          # one market's list failing mustn't stop the others
            log.warning("company list failed for %s: %s", src.market, exc)
            continue
        if not rows:
            continue
        with get_engine().connect() as conn:
            old = {t for (t,) in conn.execute(select(listed_companies.c.ticker).where(listed_companies.c.market == src.market))}
        with get_engine().begin() as conn:
            conn.execute(delete(listed_companies).where(listed_companies.c.market == src.market))
            conn.execute(insert(listed_companies), rows)
        try:
            from .signals import record_new
            record_new(src.market, old, rows)
        except Exception as exc:
            log.warning("new-listing check failed for %s: %s", src.market, exc)
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
    if direct_fetch() or src.resolve_in_app:
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
    comp = src.resolve(ticker) if (direct_fetch() or src.resolve_in_app) else _listed(market, ticker)
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
    fresh = [f for f in found if f.uid not in existing]
    english = {f.uid: f.title_en or (f.title_local if looks_english(f.title_local) else None)
               or glossary_or_cached(f.title_local, src.source_lang) for f in fresh}
    todo = [f for f in fresh if not english[f.uid]]
    for i in range(0, len(todo), 40):          # translate in batches: fewer requests to the translators
        batch = todo[i:i + 40]
        for f, en in zip(batch, translate_lines([f.title_local for f in batch], src.source_lang)):
            english[f.uid] = en
    new = 0
    for f in fresh:
        row = dict(uid=f.uid, market=f.market, ticker=f.ticker, company_name=comp["name_en"] or f.company_name,
                   filed_date=f.filed_date, title_local=f.title_local,
                   title_en=english[f.uid] or f.title_local, filer=f.filer, url=f.url,
                   notified=False, created_at=utcnow())
        try:
            with get_engine().begin() as conn:
                conn.execute(insert(filings).values(**row))
                if f.price_sensitive:
                    conn.execute(insert(filing_flags).values(uid=f.uid, price_sensitive=True))
                if f.body:
                    conn.execute(insert(filing_texts).values(uid=f.uid, body=f.body[:60000]))
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
    with get_engine().connect() as conn:
        text = conn.execute(select(filing_texts.c.body).where(filing_texts.c.uid == uid)).scalar() or ""
    if not text:
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
    body_en = text[:limit] if src.source_lang != "en" and mostly_english(text) else translate_text(text[:limit], src.source_lang)
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


def price_sensitive_uids(uids: list[str]) -> set[str]:
    if not uids:
        return set()
    with get_engine().connect() as conn:
        return set(conn.execute(select(filing_flags.c.uid).where(
            filing_flags.c.uid.in_(uids), filing_flags.c.price_sensitive == True)).scalars())  # noqa: E712


def filings_for(pairs: list[tuple[str, str]], since: date, limit: int = 300) -> list[dict]:
    if not pairs:
        return []
    cond = or_(*[and_(filings.c.market == m, filings.c.ticker == t) for m, t in pairs])
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(select(filings).where(cond, filings.c.filed_date >= since)
                .order_by(filings.c.filed_date.desc(), filings.c.uid.desc()).limit(limit)).mappings().all()]
    flagged = price_sensitive_uids([r["uid"] for r in rows])
    for r in rows:
        r["price_sensitive"] = r["uid"] in flagged
    return rows


def watched_pairs() -> list[tuple[str, str]]:
    with get_engine().connect() as conn:
        return [tuple(r) for r in conn.execute(select(watchlist.c.market, watchlist.c.ticker).distinct())]


def tracked_pairs() -> list[tuple[str, str]]:
    """Every company someone has added or looked up."""
    with get_engine().connect() as conn:
        return [tuple(r) for r in conn.execute(select(companies.c.market, companies.c.ticker))]


# ---- alerts ------------------------------------------------------------------------------
def notify_pending() -> int:
    from .alerts import notify_pending as instant
    return instant()


def retranslate_titles(limit: int = 200) -> int:
    """Worker only: translate titles that were stored untranslated because every translator refused."""
    fixed = 0
    for src in configured_sources():
        if src.source_lang in ("en", "auto"):
            continue
        with get_engine().connect() as conn:
            rows = conn.execute(select(filings.c.uid, filings.c.title_local).where(
                filings.c.market == src.market, filings.c.title_en == filings.c.title_local)
                .order_by(filings.c.filed_date.desc()).limit(limit)).all()
        if not rows:
            continue
        rows = [r for r in rows if not looks_english(r.title_local)]
        if not rows:
            continue
        titles = [r.title_local for r in rows]
        english = []
        for i in range(0, len(titles), 40):
            english += translate_lines(titles[i:i + 40], src.source_lang)
        with get_engine().begin() as conn:
            for r, en in zip(rows, english):
                if en and en != r.title_local:
                    conn.execute(update(filings).where(filings.c.uid == r.uid).values(title_en=en))
                    fixed += 1
    return fixed


def _refresh_prices() -> int:
    from .prices import refresh_cache
    return refresh_cache()


def _fair_value_alerts() -> int:
    from . import deliver, fairvalue, portfolio
    return sum(deliver.send(uid, "Fair value alert", text) for uid, text in fairvalue.check(portfolio.price_history))


def _due(name: str, minutes: int) -> bool:
    """True (and remembered) if `name` hasn't run in the last `minutes` minutes."""
    from datetime import datetime, timezone
    from core.usage import get_state, set_state
    now = datetime.now(timezone.utc)
    last = get_state(name)
    try:
        if last and (now - datetime.fromisoformat(last)).total_seconds() < minutes * 60 - 30:
            return False
    except ValueError:
        pass
    set_state(name, now.isoformat())
    return True


class _WarningCollector(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage()[:300])


def run_once() -> dict:
    """One full worker cycle. Used by worker.py and the admin page."""
    started = time.time()
    collector = _WarningCollector()
    logging.getLogger().addHandler(collector)
    stats = {"listed": 0, "companies": 0, "new": 0, "summaries": 0, "errors": 0, "alerts": 0}
    try:
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
        from . import bot, briefs, digests, events, financials, housekeeping, insiders, ipos, journal, logos, notes, portfolio, signals
        from .alerts import run_alerts
        # (stats key, job, minimum minutes between runs; 0 = every run)
        steps = [("retranslated", retranslate_titles, 0), ("summaries", process_overviews, 0), (None, run_alerts, 0),
                 ("commands", bot.process_updates, 0), ("price_alerts", portfolio.check_alerts, 0),
                 ("events", events.refresh, 30), ("insiders", insiders.refresh, 60),
                 ("financials", financials.refresh, 360), ("briefs", briefs.refresh, 360), ("logos", logos.refresh, 360),
                 ("fair_value", _fair_value_alerts, 30), ("journal", journal.send_reminders, 60),
                 ("report_notes", notes.refresh, 60), ("insider_alerts", signals.send_insider_alerts, 60),
                 ("new_listings", signals.send_new_listing_alerts, 360), ("morning", digests.morning_briefs, 0),
                 ("monthly", digests.monthly_reports, 0), ("ipos", ipos.refresh, 240), ("ipo_details", ipos.enrich, 30), ("prices", _refresh_prices, 60), ("ipo_alerts", ipos.send_alerts, 30)]
        for key, step, every in steps:
            if every and not _due(f"job:{key}", every):
                continue
            try:
                result = step()
                if key:
                    stats[key] = result
                else:
                    stats.update(result)
            except Exception as exc:
                log.warning("%s failed: %s", key or "alerts", exc)
        try:
            cleaned = housekeeping.cleanup()
            stats["cleaned"] = sum(cleaned.values()) if cleaned else 0
        except Exception as exc:
            log.warning("clean-up failed: %s", exc)
    finally:
        logging.getLogger().removeHandler(collector)
    try:
        from .housekeeping import log_run
        log_run(time.time() - started, stats, collector.messages)
    except Exception:
        pass
    stats["warnings"] = len(collector.messages)
    return stats
