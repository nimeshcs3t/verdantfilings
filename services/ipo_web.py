"""Upcoming IPOs read from public IPO calendar websites, to complement the official feeds in services/ipos.py.

Most of these sites publish their calendar as an HTML table, so one table reader maps each site's column names
to our fields. Sites that block automated reading (robots.txt) or only load data with JavaScript are not used.
Every source records its own status (rows read, last success, last error) for the IPOs page and the Admin page.
Personal-use reading of public pages; check each site's terms before offering this commercially."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# market code -> (country, currency)
MARKETS = {"US": ("USA", "USD"), "KR": ("South Korea", "KRW"), "JP": ("Japan", "JPY"), "HK": ("Hong Kong", "HKD"),
           "AU": ("Australia", "AUD"), "IN": ("India", "INR"), "FR": ("France", "EUR"), "NL": ("Netherlands", "EUR"),
           "IT": ("Italy", "EUR"), "NO": ("Norway", "NOK"), "BE": ("Belgium", "EUR"), "PT": ("Portugal", "EUR"),
           "IE": ("Ireland", "EUR"), "ID": ("Indonesia", "IDR")}
EURONEXT_CITY = {"paris": "FR", "amsterdam": "NL", "milan": "IT", "oslo": "NO", "brussels": "BE", "lisbon": "PT",
                 "dublin": "IE"}

# name -> settings. date: order of day/month in dates ("mdy", "dmy", "ymd", "md"); window: the date column is a
# subscription window rather than a listing date.
SOURCES = {
    "Iposcoop": {"url": "https://www.iposcoop.com/ipo-calendar/", "market": "US", "date": "mdy"},
    "Renaissance Capital": {"url": "https://www.renaissancecapital.com/IPO-Center", "market": "US", "date": "mdy"},
    "StockAnalysis": {"url": "https://stockanalysis.com/ipos/calendar/", "market": "US", "date": "mdy"},
    "Euronext": {"url": "https://live.euronext.com/en/ipo-showcase", "market": "EURONEXT", "date": "dmy", "recent_days": 45},
    "AAStocks": {"url": "https://www.aastocks.com/en/stocks/market/ipo/upcomingipo/company-summary", "market": "HK", "date": "ymd"},
    "IPO Watch": {"url": "https://ipowatch.in/upcoming-ipo-calendar-ipo-list/", "market": "IN", "date": "dmy", "window": True,
                  "listing_after": 3},
    "Kalkine": {"url": "https://kalkine.com.au/stocks/upcoming-ipo", "market": "AU", "date": "mdy", "announced": True},
    "IPO Kiso": {"url": "https://www.ipokiso.com/", "market": "JP", "date": "md"},
    "e-IPO": {"url": "https://e-ipo.co.id/en", "market": "ID", "mode": "ai"},
}

# column name (lower case) -> field. First match wins, so specific names come before general ones.
HEADER_RULES = [
    ("name", ("company name", "issuer borrower name full", "name / code", "name/code", "ipo企業名", "社名", "企業名",
              "company", "issuer")),
    ("isin", ("isin",)),
    ("ticker", ("symbol proposed", "symbol", "ticker", "コード", "code")),
    ("close_date", ("closing date", "close date", "offer closes")),
    ("grey_date", ("grey market",)),
    ("listing_date", ("expected to trade", "listing date", "trade date", "ipo date", "公開日", "上場日", "date")),
    ("price_low", ("price low", "low file price")),
    ("price_high", ("price high", "high file price")),
    ("price_range", ("price range", "offer price", "price band", "ipo price", "issue price", "仮条件")),
    ("lot", ("lot size",)),
    ("entry_fee", ("entry fee",)),
    ("shares", ("shares",)),
    ("size", ("volume", "deal size", "ipo size", "issue size", "proceeds amount")),
    ("market_cap", ("market cap",)),
    ("location", ("location",)),
    ("exchange", ("primary exchange", "exchange", "market", "listing at")),
    ("sector", ("industry", "sector")),
    ("underwriters", ("lead manager", "underwriter")),
    ("status", ("transaction status", "status")),
    ("issue_type", ("issue type",)),
    ("currency", ("currency",)),
    ("rating", ("scoop rating", "rating", "評価")),
    ("revenue", ("revenue",)),
]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ---- small parsers -----------------------------------------------------------------------------------------------
def clean(text) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split())


def amount(text, default_mult: float = 1.0) -> float | None:
    """'$ 150.0 mil', '150.00M', '₹30,187.7 Cr.', '1.2B', '9,375,000', '15' (with a column multiplier)."""
    t = clean(text).lower().replace(",", "")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(trillion|tn|billion|bn|b|million|mil|mn|mm|m|crore|cr|lakh|k|億|万)?\b", t)
    if not m:
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*(億|万)", t)
        if not m:
            return None
    value = float(m.group(1))
    unit = (m.group(2) or "").strip()
    mult = {"trillion": 1e12, "tn": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mil": 1e6, "mn": 1e6,
            "mm": 1e6, "m": 1e6, "crore": 1e7, "cr": 1e7, "lakh": 1e5, "k": 1e3, "億": 1e8, "万": 1e4}.get(unit)
    return value * (mult if mult else default_mult)


def price_range(text) -> tuple[float | None, float | None]:
    """'$15.00 - $17.00', '3.92-5.07', '₹1065 to ₹1119', 'IDR 130 - IDR 140', '550円～570円', '$4.25'."""
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", clean(text))]
    nums = [n for n in nums if n > 0][:2]
    if not nums:
        return None, None
    return (min(nums), max(nums)) if len(nums) == 2 else (nums[0], nums[0])


def _year_for(month: int, day: int, today: date) -> date | None:
    """A date shown without a year: the nearest one (dates more than 4 months back belong to next year)."""
    try:
        d = date(today.year, month, day)
    except ValueError:
        return None
    if d < today - timedelta(days=120):
        d = d.replace(year=today.year + 1)
    elif d > today + timedelta(days=240):
        d = d.replace(year=today.year - 1)
    return d


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(text, order: str = "mdy", today: date | None = None) -> date | None:
    today = today or date.today()
    t = clean(text)
    if not t:
        return None
    m = re.search(r"(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})", t)               # 2026/10/20, 2026-10-20
    if m:
        y, a, b = map(int, m.groups())
        try:
            return date(y, a, b)
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", t)              # 10/15/2026 or 26/10/2026
    if m:
        a, b, y = map(int, m.groups())
        y = y + 2000 if y < 100 else y
        month, day = (a, b) if order.startswith("m") else (b, a)
        try:
            return date(y, month, day)
        except ValueError:
            try:
                return date(y, day, month)
            except ValueError:
                return None
    m = re.search(r"(?<!\d)(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?,?\s*(\d{4})?", t)  # 15 Oct 2026 / 23 October
    if m and m.group(2).lower()[:3] in MONTHS:
        month, day = MONTHS[m.group(2).lower()[:3]], int(m.group(1))
        return _safe(int(m.group(3)), month, day) if m.group(3) else _year_for(month, day, today)
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?!\d)(?:,?\s*(\d{4}))?", t)  # Oct 15, 2026 / October 15
    if m and m.group(1).lower()[:3] in MONTHS:
        month, day = MONTHS[m.group(1).lower()[:3]], int(m.group(2))
        return _safe(int(m.group(3)), month, day) if m.group(3) else _year_for(month, day, today)
    m = re.search(r"(\d{1,2})月(\d{1,2})日", t) or (re.fullmatch(r"(\d{1,2})/(\d{1,2})", t) if order == "md" else None)
    if m:
        return _year_for(int(m.group(1)), int(m.group(2)), today)
    return None


def parse_window(text, order: str = "dmy", today: date | None = None) -> tuple[date | None, date | None]:
    """'21-23 October', '15-19 Oct 2026', '24 Aug 2026 - 27 Aug 2026', 'Oct 21 - Oct 23'."""
    today = today or date.today()
    t = clean(text)
    m = re.fullmatch(r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?,?\s*(\d{4})?", t)
    if m and m.group(3).lower()[:3] in MONTHS:
        month = MONTHS[m.group(3).lower()[:3]]
        end = date(int(m.group(4)), month, int(m.group(2))) if m.group(4) else _year_for(month, int(m.group(2)), today)
        start = None
        if end:
            try:
                start = end.replace(day=int(m.group(1)))
            except ValueError:
                start = None
            if start and start > end:                                           # 30 Oct - 2 Nov style
                start = (start.replace(day=1) - timedelta(days=1)).replace(day=int(m.group(1)))
        return start, end
    parts = re.split(r"\s+[-–~～to]+\s+|\s*[–~～]\s*", t)
    if len(parts) == 2:
        return parse_date(parts[0], order, today), parse_date(parts[1], order, today)
    d = parse_date(t, order, today)
    return d, d


def business_days_after(d: date, n: int) -> date:
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


# ---- reading pages -----------------------------------------------------------------------------------------------
_clients: dict = {}


def fetch_html(source: str, url: str) -> str:
    from sources.webhttp import PoliteClient
    if source not in _clients:
        key = re.sub(r"\W", "", source.lower())
        _clients[source] = PoliteClient(f"ipoweb_{key}", pause=1.0, block_hours=12,
                                        headers={"Accept": "text/html,application/xhtml+xml"})
    r = _clients[source].get(url, timeout=40)
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text


def map_headers(headers: list[str]) -> dict[int, tuple[str, float]]:
    """Column index -> (field, multiplier for counts and amounts)."""
    out, used = {}, set()
    for i, h in enumerate(headers):
        low = clean(h).lower()
        if not low:
            continue
        for field, words in HEADER_RULES:
            if field in used and field not in ("exchange",):
                continue
            if any(w in low for w in words):
                mult = 1e6 if re.search(r"\(\$?m+\)|million|\(\$m\)", low) else 1.0
                out[i] = (field, mult)
                used.add(field)
                break
    return out


def read_tables(html: str, base_url: str) -> list[list[dict]]:
    """Every table that looks like an IPO list, as rows of {field: (text, link)}."""
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        head_at, cols, headers = None, {}, []
        for i, tr in enumerate(rows[:4]):                   # the column names may sit below a title row
            cells = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
            mapped = map_headers(cells)
            fields = {f for f, _ in mapped.values()}
            if "name" in fields and len(fields) >= 3:
                head_at, cols, headers = i, mapped, cells
                break
        if head_at is None:
            continue
        out = []
        for tr in rows[head_at + 1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < len(headers) - 2:
                continue
            row = {}
            for i, cell in enumerate(cells):
                if i not in cols:
                    continue
                field, mult = cols[i]
                a = cell.find("a", href=True)
                link = urljoin(base_url, a["href"]) if a and not a["href"].startswith(("javascript", "#", "mailto")) else None
                row[field] = (clean(cell.get_text(" ")), link, mult)
            if row.get("name") and row["name"][0]:
                out.append(row)
        if out:
            found.append(out)
    return found


def _norm_name(name: str) -> str:
    n = re.sub(r"\(.*?\)", " ", (name or "").lower())
    n = re.sub(r"[^a-z0-9ぁ-んァ-ン一-龥가-힣 ]", " ", n)
    stop = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "holdings", "holding",
            "group", "sa", "s", "a", "se", "nv", "ag", "asa", "ab", "spa", "tbk", "pt", "the", "kk", "llc", "lp", "bhd",
            "revived", "class", "units", "unit", "ord", "shares"}
    words = [w for w in n.split() if w not in stop]
    return " ".join(words[:3])


def to_item(source: str, cfg: dict, row: dict, today: date) -> dict | None:
    get = lambda f: (row.get(f) or ("", None, 1.0))
    name, link, _ = get("name")
    if cfg.get("market") == "AU" and get("issue_type")[0] and get("issue_type")[0].strip().upper() != "IPO":
        return None
    ticker = clean(get("ticker")[0]).upper() or None
    m = re.search(r"\b(\d{4,5})\.HK\b", name, re.I)                 # AAStocks: 'SWL Tour 06157.HK'
    if m:
        ticker = m.group(1).lstrip("0").zfill(4)
        name = clean(name[:m.start()])
    name = re.sub(r"\s*\((?:revived|crosslisting)\)\s*", " ", name, flags=re.I).strip(" -")
    if not name or len(name) > 160 or name.lower() in ("company", "total"):
        return None
    market = cfg["market"]
    if market == "EURONEXT":
        market = EURONEXT_CITY.get(get("location")[0].strip().lower())
        if not market:
            return None
    country, currency = MARKETS[market]
    order = cfg.get("date", "mdy")
    item = {"market": market, "country": country, "currency": currency, "name": name[:200], "ticker": (ticker or "")[:20] or None,
            "_source": source, "_link": link, "_norm": _norm_name(name), "details": {}}
    when_text = get("listing_date")[0]
    if cfg.get("window"):
        item["open_date"], item["close_date"] = parse_window(when_text, order, today)
        if item["close_date"] and cfg.get("listing_after"):
            item["listing_date"] = business_days_after(item["close_date"], cfg["listing_after"])
            item["details"]["listing_estimated"] = True
    elif cfg.get("announced"):
        announced = parse_date(when_text, order, today)
        if announced:
            item["details"]["announced"] = announced.isoformat()
    else:
        item["listing_date"] = parse_date(when_text, order, today)
    if get("close_date")[0]:
        item["close_date"] = parse_date(get("close_date")[0], order, today)
    if get("grey_date")[0]:
        item["details"]["grey_market"] = get("grey_date")[0]
    low = amount(get("price_low")[0]) if get("price_low")[0] else None
    high = amount(get("price_high")[0]) if get("price_high")[0] else None
    if get("price_range")[0] and not (low or high):
        low, high = price_range(get("price_range")[0])
    item["price_low"], item["price_high"] = low, high or low
    if get("shares")[0]:
        item["shares_offered"] = amount(get("shares")[0], get("shares")[2])
    if get("size")[0]:
        item["raise_amount"] = amount(get("size")[0], get("size")[2])
    if get("market_cap")[0]:
        item["market_cap"] = amount(get("market_cap")[0], get("market_cap")[2])
    exch = [clean(v[0]) for k, v in row.items() if k == "exchange" and v[0]]
    item["exchange"] = (exch[0] if exch else None)
    if market in EURONEXT_CITY.values() and source == "Euronext":
        item["exchange"] = f"{item['exchange'] or 'Euronext'} {get('location')[0]}".strip()
    if get("sector")[0]:
        item["details"]["industry"] = get("sector")[0]
    if get("underwriters")[0]:
        item["underwriters"] = get("underwriters")[0][:300]
    for f in ("isin", "lot", "entry_fee", "rating"):
        if get(f)[0]:
            item["details"][f] = get(f)[0]
    status_text = f"{when_text} {get('status')[0]}".lower()
    item["status"] = status_of(item, status_text, today)
    if item.get("listing_date") and item["listing_date"] < today - timedelta(days=cfg.get("recent_days", 30)):
        return None
    return item


def status_of(item: dict, text: str, today: date) -> str:
    if item.get("listing_date") and item["listing_date"] <= today and not item["details"].get("listing_estimated"):
        return "listed"
    if "priced" in text:
        return "priced"
    if item.get("open_date") and item.get("close_date") and item["open_date"] <= today <= item["close_date"]:
        return "open"
    if any(w in text for w in ("mandated", "announced", "filed")):
        return "filed"
    return "upcoming"


# ---- AI reading for pages without tables -----------------------------------------------------------------------
AI_LIST_PROMPT = """This is the text of an IPO listing page for {country}. Extract the IPOs that are upcoming, open for
subscription, or listed in the last 30 days (today is {today}). Skip cancelled ones. Reply with a JSON list only:
[{{"name": "...", "ticker": "..." or null, "sector": "..." or null, "open_date": "YYYY-MM-DD" or null,
"close_date": "YYYY-MM-DD" or null, "listing_date": "YYYY-MM-DD" or null, "price_low": number or null,
"price_high": number or null, "shares_offered": number or null, "status": "upcoming" | "open" | "listed"}}]
Use only what the page states. An empty list is fine.

Page text:
{text}"""


def page_text(html: str, limit: int = 20000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
        tag.decompose()
    return clean(soup.get_text(" "))[:limit]


def ai_items(source: str, cfg: dict, html: str, today: date) -> list[dict]:
    from .ipos import _ai_list
    country, currency = MARKETS[cfg["market"]]
    rows = _ai_list(AI_LIST_PROMPT.format(country=country, today=today.isoformat(), text=page_text(html)))
    out = []
    for r in rows or []:
        name = clean(r.get("name"))[:200]
        if not name:
            continue
        item = {"market": cfg["market"], "country": country, "currency": currency, "name": name,
                "ticker": (clean(r.get("ticker")).upper() or None), "_source": source, "_link": None,
                "_norm": _norm_name(name), "details": {}}
        for k in ("open_date", "close_date", "listing_date"):
            item[k] = parse_date(r.get(k) or "", "ymd", today)
        for k in ("price_low", "price_high", "shares_offered"):
            item[k] = amount(r.get(k)) if r.get(k) is not None else None
        if r.get("sector"):
            item["details"]["industry"] = clean(r["sector"])[:80]
        item["status"] = status_of(item, str(r.get("status") or ""), today)
        out.append(item)
    return out


# ---- run ----------------------------------------------------------------------------------------------------------
def fetch(source: str, today: date | None = None) -> list[dict]:
    today = today or date.today()
    cfg = SOURCES[source]
    html = fetch_html(source, cfg["url"])
    if cfg.get("mode") == "ai":
        return ai_items(source, cfg, html, today)
    items, seen = [], set()
    for table in read_tables(html, cfg["url"]):
        for row in table:
            item = to_item(source, cfg, row, today)
            if item and (item["market"], item["_norm"]) not in seen:
                seen.add((item["market"], item["_norm"]))
                items.append(item)
    return items


def record_status(source: str, ok: bool, count: int = 0, error: str = "") -> None:
    from core.usage import get_state, set_state
    try:
        prev = json.loads(get_state(f"iposrc:{source}") or "{}")
    except ValueError:
        prev = {}
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "+00:00"
    state = {**prev, "last_try": now, "count": count if ok else prev.get("count", 0), "error": "" if ok else error[:200]}
    if ok:
        state["last_ok"] = now
    set_state(f"iposrc:{source}", json.dumps(state))


def source_status() -> list[dict]:
    from core.usage import get_state
    out = []
    for name, cfg in {**{n: {"market": c["market"]} for n, c in SOURCES.items()}, **OFFICIAL}.items():
        try:
            st = json.loads(get_state(f"iposrc:{name}") or "{}")
        except ValueError:
            st = {}
        out.append({"source": name, "market": cfg["market"], **st})
    return out


OFFICIAL = {"Nasdaq": {"market": "US"}, "DART": {"market": "KR"}, "JPX": {"market": "JP"}, "HKEX": {"market": "HK"},
            "ASX": {"market": "AU"}}
