"""Hong Kong: HKEXnews announcements (English), from the public stock list and title search the website uses.
Unofficial, for personal use. Switch on with ENABLE_HKEX = "true"."""
from __future__ import annotations

import html
import re
import threading
import time
from datetime import date, datetime, timedelta

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .kr_dart import tidy_english_name
from .doctext import pdf_text
from .webhttp import PoliteClient

BASE = "https://www1.hkexnews.hk"
STOCKS = f"{BASE}/ncms/script/eds/activestock_sehk_e.json"
SEARCH = f"{BASE}/search/titleSearchServlet.do"


def _clean(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<br\s*/?>", " ", text or "")).split())


class HkexSource(FilingSource):
    market = "HK"
    country = "Hong Kong"
    regulator = "HKEXnews"
    source_lang = "en"
    timezone = "Asia/Hong_Kong"
    ticker_hint = "HKEX code, e.g. 0700 or 700"
    news_local = {"hl": "zh-HK", "gl": "HK", "ceid": "HK:zh-Hant"}
    attribution = "Source: HKEXnews (personal use)"
    backfill_days = 180
    incremental_days = 3

    def __init__(self):
        self.http = PoliteClient("hkex", pause=0.5, headers={"Referer": "https://www1.hkexnews.hk/search/titlesearch.xhtml"})
        self._listing, self._at, self._lock = None, 0.0, threading.Lock()

    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_HKEX", "false")).lower() in {"1", "true", "yes"}

    @staticmethod
    def _ticker(code: str) -> str:
        code = str(code).strip().zfill(5)
        return code[1:] if code.startswith("0") else code     # 00700 -> 0700 (as on Yahoo: 0700.HK)

    def all_listed(self) -> list[Company]:
        with self._lock:
            if self._listing is None or time.time() - self._at > 86400:
                out = []
                for s in self.http.get(STOCKS).json():
                    code = str(s.get("c") or "")
                    if code.isdigit() and int(code) < 10000:          # shares only (skips warrants, CBBCs, RMB lines)
                        name = " ".join(str(s.get("n") or "").split())
                        out.append(Company(self.market, self._ticker(code), str(s.get("i")), name, tidy_english_name(name)))
                self._listing, self._at = out, time.time()
            return self._listing

    def normalize_ticker(self, raw: str) -> str | None:
        t = re.sub(r"\.HK$", "", (raw or "").strip().upper())
        return self._ticker(t) if t.isdigit() and 0 < int(t) < 100000 else None

    def resolve(self, ticker: str) -> Company | None:
        t = self.normalize_ticker(ticker)
        return next((c for c in self.all_listed() if c.ticker == t), None) if t else None

    def search(self, query: str, limit: int = 8) -> list[Company]:
        q = query.strip().lower()
        hits = [c for c in self.all_listed() if q in c.name_en.lower() or q == c.ticker.lstrip("0")]
        hits.sort(key=lambda c: (q != c.name_en.lower(), len(c.name_en)))
        return hits[:limit]

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("HKEXnews", f"https://www.hkexnews.hk/index.htm")]

    def _search(self, stock_id: str, start: date, end: date, rows: int = 100) -> list[dict]:
        r = self.http.get(SEARCH, params={
            "sortDir": 0, "sortByOptions": "DateTime", "category": 0, "market": "SEHK", "stockId": stock_id,
            "documentType": -1, "fromDate": f"{start:%Y%m%d}", "toDate": f"{end:%Y%m%d}", "title": "",
            "searchType": 0, "t1code": -2, "t2Gcode": -2, "t2code": -2, "rowRange": rows, "lang": "E"})
        import json
        return json.loads(r.json().get("result") or "[]")

    def _filing(self, row: dict, company: Company) -> Filing | None:
        try:
            when = datetime.strptime(row.get("DATE_TIME", ""), "%d/%m/%Y %H:%M").date()
        except ValueError:
            return None
        title = _clean(row.get("TITLE"))
        detail = re.search(r"\[(.+?)\]", _clean(row.get("LONG_TEXT")))
        if detail and detail.group(1).lower() not in title.lower():
            title = f"{title} ({detail.group(1)})"
        return Filing(uid=f"HK:{row.get('NEWS_ID')}", market=self.market, ticker=company.ticker,
                      company_name=company.name_en, filed_date=when, title_local=title, filer=company.name_local,
                      url=BASE + (row.get("FILE_LINK") or ""), title_en=title)

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        return [f for f in (self._filing(r, company) for r in self._search(company.source_id, start, end))
                if f and start <= f.filed_date <= end]

    def market_feed(self) -> list[dict]:
        out = []
        for r in self._search("-1", self.today() - timedelta(days=1), self.today(), rows=200):
            code = re.split(r"<br", str(r.get("STOCK_CODE") or ""))[0].strip()
            if not code.isdigit():
                continue
            fake = Company(self.market, self._ticker(code), "", _clean(r.get("STOCK_NAME")).split(" ")[0], "")
            f = self._filing(r, fake)
            if f:
                out.append({"uid": f.uid, "ticker": f.ticker, "company": re.split(r"<br", str(r.get("STOCK_NAME") or ""))[0].strip(),
                            "title_local": f.title_local, "title_en": f.title_en, "url": f.url, "date": f.filed_date})
        return out

    def fetch_document_text(self, uid: str) -> str:
        from core.db import filings, get_engine
        from sqlalchemy import select
        with get_engine().connect() as conn:
            url = conn.execute(select(filings.c.url).where(filings.c.uid == uid)).scalar()
        if not url:
            return ""
        r = self.http.get(url)
        return pdf_text(r.content) if url.lower().endswith(".pdf") else ""
