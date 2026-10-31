"""US over-the-counter companies that don't report to the SEC (OTC companies that do are already covered by the
SEC source). Reads news releases and financial reports from OTC Markets' public website data, with Google News
wire headlines as a fallback. Foreign shares (ADRs, F-shares) also point to their home listing, which is where
their real filings are. Unofficial, for personal use. Switch on with ENABLE_OTC = "true"."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .doctext import any_text, html_text
from .webhttp import PoliteClient

API = "https://backend.otcmarkets.com/otcapi"
SITE = "https://www.otcmarkets.com"
WIRES = ("globenewswire", "accesswire", "newsfile", "prnewswire", "businesswire", "otcmarkets", "einpresswire")


def _when(value) -> date | None:
    """Dates come as epoch milliseconds, 'MM/DD/YYYY' or ISO text."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        v = float(value)
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc).date()
    t = str(value).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        return date(int(m[3]), int(m[1]), int(m[2]))
    try:
        return datetime.strptime(t, "%b %d, %Y").date()
    except ValueError:
        return None


def _records(data) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for k in ("records", "items", "data", "results", "news"):
            if isinstance(data.get(k), list):
                return [r for r in data[k] if isinstance(r, dict)]
    return []


class OtcSource(FilingSource):
    market = "OT"
    country = "US OTC"
    regulator = "OTC Markets"
    source_lang = "en"
    timezone = "America/New_York"
    ticker_hint = "OTC symbol, e.g. TAKOF or KWHIY"
    attribution = "Source: OTC Markets public website data and newswires (personal use)"
    backfill_days = 180
    incremental_days = 5
    resolve_in_app = True

    def __init__(self):
        self.http = PoliteClient("otc_markets", pause=0.8, headers={"Origin": SITE, "Referer": f"{SITE}/"})

    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_OTC", "false")).lower() in {"1", "true", "yes"}

    def normalize_ticker(self, raw: str) -> str | None:
        t = re.sub(r"^(OTC|PNK|OTCMKTS)[:\s]+", "", (raw or "").strip().upper())
        return t if re.fullmatch(r"[A-Z]{2,6}", t) else None

    def _json(self, path: str, **params):
        try:
            return self.http.get(f"{API}{path}", params=params or None).json()
        except Exception as exc:
            if exc.__class__.__name__ == "SourceBusy":
                raise
            return None

    def _profile(self, sym: str) -> dict:
        data = self._json(f"/company/profile/full/{sym}", symbol=sym)
        return data if isinstance(data, dict) else {}

    def resolve(self, ticker: str) -> Company | None:
        from services.prices import symbol_details
        t = self.normalize_ticker(ticker)
        if not t:
            return None
        prof = self._profile(t)
        name = prof.get("name") or (prof.get("securities") or [{}])[0].get("issuerName") if prof else None
        if not name:
            details = symbol_details(t)
            ex = (details or {}).get("exchange", "")
            if not details or not re.search(r"OTC|Pink|PNK|Other", ex, re.I):
                return None
            name = details["name"]
        return Company(self.market, t, t, name, name)

    def search(self, query: str, limit: int = 8) -> list[Company]:
        """OTC symbols matching the query, with each foreign company's home listing first when the app has it."""
        from services.home_listing import find
        from services.prices import yahoo_search
        out = []
        hits = [h for h in yahoo_search(query, ("PNK", "OQX", "OQB", "OTC", "NCM", "OBB"), limit)]
        t = self.normalize_ticker(query)
        if t and not any(h["symbol"] == t for h in hits):
            comp = self.resolve(t)
            if comp:
                hits.insert(0, {"symbol": t, "longname": comp.name_en})
        for h in hits:
            name = h.get("longname") or h.get("shortname") or h["symbol"]
            home = find(h["symbol"], name)
            if home:
                m, ticker, home_name = home
                out.append(Company(m, ticker, "", f"{home_name} (home listing of {h['symbol']})", home_name))
            out.append(Company(self.market, h["symbol"], h["symbol"], name, name))
        return out[:limit]

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("OTC Markets", f"{SITE}/stock/{company.ticker}/disclosure")]

    def _news(self, sym: str) -> list[dict]:
        out = []
        for path, params, kind in ((f"/company/dns/news/{sym}", {"symbol": sym, "page": 1, "pageSize": 50}, "news"),
                                   ("/company/financial-report/", {"symbol": sym, "page": 1, "pageSize": 50, "statusId": "A",
                                                                   "sortOn": "releaseDate", "sortDir": "DESC"}, "report")):
            for r in _records(self._json(path, **params)):
                title = " ".join(str(r.get("title") or r.get("name") or r.get("typeName") or "").split())
                when = _when(r.get("releaseDate") or r.get("periodDate") or r.get("createdDate") or r.get("date"))
                rid = r.get("id") or r.get("newsId")
                if not title or not when or not rid:
                    continue
                if kind == "report" and r.get("typeName") and r["typeName"] not in title:
                    title = f"{r['typeName']}: {title}" if title != r["typeName"] else title
                url = (f"{API}/company/financial-report/{rid}/content" if kind == "report"
                       else f"{SITE}/stock/{sym}/news")
                out.append({"uid": f"OT:{'r' if kind == 'report' else 'n'}{rid}", "date": when, "title": title,
                            "kind": "Financial report" if kind == "report" else "News release", "url": url})
        return out

    def _wire_news(self, comp: Company, start: date, end: date) -> list[dict]:
        from datetime import timedelta
        from services.news import google_news
        out = []
        q = f'"{comp.name_en}" after:{start - timedelta(days=1)} before:{end + timedelta(days=1)}'
        for item in google_news(q, limit=60):
            if not any(w in (item.get("source") or "").lower().replace(" ", "") for w in WIRES):
                continue
            when = item["published"].date() if item.get("published") else None
            if when:
                key = hashlib.sha1(f"{comp.ticker}|{item['title'].lower()}".encode()).hexdigest()[:16]
                out.append({"uid": f"OT:g{key}", "date": when, "title": item["title"], "kind": "Press release",
                            "url": item.get("link") or ""})
        return out

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        items = self._news(company.ticker)
        if not items:
            items = self._wire_news(company, start, end)
        return [Filing(uid=i["uid"], market=self.market, ticker=company.ticker, company_name=company.name_en,
                       filed_date=i["date"], title_local=i["title"], filer=i["kind"], url=i["url"], title_en=i["title"])
                for i in items if start <= i["date"] <= end]

    def fetch_document_text(self, uid: str) -> str:
        kind, rid = uid.split(":", 1)[1][:1], uid.split(":", 1)[1][1:]
        if kind == "r":
            r = self.http.get(f"{API}/company/financial-report/{rid}/content")
            return any_text(r.content, r.headers.get("content-type") or "")
        if kind == "n":
            data = self._json(f"/company/dns/news/{rid}")
            body = (data or {}).get("body") or (data or {}).get("content") or "" if isinstance(data, dict) else ""
            return html_text(body) if "<" in body else body
        return ""
