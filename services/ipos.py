"""IPOs across markets, from each region's own public source:
USA: Nasdaq IPO calendar (upcoming, priced, filed) with the SEC prospectus; Korea: DART registration statements of
unlisted companies; Japan: JPX new listings (English outline PDF); Hong Kong: HKEXnews new listings (prospectus);
Australia: ASX upcoming floats. Sector, expected market cap and a short overview are read from the official document
by Gemini/Claude where available. Website sources pause themselves if a site refuses a request.
Switch off with ENABLE_IPOS = "false"."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup
from sqlalchemy import and_, delete, insert, or_, select, update

from core.config import get_secret
from core.db import get_engine, ipo_stars, ipos, utcnow
from sources.webhttp import PoliteClient

log = logging.getLogger(__name__)
COUNTRY = {"US": "USA", "KR": "South Korea", "JP": "Japan", "HK": "Hong Kong", "AU": "Australia"}
CURRENCY = {"US": "USD", "KR": "KRW", "JP": "JPY", "HK": "HKD", "AU": "AUD"}
SECTORS = ["Technology", "Healthcare", "Financials", "Industrials", "Consumer", "Energy", "Materials & Mining",
           "Real Estate", "Communication", "Utilities", "SPAC / Blank check", "Other"]
SECTOR_WORDS = [("SPAC / Blank check", ("acquisition corp", "blank check", "spac")),
                ("Healthcare", ("therapeut", "pharma", "bio", "medical", "health", "clinic", "diagnost", "spine")),
                ("Technology", ("software", "semiconductor", "cloud", " ai", "artificial intelligence", "data", "cyber",
                                "technolog", "computing", "platform", "electronic", "saas", "robot")),
                ("Materials & Mining", ("mining", "mineral", "metals", "gold", "lithium", "exploration", "chemical")),
                ("Energy", ("energy", "oil", "gas", "solar", "renewable", "hydrogen", "power")),
                ("Financials", ("bank", "insurance", "capital", "financial", "fintech", "securities", "asset management")),
                ("Real Estate", ("real estate", "property", "reit")),
                ("Consumer", ("retail", "food", "beverage", "restaurant", "consumer", "apparel", "fashion", "cosmetic", "game")),
                ("Industrials", ("industrial", "machinery", "logistics", "aerospace", "defen", "construction", "marine", "rail"))]

_http: dict[str, PoliteClient] = {}


def enabled() -> bool:
    return str(get_secret("ENABLE_IPOS", "true")).lower() in {"1", "true", "yes"}


def http(name: str, **headers) -> PoliteClient:
    if name not in _http:
        _http[name] = PoliteClient(f"ipo_{name}", pause=0.6, headers=headers)
    return _http[name]


def _num(text) -> float | None:
    t = re.sub(r"[^\d.\-]", "", str(text or ""))
    try:
        return float(t) if t not in ("", "-", ".") else None
    except ValueError:
        return None


def _date(text: str, *formats: str) -> date | None:
    t = " ".join(str(text or "").replace(".", "").split())
    for fmt in formats:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def guess_sector(text: str) -> str:
    low = f" {(text or '').lower()} "
    return next((s for s, words in SECTOR_WORDS if any(w in low for w in words)), "Other")


# ---- USA -------------------------------------------------------------------------------------------------------------
def _sec_prospectus(name: str) -> tuple[str | None, str | None, str | None]:
    """(document URL, CIK, industry) for a company's latest S-1/F-1, via EDGAR full-text search."""
    ua = {"User-Agent": f"VerdantFilings {get_secret('SEC_CONTACT_EMAIL') or 'contact@example.com'}"}
    try:
        r = http("sec", **ua).get("https://efts.sec.gov/LATEST/search-index",
                                  params={"q": f'"{name}"', "forms": "S-1,F-1,S-1/A,F-1/A"})
        hits = (r.json().get("hits") or {}).get("hits") or []
    except Exception:
        hits = []
    for h in sorted(hits, key=lambda h: (h.get("_source") or {}).get("file_date", ""), reverse=True):
        src = h.get("_source") or {}
        cik = (src.get("ciks") or [None])[0]
        adsh, _, filename = str(h.get("_id") or "").partition(":")
        if cik and adsh and filename:
            industry = None
            try:
                sub = http("sec", **ua).get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json").json()
                industry = sub.get("sicDescription")
            except Exception:
                pass
            return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/{filename}", cik, industry
    return None, None, None


def fetch_us() -> list[dict]:
    out: dict[str, dict] = {}
    h = http("nasdaq", Accept="application/json", Origin="https://www.nasdaq.com", Referer="https://www.nasdaq.com/")
    today = date.today()
    for month in (today.replace(day=1) - timedelta(days=1), today, (today.replace(day=28) + timedelta(days=5))):
        data = (h.get("https://api.nasdaq.com/api/ipo/calendar", params={"date": f"{month:%Y-%m}"}).json().get("data") or {})
        for status in ("filed", "upcoming", "priced"):
            block = data.get(status) or {}
            rows = ((block.get("upcomingTable") or {}).get("rows") if "upcomingTable" in block else block.get("rows")) or []
            for row in rows:
                name = " ".join(str(row.get("companyName") or "").split())
                if not name:
                    continue
                low, _, high = str(row.get("proposedSharePrice") or "").partition("-")
                when = _date(row.get("expectedPriceDate") or row.get("pricedDate") or "", "%m/%d/%Y")
                key = re.sub(r"\W", "", name.lower())
                item = out.setdefault(key, {"uid": f"US:{key[:60]}", "market": "US", "name": name})
                item.update({k: v for k, v in {
                    "ticker": row.get("proposedTickerSymbol") or None, "exchange": row.get("proposedExchange") or "Nasdaq / NYSE",
                    "status": status if status != "priced" else ("listed" if when and when <= today else "priced"),
                    "listing_date": when, "price_low": _num(low), "price_high": _num(high) or _num(low),
                    "shares_offered": _num(row.get("sharesOffered")), "raise_amount": _num(row.get("dollarValueOfSharesOffered")),
                    "first_seen": _date(row.get("filedDate") or "", "%m/%d/%Y")}.items() if v is not None})
    return list(out.values())


# ---- Korea -----------------------------------------------------------------------------------------------------------
def fetch_kr() -> list[dict]:
    from sources import get_source
    src = get_source("KR")
    if not src or not src.is_configured():
        return []
    today = date.today()
    items, page = [], 1
    while page <= 5:
        data = src._json("list.json", pblntf_ty="C", bgn_de=f"{today - timedelta(days=90):%Y%m%d}",
                         end_de=f"{today:%Y%m%d}", page_no=page, page_count=100)
        if data.get("status") != "000":
            break
        items += data.get("list") or []
        if page >= int(data.get("total_page") or 1):
            break
        page += 1
    latest: dict[str, dict] = {}
    for i in items:          # unlisted companies filing an equity registration statement = IPO
        if "증권신고서(지분증권)" not in (i.get("report_nm") or "") or i.get("corp_cls") != "E":
            continue
        if i["corp_code"] not in latest or i["rcept_no"] > latest[i["corp_code"]]["rcept_no"]:
            latest[i["corp_code"]] = i
    return [{"uid": f"KR:{i['corp_code']}", "market": "KR", "name": i.get("corp_name"), "name_local": i.get("corp_name"),
             "exchange": "KOSPI / KOSDAQ", "status": "filed", "first_seen": _date(i.get("rcept_dt"), "%Y%m%d"),
             "doc_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={i['rcept_no']}", "doc_label": "Registration statement (DART)",
             "_dart": i["rcept_no"]} for i in latest.values()]


# ---- Japan -----------------------------------------------------------------------------------------------------------
JPX = "https://www.jpx.co.jp/english/listing/stocks/new/"


def fetch_jp() -> list[dict]:
    from urllib.parse import urljoin
    soup = BeautifulSoup(http("jpx").get(JPX + "index.html").text, "html.parser")
    out, current = [], None
    for tr in soup.find_all("tr"):
        cells = [" ".join(td.get_text(" ", strip=True).split()) for td in tr.find_all(["td", "th"])]
        joined = " | ".join(cells)
        listed = re.search(r"([A-Z][a-z]{2})\.? (\d{1,2}), (\d{4})", joined)
        code = next((c for c in cells if re.fullmatch(r"\d{3}[0-9A-Z]", c)), None)
        if listed and code:
            name = next((c for c in cells if c and not re.search(r"\d{4}|^\(|^-$", c) and c != code and len(c) > 2), code)
            links = [urljoin(JPX, a["href"]) for a in tr.find_all("a", href=True)]
            current = {"uid": f"JP:{code}", "market": "JP", "name": name.replace(",", ", ").replace(",  ", ", "), "ticker": code,
                       "listing_date": _date(" ".join(listed.groups()), "%b %d %Y"), "exchange": "Tokyo Stock Exchange",
                       "website": next((u for u in links if "jpx.co.jp" not in u), None),
                       "doc_url": next((u for u in links if "outline" in u.lower()), None) or JPX + "index.html",
                       "doc_label": "IPO outline (JPX, English)"}
            low, _, high = (next((c for c in cells if re.fullmatch(r"[\d,.]+\s*-\s*[\d,.]+", c)), "") or "").partition("-")
            current.update(price_low=_num(low), price_high=_num(high))
            out.append(current)
        elif current and cells:
            seg = next((c for c in cells if c in ("Prime", "Standard", "Growth", "TOKYO PRO Market")), None)
            if seg:
                current["exchange"] = f"TSE {seg}"
            price = next((_num(c) for c in cells if re.fullmatch(r"[\d,]+(\.\d+)?", c) and (_num(c) or 0) >= 100), None)
            if price and not current.get("price_low"):
                current["price_low"] = current["price_high"] = price
            current = None
    today = date.today()
    for item in out:
        item["status"] = "listed" if item.get("listing_date") and item["listing_date"] <= today else "upcoming"
    return out


# ---- Hong Kong -------------------------------------------------------------------------------------------------------
def fetch_hk() -> list[dict]:
    out = []
    for board, path in (("Main Board", "Main-Board"), ("GEM", "GEM")):
        try:
            html = http("hkex").get(f"https://www2.hkexnews.hk/New-Listings/New-Listing-Information/{path}?sc_lang=en").text
        except Exception as exc:
            log.warning("HKEX %s new listings failed: %s", board, exc)
            continue
        for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            code = tds[0].get_text(strip=True)
            if not re.fullmatch(r"\d{1,5}", code):
                continue
            name = " ".join(tds[1].get_text(" ", strip=True).split())
            links = [a["href"] for a in tr.find_all("a", href=True) if a["href"].lower().endswith(".pdf")]
            prospectus = next((a["href"] for a in (tds[3].find_all("a", href=True) if len(tds) > 3 else [])), None)
            doc = prospectus or (links[0] if links else None)
            stamp = re.search(r"/(\d{4})/(\d{2})(\d{2})/", doc or "")
            out.append({"uid": f"HK:{code}", "market": "HK", "name": name, "ticker": code.zfill(4), "exchange": f"HKEX {board}",
                        "status": "listed" if len(tds) > 4 and tds[4].find("a") else "upcoming",
                        "first_seen": date(int(stamp[1]), int(stamp[2]), int(stamp[3])) if stamp else None,
                        "doc_url": doc, "doc_label": "Prospectus (HKEXnews)" if prospectus else "Listing announcement (HKEXnews)"})
    return out


# ---- Australia -------------------------------------------------------------------------------------------------------
ASX = "https://www.asx.com.au/listings/upcoming-floats-and-listings"


def fetch_au() -> list[dict]:
    soup = BeautifulSoup(http("asx").get(ASX).text, "html.parser")
    out, current = [], None
    for el in soup.find_all(["h2", "h3", "h4", "h5", "strong", "tr"]):
        text = " ".join(el.get_text(" ", strip=True).split())
        if el.name != "tr":
            m = re.match(r"^(.{3,120}?)\s+-\s+(TBA|\w+day \d{1,2} \w+ \d{4}.*)$", text)
            if m and not re.search(r"upcoming|floats|listings", m[1], re.I):
                current = {"uid": "", "market": "AU", "name": m[1].strip(), "exchange": "ASX", "doc_url": ASX,
                           "doc_label": "ASX upcoming floats", "listing_date": _date(" ".join(m[2].split()[:4]), "%A %d %B %Y")}
                out.append(current)
            continue
        if not current:
            continue
        cells = [" ".join(td.get_text(" ", strip=True).split()) for td in el.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        label, value = cells[0].lower(), " ".join(c for c in cells[1:] if c)
        if "contact" in label:
            site = re.search(r"([\w.-]+\.[a-z]{2,}(?:\.[a-z]{2})?)", value)
            current["website"] = f"https://{site[1]}" if site else None
        elif "principal" in label:
            current["overview"] = value
        elif "issue price" in label:
            current["price_low"] = current["price_high"] = _num(value.split("AUD")[0])
        elif "security code" in label:
            current["ticker"] = value.split()[0].upper()
        elif "capital" in label:
            current["raise_amount"] = _num(value.split("AUD")[0])
        elif "close" in label:
            current["close_date"] = _date(value, "%A %d %B %Y")
        elif "listing date" in label and not current.get("listing_date"):
            current["listing_date"] = _date(" ".join(value.split()[:4]), "%A %d %B %Y")
    for item in out:
        item["uid"] = f"AU:{(item.get('ticker') or re.sub(r'[^A-Za-z0-9]', '', item['name']))[:40]}"
        item["status"] = "listed" if item.get("listing_date") and item["listing_date"] <= date.today() else "upcoming"
        item["sector"] = guess_sector(item.get("overview") or item["name"])
    return out


# ---- store -----------------------------------------------------------------------------------------------------------
FETCHERS = {"US": fetch_us, "KR": fetch_kr, "JP": fetch_jp, "HK": fetch_hk, "AU": fetch_au}
FIELDS = {c.name for c in ipos.c}


def save(items: list[dict]) -> int:
    new = 0
    with get_engine().begin() as conn:
        for item in items:
            row = {k: v for k, v in item.items() if k in FIELDS and v is not None}
            row.setdefault("country", COUNTRY.get(item["market"]))
            row.setdefault("currency", CURRENCY.get(item["market"]))
            row["updated_at"] = utcnow()
            exists = conn.execute(select(ipos.c.uid, ipos.c.doc_url).where(ipos.c.uid == row["uid"])).first()
            if exists:
                if exists[1] and row.get("doc_url") and row["doc_url"] != exists[1]:
                    row["ai_done"] = False          # a newer document: read it again
                conn.execute(update(ipos).where(ipos.c.uid == row["uid"]).values(**row))
            else:
                row.setdefault("first_seen", date.today())
                row["ai_done"] = False
                conn.execute(insert(ipos).values(**row))
                new += 1
    return new


def refresh() -> int:
    """Worker (every few hours): pull every market's IPO list."""
    if not enabled():
        return 0
    total = 0
    for market, fetch in FETCHERS.items():
        try:
            items = fetch()
            total += save(items)
        except Exception as exc:
            log.warning("IPO list failed for %s: %s", market, exc)
    with get_engine().begin() as conn:      # tidy up: drop old entries
        conn.execute(delete(ipos).where(or_(and_(ipos.c.listing_date.is_not(None), ipos.c.listing_date < date.today() - timedelta(days=120)),
                                            and_(ipos.c.listing_date.is_(None), ipos.c.first_seen < date.today() - timedelta(days=240)))))
    return total


# ---- documents, AI details, first-day move ----------------------------------------------------------------------------
PROMPT = """You are reading an IPO prospectus or listing document. Reply with JSON only:
{{"name_en": "company name in English", "sector": one of {sectors},
 "overview": "3 to 4 plain-English sentences: what the company does, its main products and markets, scale (revenue or
 customers if stated) and what the IPO money is for",
 "market_cap": number or null (expected market value at the offer price or the middle of the price range, in the
 offer currency, only if the document gives shares outstanding after the offering or states it),
 "currency": "ISO code", "listing_date": "YYYY-MM-DD" or null (expected first trading day if stated),
 "price_low": number or null, "price_high": number or null}}
Use only facts in the document. No investment advice.

Company: {name} ({country})
Known details: {known}
Document text:
{text}"""


def _doc_text(item: dict) -> str:
    from sources.doctext import any_text
    if item["market"] == "KR" and item.get("doc_url"):
        from sources import get_source
        rcept = item["doc_url"].rsplit("=", 1)[-1]
        try:
            return get_source("KR").fetch_document_text(f"KR:{rcept}")[:30000]
        except Exception:
            return ""
    if item["market"] == "US" and not item.get("doc_url"):
        url, _, industry = _sec_prospectus(item["name"])
        from urllib.parse import quote_plus
        label = "Prospectus (SEC EDGAR)" if url else "Filings (SEC EDGAR)"
        url = url or ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=S-1&company="
                      + quote_plus(re.sub(r",?\s+(Inc|Corp|Ltd|Holdings?|Co)\.?.*$", "", item["name"])))
        with get_engine().begin() as conn:
            conn.execute(update(ipos).where(ipos.c.uid == item["uid"]).values(doc_url=url, doc_label=label))
        item["doc_url"], item["sec_industry"] = url, industry
        if label.startswith("Filings"):
            return ""
    url = item.get("doc_url")
    if not url or url in (ASX, JPX + "index.html"):
        return ""
    client = http("sec", **{"User-Agent": f"VerdantFilings {get_secret('SEC_CONTACT_EMAIL') or 'contact@example.com'}"}) \
        if "sec.gov" in url else http(item["market"].lower())
    try:
        r = client.get(url)
        return any_text(r.content[:8_000_000], r.headers.get("content-type") or "")[:30000]
    except Exception as exc:
        log.warning("IPO document failed for %s: %s", item["uid"], exc)
        return ""


def _ai(prompt: str) -> dict | None:
    from .summarize import _anthropic, _gemini
    for provider in (_gemini, _anthropic):
        try:
            reply = provider(prompt)
        except Exception:
            continue
        m = re.search(r"\{.*\}", reply or "", re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                continue
    return None


def enrich(limit: int = 5) -> int:
    """Worker: read official documents for sector, market cap, overview; and record first-day moves."""
    if not enabled():
        return 0
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(select(ipos).where(ipos.c.ai_done == False)  # noqa: E712
                                              .order_by(ipos.c.listing_date.is_(None), ipos.c.listing_date).limit(limit)).mappings()]
    has_ai = bool(get_secret("GEMINI_API_KEY") or get_secret("ANTHROPIC_API_KEY"))
    done = 0
    for item in rows:
        text = _doc_text(item)
        values: dict = {"ai_done": True}
        if item.get("sec_industry"):
            values["sector"] = guess_sector(item["sec_industry"]) if guess_sector(item["sec_industry"]) != "Other" else None
        if has_ai and (text or item.get("overview")):
            known = {k: item.get(k) for k in ("ticker", "exchange", "listing_date", "price_low", "price_high", "raise_amount", "overview")}
            got = _ai(PROMPT.format(sectors=json.dumps(SECTORS), name=item["name"], country=item.get("country"),
                                    known=json.dumps(known, default=str), text=(text or item.get("overview") or "")[:24000]))
            if got:
                if got.get("sector") in SECTORS and not values.get("sector"):
                    values["sector"] = got["sector"]
                if got.get("overview"):
                    values["overview"] = str(got["overview"])[:1500]
                if _num(got.get("market_cap")):
                    values["market_cap"] = _num(got["market_cap"])
                if got.get("name_en") and item["market"] == "KR":
                    values["name"] = str(got["name_en"])[:200]
                if not item.get("listing_date") and got.get("listing_date"):
                    values["listing_date"] = _date(got["listing_date"], "%Y-%m-%d")
                for k in ("price_low", "price_high"):
                    if not item.get(k) and _num(got.get(k)):
                        values[k] = _num(got[k])
        elif not text and not item.get("overview") and item["market"] in ("US", "HK", "KR"):
            values["ai_done"] = False if not has_ai else True     # retry later once a document is found
        if not values.get("sector") and not item.get("sector"):
            values["sector"] = guess_sector(f"{item['name']} {values.get('overview') or item.get('overview') or ''}")
        if item["market"] == "KR" and "name" not in values and not has_ai:
            try:
                from .translate import translate_title
                values["name"] = translate_title(item["name"], "ko") or item["name"]
            except Exception:
                pass
        with get_engine().begin() as conn:
            conn.execute(update(ipos).where(ipos.c.uid == item["uid"]).values(**{k: v for k, v in values.items() if v is not None or k == "ai_done"}))
        done += 1
    _first_day_moves()
    return done


YAHOO = {"US": "", "JP": ".T", "HK": ".HK", "AU": ".AX"}


def _first_day_moves() -> None:
    from .prices import _yahoo
    today = date.today()
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(select(ipos).where(
            ipos.c.first_day.is_(None), ipos.c.ticker.is_not(None), ipos.c.listing_date <= today,
            ipos.c.listing_date >= today - timedelta(days=30), ipos.c.market.in_(list(YAHOO)))).mappings()]
    for r in rows[:10]:
        offer = r.get("price_high") if r.get("price_high") == r.get("price_low") else ((r.get("price_low") or 0) + (r.get("price_high") or 0)) / 2 or None
        try:
            hist = _yahoo(f"{r['ticker']}{YAHOO[r['market']]}", 1)
        except Exception:
            continue
        first = next((v for d, v in hist if d >= r["listing_date"]), None)
        if first and offer:
            with get_engine().begin() as conn:
                conn.execute(update(ipos).where(ipos.c.uid == r["uid"]).values(first_day=first / offer - 1, status="listed"))


def listing(start: date | None = None, end: date | None = None) -> list[dict]:
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(select(ipos)).mappings()]
    if start or end:
        rows = [r for r in rows if r.get("listing_date") and (not start or r["listing_date"] >= start)
                and (not end or r["listing_date"] <= end)]
    return rows



# ---- following IPOs and alerts ---------------------------------------------------------------------------------------
def starred(user_id: int) -> set[str]:
    with get_engine().connect() as conn:
        return set(conn.execute(select(ipo_stars.c.uid).where(ipo_stars.c.user_id == user_id)).scalars())


def toggle_star(user_id: int, uid: str) -> bool:
    with get_engine().begin() as conn:
        if conn.execute(delete(ipo_stars).where(ipo_stars.c.user_id == user_id, ipo_stars.c.uid == uid)).rowcount:
            return False
        row = conn.execute(select(ipos.c.status, ipos.c.listing_date).where(ipos.c.uid == uid)).first()
        conn.execute(insert(ipo_stars).values(user_id=user_id, uid=uid, last_status=row[0] if row else None,
                                              last_date=row[1] if row else None, moved=False))
        return True


def _line(r: dict) -> str:
    import html as h
    when = f'{r["listing_date"]:%d %b}' if r.get("listing_date") else "date not set"
    price = ""
    if r.get("price_low"):
        price = f', {r["price_low"]:,.2f}' + (f'–{r["price_high"]:,.2f}' if r.get("price_high") and r["price_high"] != r["price_low"] else "") + f' {r.get("currency") or ""}'
    link = f' <a href="{h.escape(r["doc_url"])}">document</a>' if r.get("doc_url") else ""
    return (f'• <b>{h.escape(r["name"])}</b> {h.escape(r.get("ticker") or "")} ({h.escape(r.get("country") or "")}, '
            f'{h.escape(r.get("sector") or "Other")}): lists {when}{price}{link}')


def send_alerts() -> int:
    """Worker: new IPOs matching a member's countries/sectors, and changes to IPOs they follow."""
    from core.auth import get_user
    from . import deliver, settings, watch
    from .signals import once
    from core.db import user_settings
    sent = 0
    today = date.today()
    with get_engine().connect() as conn:
        rows = {r["uid"]: dict(r) for r in conn.execute(select(ipos)).mappings()}
        members = set(conn.execute(select(user_settings.c.user_id).where(user_settings.c.key == "ipo_alert_countries")).scalars())
        stars = [dict(r) for r in conn.execute(select(ipo_stars)).mappings()]
    # 1) new IPOs (wait until details are read, or a day has passed, so the sector is known)
    for uid in members:
        prefs = settings.get(uid)
        countries, sectors = prefs.get("ipo_alert_countries") or [], prefs.get("ipo_alert_sectors") or []
        if not countries:
            continue
        fresh = [r for r in rows.values() if r.get("country") in countries and (not sectors or (r.get("sector") or "Other") in sectors)
                 and r.get("first_seen") and r["first_seen"] >= today - timedelta(days=3)
                 and (r.get("ai_done") or r["first_seen"] < today) and once(f"ipo:{uid}:{r['uid']}")]
        if fresh:
            sent += deliver.send(uid, "New IPOs", "<b>New IPOs</b>\n" + "\n".join(_line(r) for r in fresh[:25]))
    # 2) followed IPOs: price set, date set or changed, listed (with first-day move); move to the watchlist once listed
    for s in stars:
        r = rows.get(s["uid"])
        if not r:
            continue
        notes = []
        if r.get("status") != s.get("last_status") and r.get("status") in ("priced", "listed"):
            notes.append("has priced" if r["status"] == "priced" else "is now listed")
        if r.get("listing_date") and r["listing_date"] != s.get("last_date"):
            notes.append(f'listing date {"set to" if not s.get("last_date") else "moved to"} {r["listing_date"]:%d %b %Y}')
        if r.get("first_day") is not None and once(f"ipofd:{s['user_id']}:{r['uid']}"):
            notes.append(f'first day {r["first_day"] * 100:+.1f}% vs offer price')
        if notes:
            sent += deliver.send(s["user_id"], f'IPO update: {r["name"]}', f'<b>IPO update</b>\n{_line(r)}\n' + "; ".join(notes).capitalize() + ".")
        moved = s.get("moved")
        if not moved and r.get("status") == "listed" and r.get("ticker") and r["market"] in ("US", "JP", "HK", "AU"):
            user = get_user(s["user_id"])
            try:
                ticker = r["ticker"].zfill(4) if r["market"] == "HK" else r["ticker"]
                _, err = watch.add(user, r["market"], ticker) if user else (None, "no user")
                moved = err is None or "already" in (err or "").lower()
            except Exception:
                moved = False
            if moved:
                sent += deliver.send(s["user_id"], f'{r["name"]} added to your watchlist',
                                     f'<b>{r["name"]}</b> has listed and was added to your watchlist, so its filings will now appear.')
        with get_engine().begin() as conn:
            conn.execute(update(ipo_stars).where(ipo_stars.c.user_id == s["user_id"], ipo_stars.c.uid == s["uid"])
                         .values(last_status=r.get("status"), last_date=r.get("listing_date"), moved=bool(moved)))
    return sent
