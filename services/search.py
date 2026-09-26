"""Search across all stored filings."""
from __future__ import annotations

import csv
import io
from datetime import date

from sqlalchemy import or_, select

from core.db import filings, get_engine

from .classify import categorize, label
from .pipeline import price_sensitive_uids


def search(text: str = "", market: str | None = None, since: date | None = None,
           uids: set[str] | None = None, limit: int = 300) -> list[dict]:
    query = select(filings)
    words = [w for w in (text or "").split() if w][:6]
    for w in words:
        like = f"%{w}%"
        query = query.where(or_(filings.c.title_en.ilike(like), filings.c.title_local.ilike(like),
                                filings.c.summary_en.ilike(like), filings.c.company_name.ilike(like),
                                filings.c.ticker.ilike(like)))
    if market:
        query = query.where(filings.c.market == market)
    if since:
        query = query.where(filings.c.filed_date >= since)
    if uids is not None:
        if not uids:
            return []
        query = query.where(filings.c.uid.in_(list(uids)))
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(query.order_by(filings.c.filed_date.desc(), filings.c.uid.desc())
                                              .limit(limit)).mappings()]
    flagged = price_sensitive_uids([r["uid"] for r in rows])
    for r in rows:
        r["price_sensitive"] = r["uid"] in flagged
        r["category"] = categorize(r["title_en"], r["title_local"], r["price_sensitive"])
    return rows


def to_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Market", "Ticker", "Company", "Category", "Title (English)", "Title (original)",
                "Price sensitive", "Overview", "Link"])
    for r in rows:
        w.writerow([r["filed_date"], r["market"], r["ticker"], r["company_name"], label(r["category"]), r["title_en"],
                    r["title_local"], "yes" if r.get("price_sensitive") else "", (r.get("summary_en") or "").replace("\n", " "),
                    r["url"]])
    return out.getvalue()
