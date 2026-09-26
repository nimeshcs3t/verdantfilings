"""Insider trades: US Form 4 filings (parsed) and Korean executive / major shareholder reports (DART API).
Filled by the worker; the website reads the insider_tx table."""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from sqlalchemy import and_, delete, insert, select
from sqlalchemy.exc import IntegrityError

from core.db import as_utc, companies, company_meta, filings, get_engine, insider_tx, utcnow, watchlist
from sources import get_source

from .financials import touch_meta

log = logging.getLogger(__name__)
CODE_LABEL = {"P": "Bought", "S": "Sold", "A": "Granted", "M": "Option exercise", "F": "Tax withholding", "G": "Gift",
              "D": "Returned", "C": "Conversion", "X": "Exercise", "J": "Other", "H": "Holding", "+": "Increased",
              "-": "Decreased"}
KR_ROLES = {"대표이사": "CEO", "사장": "President", "부사장": "Vice president", "전무": "Senior executive VP",
            "상무": "Executive VP", "사내이사": "Director", "사외이사": "Outside director", "이사": "Director",
            "감사": "Auditor", "회장": "Chairman", "부회장": "Vice chairman", "미등기임원": "Non-registered officer"}


def _store(uid: str, market: str, ticker: str, rows: list[dict]) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(insider_tx).where(insider_tx.c.uid == uid))
        if not rows:   # remember that this filing was read, even with no transactions
            conn.execute(insert(insider_tx).values(uid=uid, seq=-1, market=market, ticker=ticker, code="-0"))
        for i, r in enumerate(rows):
            try:
                tx_date = date.fromisoformat(r["date"]) if r.get("date") else None
            except ValueError:
                tx_date = None
            conn.execute(insert(insider_tx).values(uid=uid, seq=i, market=market, ticker=ticker, person=r.get("person"),
                                                   role=r.get("role"), tx_date=tx_date, code=r.get("code"),
                                                   shares=r.get("shares"), price=r.get("price"), after=r.get("after")))


def _us(limit: int) -> int:
    from sources.us_edgar import parse_ownership
    src = get_source("US")
    if not src or not src.is_configured():
        return 0
    since = src.today() - timedelta(days=180)
    with get_engine().connect() as conn:
        watched = {tuple(r) for r in conn.execute(select(watchlist.c.market, watchlist.c.ticker).distinct())}
        tickers = [t for m, t in watched if m == "US"]
        if not tickers:
            return 0
        done_uids = select(insider_tx.c.uid)
        todo = conn.execute(select(filings.c.uid, filings.c.ticker).where(
            filings.c.market == "US", filings.c.ticker.in_(tickers), filings.c.filed_date >= since,
            filings.c.title_en.like("%insider%"), filings.c.uid.not_in(done_uids))
            .order_by(filings.c.filed_date.desc()).limit(limit)).all()
    n = 0
    for uid, ticker in todo:
        try:
            text = src.fetch_submission(uid)
            doc = re.search(r"<ownershipDocument>.*?</ownershipDocument>", text, re.S)
            _store(uid, "US", ticker, parse_ownership(doc.group(0)) if doc else [])
            n += 1
        except Exception as exc:
            log.warning("form 4 read failed for %s: %s", uid, exc)
    return n


def _kr(limit: int) -> int:
    src = get_source("KR")
    if not src or not src.is_configured():
        return 0
    with get_engine().connect() as conn:
        rows = conn.execute(select(companies.c.ticker, companies.c.source_id, company_meta.c.insider_updated)
                            .select_from(companies.join(watchlist, and_(watchlist.c.market == companies.c.market,
                                                                        watchlist.c.ticker == companies.c.ticker))
                                         .outerjoin(company_meta, and_(company_meta.c.market == companies.c.market,
                                                                       company_meta.c.ticker == companies.c.ticker)))
                            .where(companies.c.market == "KR").distinct()).all()
    due = [r for r in rows if not r[2] or utcnow() - as_utc(r[2]) > timedelta(days=1)][:limit]
    n = 0
    for ticker, corp_code, _ in due:
        try:
            data = src._json("elestock.json", corp_code=corp_code)
        except Exception as exc:
            log.warning("insider list failed for KR:%s: %s", ticker, exc)
            touch_meta("KR", ticker, insider_updated=utcnow())   # retry tomorrow
            continue
        cutoff = src.today() - timedelta(days=365)
        for it in data.get("list", []) if data.get("status") == "000" else []:
            try:
                day = date(int(it["rcept_dt"][:4]), int(it["rcept_dt"][4:6]), int(it["rcept_dt"][6:8]))
            except (KeyError, ValueError):
                continue
            if day < cutoff:
                continue
            change = _num(it.get("sp_stock_lmp_irds_cnt"))
            position = (it.get("isu_exctv_ofcps") or "").strip()
            role = KR_ROLES.get(position, position) or ("Major shareholder" if it.get("isu_main_shrholdr") else "")
            _store(f"KR:{it['rcept_no']}", "KR", ticker, [{
                "person": it.get("repror", ""), "role": role, "date": day.isoformat(),
                "code": "+" if (change or 0) > 0 else "-" if (change or 0) < 0 else "H",
                "shares": change, "price": None, "after": _num(it.get("sp_stock_lmp_cnt"))}])
        touch_meta("KR", ticker, insider_updated=utcnow())
        n += 1
    return n


def _num(text) -> float | None:
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def refresh(limit: int = 25) -> int:
    return _us(limit) + _kr(max(3, limit // 5))


def recent(market: str, ticker: str, days: int = 180) -> list[dict]:
    since = date.today() - timedelta(days=days)
    with get_engine().connect() as conn:
        rows = conn.execute(select(insider_tx).where(insider_tx.c.market == market, insider_tx.c.ticker == ticker,
                                                     insider_tx.c.seq >= 0, insider_tx.c.tx_date >= since)
                            .order_by(insider_tx.c.tx_date.desc(), insider_tx.c.uid.desc())).mappings().all()
    return [dict(r) for r in rows]


def summary(rows: list[dict]) -> dict:
    bought = sum(r["shares"] or 0 for r in rows if r["code"] in ("P", "+") and (r["shares"] or 0) > 0)
    sold = sum(abs(r["shares"] or 0) for r in rows if r["code"] in ("S", "-"))
    buyers = {r["person"] for r in rows if r["code"] in ("P", "+")}
    sellers = {r["person"] for r in rows if r["code"] in ("S", "-")}
    value_bought = sum((r["shares"] or 0) * (r["price"] or 0) for r in rows if r["code"] == "P")
    value_sold = sum((r["shares"] or 0) * (r["price"] or 0) for r in rows if r["code"] == "S")
    return {"bought": bought, "sold": sold, "buyers": len(buyers), "sellers": len(sellers),
            "value_bought": value_bought, "value_sold": value_sold}
