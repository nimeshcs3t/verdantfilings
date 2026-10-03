"""Company logos where a free source exists; the letter tile stays as the fallback underneath."""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from sqlalchemy import and_, delete, insert, select

from core.db import as_utc, companies, company_logos, company_sites, get_engine, holdings, transactions, utcnow, watchlist
from sources import get_source

log = logging.getLogger(__name__)
RECHECK = timedelta(days=30)


def favicon(website: str | None) -> str | None:
    domain = re.sub(r"^https?://", "", (website or "").strip().lower()).split("/")[0]
    domain = domain.removeprefix("www.")
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=64" if "." in domain else None


def _find(market: str, ticker: str, source_id: str) -> str | None:
    if market == "US":
        return f"https://financialmodelingprep.com/image-stock/{ticker}.png"
    if market == "KR":
        src = get_source("KR")
        data = src._json("company.json", corp_code=source_id) if src and src.is_configured() else {}
        return favicon(data.get("hm_url")) if data.get("status") == "000" else None
    if market == "IL":
        src = get_source("IL")
        return favicon(getattr(src, "websites", {}).get(source_id))
    return None


def refresh(limit: int = 25) -> int:
    """Worker: find logos (and websites) for watched and held companies that don't have them yet."""
    try:
        refresh_sites(limit)
    except Exception as exc:
        log.warning("website refresh failed: %s", exc)
    with get_engine().connect() as conn:
        pairs = {tuple(r) for r in conn.execute(select(watchlist.c.market, watchlist.c.ticker).distinct())}
        pairs |= {tuple(r) for r in conn.execute(select(transactions.c.market, transactions.c.ticker).distinct())}
        known = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(select(company_logos.c.market, company_logos.c.ticker,
                                                                         company_logos.c.url, company_logos.c.checked_at))}
        ids = {(r[0], r[1]): r[2] for r in conn.execute(select(companies.c.market, companies.c.ticker, companies.c.source_id))}
    due = [p for p in pairs if p in ids and (p not in known or (not known[p][0] and utcnow() - as_utc(known[p][1]) > RECHECK))]
    done = 0
    for market, ticker in sorted(due)[:limit]:
        try:
            url = _find(market, ticker, ids[(market, ticker)])
        except Exception as exc:
            log.warning("logo lookup failed for %s:%s: %s", market, ticker, exc)
            continue
        if url is None and market == "IL" and (market, ticker) not in known:
            continue        # MAYA website not seen yet; try again after the next report sync
        with get_engine().begin() as conn:
            conn.execute(delete(company_logos).where(company_logos.c.market == market, company_logos.c.ticker == ticker))
            conn.execute(insert(company_logos).values(market=market, ticker=ticker, url=url, checked_at=utcnow()))
        done += 1
    return done


def all_logos() -> dict[tuple[str, str], str]:
    with get_engine().connect() as conn:
        return {(r[0], r[1]): r[2] for r in conn.execute(select(company_logos.c.market, company_logos.c.ticker,
                                                                company_logos.c.url)) if r[2]}


# ---- websites ----------------------------------------------------------------------------------
def _site(market: str, ticker: str, source_id: str) -> str | None:
    src = get_source(market)
    if market == "KR":
        data = src._json("company.json", corp_code=source_id) if src and src.is_configured() else {}
        return (data.get("hm_url") or None) if data.get("status") == "000" else None
    if market == "TW":
        return getattr(src, "websites", {}).get(source_id)
    if market in ("US", "IL", "PL"):
        return getattr(src, "websites", {}).get(str(int(source_id)) if market == "US" else source_id)
    return None


def _normal(url: str | None) -> str | None:
    url = (url or "").strip()
    if not url or "." not in url:
        return None
    return url if re.match(r"^https?://", url, re.I) else "https://" + url.lstrip("/")


def refresh_sites(limit: int = 25) -> int:
    """Worker: remember websites for watched and held companies (from data the sources already saw)."""
    with get_engine().connect() as conn:
        pairs = {tuple(r) for r in conn.execute(select(watchlist.c.market, watchlist.c.ticker).distinct())}
        pairs |= {tuple(r) for r in conn.execute(select(transactions.c.market, transactions.c.ticker).distinct())}
        known = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(select(company_sites.c.market, company_sites.c.ticker,
                                                                         company_sites.c.url, company_sites.c.checked_at))}
        ids = {(r[0], r[1]): r[2] for r in conn.execute(select(companies.c.market, companies.c.ticker, companies.c.source_id))}
    due = [p for p in pairs if p in ids and (p not in known or (not known[p][0] and utcnow() - as_utc(known[p][1]) > timedelta(days=7)))]
    done = 0
    for market, ticker in sorted(due)[:limit]:
        try:
            url = _normal(_site(market, ticker, ids[(market, ticker)]))
        except Exception as exc:
            log.warning("website lookup failed for %s:%s: %s", market, ticker, exc)
            continue
        if url is None and market in ("US", "IL", "PL") and (market, ticker) not in known:
            continue        # not seen yet; the next filings sync will pass it along
        with get_engine().begin() as conn:
            conn.execute(delete(company_sites).where(company_sites.c.market == market, company_sites.c.ticker == ticker))
            conn.execute(insert(company_sites).values(market=market, ticker=ticker, url=url, checked_at=utcnow()))
        done += 1
    return done


def website(market: str, ticker: str) -> str | None:
    with get_engine().connect() as conn:
        return conn.execute(select(company_sites.c.url).where(company_sites.c.market == market,
                                                              company_sites.c.ticker == ticker)).scalar()
