"""A short AI-written summary of each watched company's recent filings, refreshed weekly (needs Gemini/Claude)."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import and_, select

from core.config import get_secret
from core.db import as_utc, company_meta, companies, filings, get_engine, utcnow, watchlist

from .financials import touch_meta

log = logging.getLogger(__name__)
PROMPT = """Write a short brief (3 to 5 sentences) on what this listed company has disclosed recently, based only on
the filings below. Lead with the most important developments (results, deals, capital changes, big holders,
management changes), give key figures and dates, and mention if things are quiet. Plain English, no headings,
no investment advice, no speculation.

Company: {company}
Recent filings, newest first:
{items}"""


def refresh(limit: int = 3) -> int:
    if not (get_secret("GEMINI_API_KEY") or get_secret("ANTHROPIC_API_KEY")):
        return 0
    from .summarize import _anthropic, _gemini
    with get_engine().connect() as conn:
        rows = conn.execute(select(companies.c.market, companies.c.ticker, companies.c.name_en, company_meta.c.brief_updated)
                            .select_from(companies.join(watchlist, and_(watchlist.c.market == companies.c.market,
                                                                        watchlist.c.ticker == companies.c.ticker))
                                         .outerjoin(company_meta, and_(company_meta.c.market == companies.c.market,
                                                                       company_meta.c.ticker == companies.c.ticker)))
                            .distinct()).all()
    due = sorted((r for r in rows if not r[3] or utcnow() - as_utc(r[3]) > timedelta(days=7)),
                 key=lambda r: as_utc(r[3]) if r[3] else utcnow() - timedelta(days=9999))[:limit]
    done = 0
    for market, ticker, name, _ in due:
        with get_engine().connect() as conn:
            items = conn.execute(select(filings.c.filed_date, filings.c.title_en, filings.c.summary_en).where(
                filings.c.market == market, filings.c.ticker == ticker,
                filings.c.filed_date >= date.today() - timedelta(days=90)).order_by(filings.c.filed_date.desc()).limit(25)).all()
        if not items:
            touch_meta(market, ticker, brief="No filings in the last 90 days.", brief_updated=utcnow())
            continue
        text = "\n".join(f"- {d}: {t}" + (f" | {(s or '').replace(chr(10), ' ')[:400]}" if s else "") for d, t, s in items)
        brief = None
        for provider in (_gemini, _anthropic):
            try:
                brief = provider(PROMPT.format(company=name, items=text))
                if brief:
                    break
            except Exception:
                continue
        if brief:
            touch_meta(market, ticker, brief=brief.strip(), brief_updated=utcnow())
            done += 1
    return done


def get(market: str, ticker: str) -> tuple[str | None, object]:
    with get_engine().connect() as conn:
        row = conn.execute(select(company_meta.c.brief, company_meta.c.brief_updated).where(
            company_meta.c.market == market, company_meta.c.ticker == ticker)).first()
    return (row[0], row[1]) if row else (None, None)
