"""US stock screener data: every US-listed stock's price, market cap and sector (Nasdaq's public screener, one
request) joined with key figures from SEC XBRL 'frames' (one file per figure and period covering all filers).

Refreshed by the IPO calendar job: prices twice a day, SEC figures weekly. No AI, no keys."""
from __future__ import annotations

import logging
import re
from datetime import date

from sqlalchemy import delete, insert, select, update

from core.config import get_secret
from core.db import get_engine, listed_companies, us_stocks, utcnow

log = logging.getLogger(__name__)
NASDAQ = "https://api.nasdaq.com/api/screener/stocks"
FRAMES = "https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/{period}.json"
REVENUE_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"]
CAP_BANDS = {"Mega (over $200B)": (200e9, None), "Large ($10B–200B)": (10e9, 200e9), "Mid ($2B–10B)": (2e9, 10e9),
             "Small ($300M–2B)": (300e6, 2e9), "Micro (under $300M)": (None, 300e6)}


def _num(text) -> float | None:
    t = re.sub(r"[^\d.\-]", "", str(text or ""))
    try:
        return float(t) if t not in ("", "-", ".") else None
    except ValueError:
        return None


def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/140.0.0.0 Safari/537.36", "Accept": "application/json, text/plain, */*",
                      "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"})
    return s


def fetch_listing() -> list[dict]:
    """All US stocks from Nasdaq's screener: symbol, name, price, % change, market cap, sector, industry, country."""
    r = _session().get(NASDAQ, params={"tableonly": "true", "limit": "25", "offset": "0", "download": "true"}, timeout=60)
    r.raise_for_status()
    data = r.json().get("data") or {}
    rows = data.get("rows") or (data.get("table") or {}).get("rows") or []
    out = []
    for row in rows:
        sym = str(row.get("symbol") or "").strip().upper()
        if not sym or "^" in sym or "/" in sym:
            continue
        out.append({"ticker": sym.replace(".", "-")[:16], "name": re.sub(r"\s+(Common Stock|Ordinary Shares|Class [A-C] .*|"
                                                                          r"American Depositary Shares.*)$", "",
                                                                          str(row.get("name") or "").strip())[:200],
                    "price": _num(row.get("lastsale")), "change_pct": _num(row.get("pctchange")),
                    "market_cap": _num(row.get("marketCap")), "sector": (row.get("sector") or "").strip()[:80] or None,
                    "industry": (row.get("industry") or "").strip()[:120] or None,
                    "country": (row.get("country") or "").strip()[:60] or None})
    return out


def refresh_prices() -> int:
    """Upsert every stock's latest price, market cap and sector."""
    rows = fetch_listing()
    if len(rows) < 1000:
        raise RuntimeError(f"Nasdaq screener returned only {len(rows)} stocks")
    with get_engine().connect() as conn:
        ciks = {t: c for t, c in conn.execute(select(listed_companies.c.ticker, listed_companies.c.source_id)
                                               .where(listed_companies.c.market == "US"))}
        known = set(conn.execute(select(us_stocks.c.ticker)).scalars())
    now = utcnow()
    with get_engine().begin() as conn:
        new = []
        for r in rows:
            values = {**r, "prices_at": now}
            cik = ciks.get(r["ticker"]) or ciks.get(r["ticker"].replace("-", "."))
            if r["ticker"] in known:
                if cik:
                    values["cik"] = str(cik)
                conn.execute(update(us_stocks).where(us_stocks.c.ticker == r["ticker"]).values(**values))
            else:
                new.append({**values, "cik": str(cik) if cik else None})   # same keys on every row for a bulk insert
        if new:
            conn.execute(insert(us_stocks), new)
        listed = {r["ticker"] for r in rows}
        gone = known - listed
        if gone and len(gone) < len(known) * 0.2:        # delisted; never wipe the table on a partial reply
            conn.execute(delete(us_stocks).where(us_stocks.c.ticker.in_(list(gone))))
    return len(rows)


def _frame(session, tag: str, period: str) -> dict[str, float]:
    """cik -> value for one XBRL concept and calendar period, for every filer."""
    r = session.get(FRAMES.format(tag=tag, period=period), timeout=120)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return {str(int(d["cik"])): float(d["val"]) for d in r.json().get("data", []) if d.get("cik") is not None and d.get("val") is not None}


def _revenue(session, period: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for tag in REVENUE_TAGS:                 # companies use different revenue concepts: first one found wins
        for cik, v in _frame(session, tag, period).items():
            out.setdefault(cik, v)
    return out


def refresh_figures(today: date | None = None) -> dict:
    """Weekly: revenue (latest two fiscal years and latest quarter vs a year earlier), net income, operating income
    and equity for every SEC filer, matched to tickers by CIK."""
    import time
    today = today or date.today()
    s = _session()
    s.headers.update({"User-Agent": f"VerdantFilings {get_secret('SEC_CONTACT_EMAIL', 'admin@example.com')}",
                      "Origin": "", "Referer": ""})
    year = today.year - 1
    rev = _revenue(s, f"CY{year}")
    if len(rev) < 2500:                      # early in the year most annual reports aren't in yet
        year -= 1
        rev = _revenue(s, f"CY{year}")
    rev_prev = _revenue(s, f"CY{year - 1}")
    time.sleep(0.3)
    net = _frame(s, "NetIncomeLoss", f"CY{year}")
    op = _frame(s, "OperatingIncomeLoss", f"CY{year}")
    equity = _frame(s, "StockholdersEquity", f"CY{year}Q4I")
    quarter, rev_q, rev_q_prev = None, {}, {}
    for y, q in ((today.year, 3), (today.year, 2), (today.year, 1), (today.year - 1, 4), (today.year - 1, 3)):
        if date(y, q * 3, 1) > today:
            continue
        got = _revenue(s, f"CY{y}Q{q}")
        if len(got) >= 2000:
            quarter, rev_q, rev_q_prev = f"Q{q} {y}", got, _revenue(s, f"CY{y - 1}Q{q}")
            break
    with get_engine().connect() as conn:
        stocks = conn.execute(select(us_stocks.c.ticker, us_stocks.c.cik).where(us_stocks.c.cik.is_not(None))).all()
    now, matched = utcnow(), 0
    with get_engine().begin() as conn:
        for ticker, cik in stocks:
            cik = str(int(cik))
            values = {"revenue": rev.get(cik), "revenue_prev": rev_prev.get(cik), "net_income": net.get(cik),
                      "op_income": op.get(cik), "equity": equity.get(cik), "rev_q": rev_q.get(cik),
                      "rev_q_prev": rev_q_prev.get(cik), "fiscal_year": year, "quarter": quarter, "figures_at": now}
            if any(values[k] is not None for k in ("revenue", "net_income", "equity")):
                matched += 1
            conn.execute(update(us_stocks).where(us_stocks.c.ticker == ticker).values(**values))
    return {"year": year, "quarter": quarter, "filers": len(rev), "matched": matched}


def ratios(r: dict) -> dict:
    """P/E, P/S, P/B, growth and margins from a us_stocks row (None where not meaningful)."""
    cap, rev, net = r.get("market_cap"), r.get("revenue"), r.get("net_income")
    out = {"pe": cap / net if cap and net and net > 0 else None, "ps": cap / rev if cap and rev and rev > 0 else None,
           "pb": cap / r["equity"] if cap and r.get("equity") and r["equity"] > 0 else None,
           "growth": rev / r["revenue_prev"] - 1 if rev and r.get("revenue_prev") and r["revenue_prev"] > 0 else None,
           "growth_q": r["rev_q"] / r["rev_q_prev"] - 1 if r.get("rev_q") and r.get("rev_q_prev") and r["rev_q_prev"] > 0 else None,
           "net_margin": net / rev if net is not None and rev and rev > 0 else None,
           "op_margin": r["op_income"] / rev if r.get("op_income") is not None and rev and rev > 0 else None,
           "price": r.get("price"), "market_cap": cap}
    return out


def rows() -> list[dict]:
    with get_engine().connect() as conn:
        data = [dict(r) for r in conn.execute(select(us_stocks)).mappings()]
    for r in data:
        r.update({k: v for k, v in ratios(r).items() if k not in ("price", "market_cap")})
    return data


def screen(data: list[dict], sectors=None, bands=None, pe_max=None, ps_max=None, growth_min=None, net_margin_min=None,
           op_margin_min=None, profitable=False, text="") -> list[dict]:
    def in_band(cap):
        if not bands:
            return True
        return any((lo is None or (cap or 0) >= lo) and (hi is None or (cap or 0) < hi) for lo, hi in (CAP_BANDS[b] for b in bands))
    t = text.strip().lower()
    out = []
    for r in data:
        if sectors and r.get("sector") not in sectors:
            continue
        if not in_band(r.get("market_cap")):
            continue
        if profitable and not (r.get("net_income") and r["net_income"] > 0):
            continue
        if pe_max is not None and not (r.get("pe") is not None and r["pe"] <= pe_max):
            continue
        if ps_max is not None and not (r.get("ps") is not None and r["ps"] <= ps_max):
            continue
        if growth_min is not None and not (r.get("growth") is not None and r["growth"] >= growth_min):
            continue
        if net_margin_min is not None and not (r.get("net_margin") is not None and r["net_margin"] >= net_margin_min):
            continue
        if op_margin_min is not None and not (r.get("op_margin") is not None and r["op_margin"] >= op_margin_min):
            continue
        if t and t not in f"{r['ticker']} {r.get('name') or ''} {r.get('industry') or ''}".lower():
            continue
        out.append(r)
    return out


def status() -> dict:
    from sqlalchemy import func
    with get_engine().connect() as conn:
        n, p, f, fy, q = conn.execute(select(func.count(), func.max(us_stocks.c.prices_at), func.max(us_stocks.c.figures_at),
                                             func.max(us_stocks.c.fiscal_year), func.max(us_stocks.c.quarter))).one()
    return {"stocks": n, "prices_at": p, "figures_at": f, "fiscal_year": fy, "quarter": q}
