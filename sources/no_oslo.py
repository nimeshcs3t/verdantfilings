"""Norway: Oslo Børs NewsWeb announcements, from the data service the NewsWeb website uses.
Unofficial, for personal use. Switch on with ENABLE_OSLO = "true"."""
from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, timedelta

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .doctext import html_text
from .webhttp import PoliteClient

API = "https://api3.oslo.oslobors.no/v1/newsreader"
PAGE = "https://newsweb.oslobors.no/message/{}"


class OsloSource(FilingSource):
    market = "NO"
    country = "Norway"
    regulator = "Oslo Børs"
    source_lang = "no"
    timezone = "Europe/Oslo"
    ticker_hint = "Oslo ticker, e.g. EQNR"
    news_local = {"hl": "no", "gl": "NO", "ceid": "NO:no"}
    attribution = "Source: Oslo Børs NewsWeb (personal use)"
    backfill_days = 180
    incremental_days = 3
    resolve_in_app = True

    def __init__(self):
        self.http = PoliteClient("oslo", pause=0.5, headers={"Origin": "https://newsweb.oslobors.no",
                                                               "Referer": "https://newsweb.oslobors.no/"})
        self._listing, self._at, self._lock = None, 0.0, threading.Lock()

    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_OSLO", "false")).lower() in {"1", "true", "yes"}

    def _list(self, **params) -> list[dict]:
        return ((self.http.get(f"{API}/list", params=params).json().get("data") or {}).get("messages")) or []

    def all_listed(self) -> list[Company]:
        """Companies that published on NewsWeb in the last 90 days (a month at a time)."""
        with self._lock:
            if self._listing is None or time.time() - self._at > 86400:
                seen: dict[str, Company] = {}
                end = self.today()
                for _ in range(3):
                    start = end - timedelta(days=30)
                    for m in self._list(fromDate=f"{start}", toDate=f"{end}"):
                        sign = (m.get("issuerSign") or "").upper()
                        if sign and sign not in seen:
                            name = m.get("issuerName") or sign
                            seen[sign] = Company(self.market, sign, sign, name, name)
                    end = start - timedelta(days=1)
                self._listing, self._at = list(seen.values()), time.time()
            return self._listing

    def normalize_ticker(self, raw: str) -> str | None:
        t = re.sub(r"\.OL$", "", (raw or "").strip().upper())
        return t if re.fullmatch(r"[A-Z0-9]{1,10}", t) else None

    def resolve(self, ticker: str) -> Company | None:
        t = self.normalize_ticker(ticker)
        if not t:
            return None
        msgs = self._list(issuer=t, fromDate=f"{self.today() - timedelta(days=365)}", toDate=f"{self.today()}")
        m = next((m for m in msgs if (m.get("issuerSign") or "").upper() == t), None)
        return Company(self.market, t, t, m["issuerName"], m["issuerName"]) if m else None

    def search(self, query: str, limit: int = 8) -> list[Company]:
        q = query.strip()
        exact = self.resolve(q) if self.normalize_ticker(q) else None
        if exact:
            return [exact]
        from services.prices import yahoo_search
        out = []
        for hit in yahoo_search(q, ("OSL",), limit):
            sign = hit["symbol"].removesuffix(".OL")
            out.append(Company(self.market, sign, sign, hit.get("longname") or hit.get("shortname") or sign,
                               hit.get("longname") or hit.get("shortname") or sign))
        return out

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("NewsWeb", f"https://newsweb.oslobors.no/search?issuer={company.ticker}")]

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        out = []
        for m in self._list(issuer=company.source_id, fromDate=f"{start}", toDate=f"{end}"):
            if (m.get("issuerSign") or "").upper() != company.source_id.upper() or m.get("test"):
                continue
            try:
                when = datetime.fromisoformat(str(m.get("publishedTime")).replace("Z", "+00:00")).date()
            except ValueError:
                continue
            title = " ".join((m.get("title") or "").split())
            category = ((m.get("category") or [{}])[0] or {}).get("category_en") or ""
            english = title if title.isascii() else ""
            out.append(Filing(uid=f"NO:{m['messageId']}", market=self.market, ticker=company.ticker,
                              company_name=company.name_en, filed_date=when, title_local=title, filer=category,
                              url=PAGE.format(m["messageId"]), title_en=english))
        return [f for f in out if start <= f.filed_date <= end]

    def market_feed(self) -> list[dict]:
        out = []
        for m in self._list(fromDate=f"{self.today() - timedelta(days=1)}", toDate=f"{self.today()}"):
            try:
                when = datetime.fromisoformat(str(m.get("publishedTime")).replace("Z", "+00:00")).date()
            except ValueError:
                continue
            title = " ".join((m.get("title") or "").split())
            out.append({"uid": f"NO:{m['messageId']}", "ticker": (m.get("issuerSign") or "").upper(),
                        "company": m.get("issuerName") or "", "title_local": title,
                        "title_en": title if title.isascii() else "", "url": PAGE.format(m["messageId"]), "date": when})
        return out

    def fetch_document_text(self, uid: str) -> str:
        message_id = uid.split(":", 1)[1]
        m = ((self.http.get(f"{API}/message", params={"messageId": message_id}).json().get("data") or {}).get("message")) or {}
        body = m.get("body") or ""
        return html_text(body) if "<" in body else body
