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
SUPPORTED = {"KR", "US", "TW", "FR", "UK", "NO", "SE", "DK", "FI"}
ESEF = {"FR", "UK", "NO", "SE", "DK", "FI"}       # annual reports in the EU/UK digital format (filings.xbrl.org)
ESEF_COUNTRY = {"NO": "NO", "SE": "SE", "DK": "DK", "FI": "FI", "UK": "GB", "FR": "FR"}
TW_SETS = ("ci", "basi", "bd", "fh", "ins", "mim")  # TWSE income statements by industry format
TW_FIELDS = {"revenue": ("營業收入", "收益合計", "淨收益", "收入合計"), "op_income": ("營業利益（損失）",),
             "net_income": ("本期淨利（淨損）", "本期稅後淨利（淨損）")}
ESEF_CONCEPTS = {"revenue": ("ifrs-full:Revenue", "ifrs-full:RevenueFromContractsWithCustomers"),
                 "op_income": ("ifrs-full:ProfitLossFromOperatingActivities",),
                 "net_income": ("ifrs-full:ProfitLoss", "ifrs-full:ProfitLossAttributableToOwnersOfParent")}
_TW_CACHE: dict = {}

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
LAST_SHARES: list = [None]       # shares outstanding found by the latest fetch (for market cap and ratios)


def fetch_kr(corp_code: str, this_year: int) -> list[dict]:
    src = get_source("KR")
    rows = []
    for year in (this_year - 1, this_year - 2):
        try:
            data = src._json("stockTotqySttus.json", corp_code=corp_code, bsns_year=str(year), reprt_code="11011")
        except Exception:
            break
        common = next((i for i in data.get("list", []) if "보통" in (i.get("se") or "")), None) if data.get("status") == "000" else None
        if common and _number(common.get("istc_totqy")):
            LAST_SHARES[0] = _number(common.get("istc_totqy"))
            break
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
    shares = (((facts.get("dei") or {}).get("EntityCommonStockSharesOutstanding") or {}).get("units") or {}).get("shares") or []
    if shares:
        LAST_SHARES[0] = _number(sorted(shares, key=lambda e: e.get("end") or "")[-1].get("val"))
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


def fetch_tw(ticker: str) -> list[dict]:
    """Latest quarter from TWSE open data (thousands of TWD, cumulative for the year to date)."""
    import requests
    if not _TW_CACHE.get("rows") or __import__("time").time() - _TW_CACHE.get("at", 0) > 3600:
        rows = []
        for name in TW_SETS:
            try:
                r = requests.get(f"https://openapi.twse.com.tw/v1/opendata/t187ap06_L_{name}", timeout=60)
                if r.status_code == 200:
                    rows += r.json()
            except Exception:
                continue
        _TW_CACHE.update(rows=rows, at=__import__("time").time())
    try:
        from sources import get_source as _gs
        src_tw = _gs("TW")
        listing = _TW_CACHE.get("list") or requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=60).json()
        _TW_CACHE["list"] = listing
        info = next((c for c in listing if str(c.get("公司代號") or "").strip() == ticker), None)
        if info and _number(info.get("已發行普通股數或TDR原股發行股數")):
            LAST_SHARES[0] = _number(info.get("已發行普通股數或TDR原股發行股數"))
    except Exception:
        pass
    rec = next((r for r in _TW_CACHE["rows"] if str(r.get("公司代號") or "").strip() == ticker), None)
    if not rec:
        return []
    year, quarter = int(rec.get("年度") or 0) + 1911, int(rec.get("季別") or 0)
    if year < 1990 or not 1 <= quarter <= 4:
        return []
    values = {}
    for key, names in TW_FIELDS.items():
        v = next((_number(rec.get(n)) for n in names if _number(rec.get(n)) is not None), None)
        values[key] = v * 1000 if v is not None else None
    if quarter == 4:
        return [{"kind": "annual", "period": str(year), "currency": "TWD", **values}]
    return [{"kind": "quarter", "period": f"{year} Q{quarter} YTD", "currency": "TWD", **values}]


def lei_for(market: str, ticker: str, source_id: str, name: str) -> str | None:
    """LEI for annual-report lookups: the UK source already uses it; France by ISIN; others by name (GLEIF)."""
    from core.usage import get_state, set_state
    import requests
    if market == "UK":
        return source_id
    cached = get_state(f"lei:{market}:{ticker}")
    if cached is not None:
        return cached or None
    lei = None
    try:
        if market == "FR":
            data = requests.get("https://api.gleif.org/api/v1/lei-records", params={"filter[isin]": source_id}, timeout=30).json()
            lei = data["data"][0]["id"] if data.get("data") else None
        else:
            plain = re.sub(r"\s+", " ", re.sub(r"\(.*?\)", "", name or "")).strip()
            for params in ({"filter[entity.legalName]": plain},
                           {"filter[fulltext]": plain, "filter[entity.legalAddress.country]": ESEF_COUNTRY.get(market, "")}):
                data = requests.get("https://api.gleif.org/api/v1/lei-records", params={**params, "page[size]": 3}, timeout=30).json()
                hit = next((d for d in data.get("data", []) if d.get("attributes", {}).get("entity", {}).get("status") == "ACTIVE"), None)
                if hit:
                    lei = hit["id"]
                    break
    except Exception:
        return None
    set_state(f"lei:{market}:{ticker}", lei or "")
    return lei


def fetch_esef(lei: str) -> list[dict]:
    """Yearly revenue, operating profit and net profit from the company's latest digital annual reports."""
    import requests
    from datetime import date as _date
    listing = requests.get("https://filings.xbrl.org/api/filings", params={"filter[entity.identifier]": lei, "sort": "-period_end",
                                                                           "page[size]": 2},
                           headers={"Accept": "application/vnd.api+json"}, timeout=60).json().get("data", [])
    periods: dict[str, dict] = {}
    for item in listing:
        url = (item.get("attributes") or {}).get("json_url")
        if not url:
            continue
        facts = requests.get("https://filings.xbrl.org" + url, timeout=120).json().get("facts") or {}
        for f in facts.values():
            dims = f.get("dimensions") or {}
            if set(dims) - {"concept", "entity", "period", "unit", "language"}:
                continue          # skip breakdowns by segment, region and so on
            if dims.get("concept") in ("ifrs-full:NumberOfSharesOutstanding", "ifrs-full:NumberOfSharesIssued",
                                       "ifrs-full:WeightedAverageShares") and _number(f.get("value")):
                if LAST_SHARES[0] is None or dims["concept"] != "ifrs-full:WeightedAverageShares":
                    LAST_SHARES[0] = _number(f.get("value"))
            key = next((k for k, names in ESEF_CONCEPTS.items() if dims.get("concept") in names), None)
            span = str(dims.get("period") or "").split("/")
            if not key or len(span) != 2:
                continue
            start, end = (_date.fromisoformat(x[:10]) for x in span)
            if not 330 <= (end - start).days <= 400:
                continue          # yearly figures only
            last_day = end - timedelta(days=1)
            label = str(last_day.year) if last_day.month == 12 else f"{last_day.year}-{last_day.month:02d}"
            row = periods.setdefault(label, {"kind": "annual", "period": label,
                                             "currency": str(dims.get("unit") or "").replace("iso4217:", "")})
            rank = ESEF_CONCEPTS[key].index(dims["concept"])
            if row.get(f"_{key}", 99) > rank:
                row[key], row[f"_{key}"] = _number(f.get("value")), rank
    rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in periods.values()]
    return sorted(rows, key=lambda r: r["period"])[-5:]


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
        watched = conn.execute(select(companies.c.market, companies.c.ticker, companies.c.source_id, companies.c.name_en).select_from(
            companies.join(watchlist, and_(watchlist.c.market == companies.c.market,
                                           watchlist.c.ticker == companies.c.ticker)))
            .where(companies.c.market.in_(SUPPORTED)).distinct()).all()
        due = []
        for market, ticker, source_id, name in watched:
            src = get_source(market)
            if not src or not src.is_configured():
                continue
            meta = _meta(conn, market, ticker)
            last = as_utc(meta["fin_updated"]) if meta and meta.get("fin_updated") else None
            if not last or utcnow() - last > REFRESH:
                due.append((last or utcnow() - timedelta(days=9999), market, ticker, source_id, name))
    done = 0
    for _, market, ticker, source_id, name in sorted(due)[:limit]:
        LAST_SHARES[0] = None
        try:
            if market == "KR":
                rows = fetch_kr(source_id, get_source("KR").today().year)
            elif market == "US":
                rows = fetch_us(source_id)
            elif market == "TW":
                rows = fetch_tw(ticker)
            else:
                lei = lei_for(market, ticker, source_id, name)
                rows = fetch_esef(lei) if lei else []
        except Exception as exc:
            log.warning("financials failed for %s:%s: %s", market, ticker, exc)
            touch_meta(market, ticker, fin_updated=utcnow() - REFRESH + timedelta(days=1))   # retry tomorrow
            continue
        with get_engine().begin() as conn:
            if market == "TW":       # Taiwan publishes only the latest quarter: keep earlier ones
                for r in rows:
                    conn.execute(delete(financials).where(financials.c.market == market, financials.c.ticker == ticker,
                                                          financials.c.kind == r["kind"], financials.c.period == r["period"]))
            else:
                conn.execute(delete(financials).where(financials.c.market == market, financials.c.ticker == ticker))
            for r in rows:
                conn.execute(insert(financials).values(market=market, ticker=ticker, kind=r["kind"], period=r["period"],
                                                       revenue=r.get("revenue"), op_income=r.get("op_income"),
                                                       net_income=r.get("net_income"), currency=r.get("currency")))
        touch_meta(market, ticker, fin_updated=utcnow(), **({"shares": LAST_SHARES[0]} if LAST_SHARES[0] else {}))
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
