"""AI notes on reports: 'What changed' versus the company's previous report of the same kind, and 'Highlights'
(key figures and outlook) for annual and half-year reports. Needs Gemini or Claude."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import and_, insert, or_, select

from core.config import get_secret
from core.db import filing_notes, filings, get_engine, watchlist

from .classify import categorize

log = logging.getLogger(__name__)
CHANGES = """Compare a company's new report with its previous report of the same kind. In 3 to 6 short bullet points,
say what changed: figures that moved notably (give both numbers), new or dropped risks, changes in guidance or
outlook, new projects, deals or management changes, and wording that became more or less confident. Use only the
two texts. No investment advice. Start each bullet with "- ".

Company: {company}
Previous report ({prev_date}): {prev_title}
{prev_text}

New report ({new_date}): {new_title}
{new_text}"""
HIGHLIGHTS = """From this annual or half-year report, list in 4 to 7 short bullet points: revenue, profit and margins
(with the change from last year if stated), cash and debt, dividends, the management's outlook or guidance, and the
main risks mentioned. Use only the text and give figures exactly. No investment advice. Start each bullet with "- ".

Company: {company}
Report: {title}
{text}"""


def get(uid: str) -> dict[str, str]:
    with get_engine().connect() as conn:
        return {k: b for k, b in conn.execute(select(filing_notes.c.kind, filing_notes.c.body).where(filing_notes.c.uid == uid))}


def _llm(prompt: str) -> str | None:
    from .summarize import _anthropic, _gemini
    for provider in (_gemini, _anthropic):
        try:
            out = provider(prompt)
            if out:
                return out.strip()
        except Exception:
            continue
    return None


def _save(uid: str, kind: str, body: str) -> None:
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(filing_notes).values(uid=uid, kind=kind, body=body[:6000]))
    except Exception:
        pass


def refresh(limit: int = 3) -> int:
    """Worker: write notes for new reports of watched companies (needs an AI key and an overview already done)."""
    if not (get_secret("GEMINI_API_KEY") or get_secret("ANTHROPIC_API_KEY")):
        return 0
    with get_engine().connect() as conn:
        watched = [tuple(r) for r in conn.execute(select(watchlist.c.market, watchlist.c.ticker).distinct())]
        if not watched:
            return 0
        cond = or_(*[and_(filings.c.market == m, filings.c.ticker == t) for m, t in watched])
        rows = [dict(r) for r in conn.execute(select(filings).where(
            cond, filings.c.body_en.is_not(None), filings.c.filed_date >= date.today() - timedelta(days=60),
            filings.c.uid.not_in(select(filing_notes.c.uid))).order_by(filings.c.filed_date.desc()).limit(40)).mappings()]
    done = 0
    for row in rows:
        if done >= limit:
            break
        cat = categorize(row["title_en"], row["title_local"])
        if cat not in ("periodic", "earnings"):
            continue
        with get_engine().connect() as conn:
            prev = conn.execute(select(filings).where(
                filings.c.market == row["market"], filings.c.ticker == row["ticker"], filings.c.body_en.is_not(None),
                filings.c.filed_date < row["filed_date"]).order_by(filings.c.filed_date.desc()).limit(20)).mappings().all()
        prev = next((dict(p) for p in prev if categorize(p["title_en"], p["title_local"]) == cat), None)
        if prev:
            note = _llm(CHANGES.format(company=row["company_name"], prev_date=prev["filed_date"], prev_title=prev["title_en"],
                                       prev_text=(prev["body_en"] or "")[:5000], new_date=row["filed_date"],
                                       new_title=row["title_en"], new_text=(row["body_en"] or "")[:5000]))
            if note:
                _save(row["uid"], "changes", note)
        if cat == "periodic" or "annual" in (row["title_en"] or "").lower() or "half" in (row["title_en"] or "").lower():
            note = _llm(HIGHLIGHTS.format(company=row["company_name"], title=row["title_en"], text=(row["body_en"] or "")[:9000]))
            if note:
                _save(row["uid"], "highlights", note)
        _save(row["uid"], "checked", "")
        done += 1
    return done
