"""Key financials (revenue, operating profit, net profit) from free official sources:
Korea: DART key accounts (annual). USA: SEC company facts (annual and quarterly).
Fetched by the worker and stored; the website only reads the table."""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from sqlalchemy import and_, delete, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from core.db import as_utc, companies, company_meta, financials, get_engine, utcnow, watchlist
from sources import get_source

log = logging.getLogger(__name__)
REFRESH = timedelta(days=7)
SUPPORTED = {"KR", "US"}

US_TAGS = {
    "revenue": [("us-gaap", "Revenues"), ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
                ("us-gaap", "SalesRevenueNet"), ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
                ("ifrs-full", "Revenue")],
    "op_income": [("us-gaap", "OperatingIncomeLoss"), ("ifrs-full", "ProfitLossFromOperatingActivities")],
    "net_income": [("us-gaap", "NetIncomeLoss"), ("us-gaap", "ProfitLoss"), ("ifrs-full", "ProfitLoss"),
                   ("ifrs-full", "ProfitLossAttributableToOwnersOfParent")],
}
KR_ACCOUNTS = {"revenue": ("매출액", "수익(매출액)", "영업수익", "매출"), "op_income": ("영업이익",),
               "net_income": ("당기순이익", "연결당기순이익", "당기순손익")}


def _number(text) -> float | None:
    try:
        t = str(text or "").replace(",", "").strip()
        if t in ("", "-"):
            return None
        return float(t.replace("(", "-").replace(")", ""))
    except ValueError:
        return None


# ---- fetching ---------------------------------------------------------------------------
def fetch_kr(corp_code: str, this_year: int) -> list[dict]:
    src = get_source("KR")
    rows = []
    for year in range(this_year - 1, this_year - 6, -1):
        data = src._json("fnlttSinglAcnt.json", corp_code=corp_code, bsns_year=str(year), reprt_code="11011")
        if data.get("status") != "000":
            continue
        items = data.get("list", [])
        div = "CFS" if any(i.get("fs_div") == "CFS" for i in items) else "OFS"
        found = {}
        for key, names in KR_ACCOUNTS.items():
            for i in items:
                name = re.sub(r"\s+", "", i.get("account_nm", ""))
                if i.get("fs_div") == div and any(name.startswith(n) for n in names) and key not in found:
                    found[key] = _number(i.get("thstrm_amount"))
        if found:
            rows.append({"kind": "annual", "period": str(year), "currency": "KRW", **found})
    return rows


def fetch_us(cik: str) -> list[dict]:
    src = get_source("US")
    facts = src._get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json").json().get("facts", {})
    series: dict[tuple[str, str], dict] = {}
    currency = "USD"
    for key, tags in US_TAGS.items():
        best: dict[str, float] = {}
        for ns, tag in tags:
            units = ((facts.get(ns) or {}).get(tag) or {}).get("units") or {}
            if not units:
                continue
            unit = "USD" if "USD" in units else next(iter(units))
            values = {}
            for e in units[unit]:
                frame = e.get("frame") or ""
                if re.fullmatch(r"CY\d{4}", frame):
                    values[("annual", frame[2:])] = e["val"]
                elif re.fullmatch(r"CY\d{4}Q\d", frame):
                    values[("quarter", f"{frame[2:6]} Q{frame[-1]}")] = e["val"]
            if values and max(p for _, p in values) >= max((p for _, p in best), default=""):
                best, currency = values, unit
        for (kind, period), val in best.items():
            series.setdefault((kind, period), {"kind": kind, "period": period})[key] = val
    annual = sorted((v for (k, _), v in series.items() if k == "annual"), key=lambda r: r["period"])[-5:]
    quarter = sorted((v for (k, _), v in series.items() if k == "quarter"), key=lambda r: r["period"])[-8:]
    return [{**r, "currency": currency} for r in annual + quarter]


# ---- worker -----------------------------------------------------------------------------
def _meta(conn, market: str, ticker: str) -> dict | None:
    row = conn.execute(select(company_meta).where(company_meta.c.market == market,
                                                  company_meta.c.ticker == ticker)).mappings().first()
    return dict(row) if row else None


def touch_meta(market: str, ticker: str, **values) -> None:
    with get_engine().begin() as conn:
        if not conn.execute(update(company_meta).where(company_meta.c.market == market, company_meta.c.ticker == ticker)
                            .values(**values)).rowcount:
            try:
                conn.execute(insert(company_meta).values(market=market, ticker=ticker, **values))
            except IntegrityError:
                pass


def refresh(limit: int = 6) -> int:
    """Refresh financials for watched Korean and US companies, oldest first, a few per run."""
    with get_engine().connect() as conn:
        watched = conn.execute(select(companies.c.market, companies.c.ticker, companies.c.source_id).select_from(
            companies.join(watchlist, and_(watchlist.c.market == companies.c.market,
                                           watchlist.c.ticker == companies.c.ticker)))
            .where(companies.c.market.in_(SUPPORTED)).distinct()).all()
        due = []
        for market, ticker, source_id in watched:
            src = get_source(market)
            if not src or not src.is_configured():
                continue
            meta = _meta(conn, market, ticker)
            last = as_utc(meta["fin_updated"]) if meta and meta.get("fin_updated") else None
            if not last or utcnow() - last > REFRESH:
                due.append((last or utcnow() - timedelta(days=9999), market, ticker, source_id))
    done = 0
    for _, market, ticker, source_id in sorted(due)[:limit]:
        try:
            rows = fetch_kr(source_id, get_source("KR").today().year) if market == "KR" else fetch_us(source_id)
        except Exception as exc:
            log.warning("financials failed for %s:%s: %s", market, ticker, exc)
            touch_meta(market, ticker, fin_updated=utcnow() - REFRESH + timedelta(days=1))   # retry tomorrow
            continue
        with get_engine().begin() as conn:
            conn.execute(delete(financials).where(financials.c.market == market, financials.c.ticker == ticker))
            for r in rows:
                conn.execute(insert(financials).values(market=market, ticker=ticker, kind=r["kind"], period=r["period"],
                                                       revenue=r.get("revenue"), op_income=r.get("op_income"),
                                                       net_income=r.get("net_income"), currency=r.get("currency")))
        touch_meta(market, ticker, fin_updated=utcnow())
        done += 1
    return done


# ---- reading ----------------------------------------------------------------------------
def get(market: str, ticker: str) -> dict[str, list[dict]]:
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(select(financials).where(
            financials.c.market == market, financials.c.ticker == ticker).order_by(financials.c.period)).mappings()]
    return {"annual": [r for r in rows if r["kind"] == "annual"], "quarter": [r for r in rows if r["kind"] == "quarter"]}


def money(value: float | None, currency: str = "") -> str:
    if value is None:
        return "–"
    sign, v = ("-" if value < 0 else ""), abs(value)
    for size, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= size:
            return f"{sign}{v / size:,.1f}{unit} {currency}".strip()
    return f"{sign}{v:,.0f} {currency}".strip()


def bars_svg(rows: list[dict], key: str, title: str) -> str:
    """A small bar chart for one measure; negative values drawn below the line."""
    values = [(r["period"], r.get(key)) for r in rows if r.get(key) is not None]
    if not values:
        return ""
    w, h, pad, gap = 300, 120, 18, 6
    top = max(max(v for _, v in values), 0) or 1
    bottom = min(min(v for _, v in values), 0)
    span = (top - bottom) or 1
    zero = pad + (top / span) * (h - 2 * pad)
    bw = (w - gap * (len(values) + 1)) / len(values)
    bars, labels = [], []
    for i, (period, v) in enumerate(values):
        x = gap + i * (bw + gap)
        y = zero - (v / span) * (h - 2 * pad)
        cls = "neg" if v < 0 else "pos"
        bars.append(f'<rect class="{cls}" x="{x:.1f}" y="{min(y, zero):.1f}" width="{bw:.1f}" '
                    f'height="{max(abs(zero - y), 1):.1f}" rx="2"><title>{period}: {money(v, rows[0]["currency"])}</title></rect>')
        labels.append(f'<text x="{x + bw / 2:.1f}" y="{h - 3}" text-anchor="middle">{period[-7:]}</text>')
    latest = money(values[-1][1], rows[0]["currency"])
    return (f'<div class="fin"><div class="fin-head"><span>{title}</span><b>{latest}</b></div>'
            f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{title} by period">'
            f'<line x1="0" x2="{w}" y1="{zero:.1f}" y2="{zero:.1f}" class="axis"/>{"".join(bars)}{"".join(labels)}</svg></div>')
