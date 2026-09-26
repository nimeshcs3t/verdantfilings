"""Australia: ASX company announcements, read from the data service behind the ASX website.

Unofficial: ASX sells the official feed (ComNews). The website's data needs no key, but ASX can change
or block it, and its website terms limit commercial reuse. Switch it on with ENABLE_ASX = "true";
set it to "false" to turn Australia off without touching anything else.

Telegram alerts: only announcements ASX marks as price sensitive, unless ASX_ALERTS = "all".
"""
from __future__ import annotations

import csv
import io
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .kr_dart import tidy_english_name

API = "https://asx.api.markitdigital.com/asx-research/1.0"
LISTING_CSV = f"{API}/companies/directory/file"
PDF = "https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/{}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/140.0.0.0 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Origin": "https://www.asx.com.au",
           "Referer": "https://www.asx.com.au/"}
PAUSE = 0.3
MAX_PDF_BYTES = 15_000_000
MAX_PDF_PAGES = 15


class AsxSource(FilingSource):
    market = "AU"
    country = "Australia"
    regulator = "ASX"
    source_lang = "en"
    timezone = "Australia/Sydney"
    ticker_hint = "ASX code, e.g. BHP or 14D"
    news_local = None
    attribution = "Source: ASX company announcements"
    backfill_days = 60
    incremental_days = 3

    def __init__(self):
        self._listing: list[Company] | None = None
        self._listing_at = 0.0
        self._lock = threading.Lock()
        self._last_call = 0.0

    # ---- plumbing -------------------------------------------------------
    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_ASX", "false")).lower() in {"1", "true", "yes"}

    def _get(self, url: str, **kwargs) -> requests.Response:
        wait = PAUSE - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()
        r = requests.get(url, headers=HEADERS, timeout=60, **kwargs)
        r.raise_for_status()
        return r

    # ---- companies ------------------------------------------------------
    def all_listed(self) -> list[Company]:
        with self._lock:
            if self._listing is not None and time.time() - self._listing_at < 86400:
                return self._listing
            text = self._get(LISTING_CSV).content.decode("utf-8-sig", errors="replace")
            rows = list(csv.reader(io.StringIO(text)))
            header = [h.strip().lower() for h in rows[0]]
            i_code = next(i for i, h in enumerate(header) if "code" in h)
            i_name = next(i for i, h in enumerate(header) if "name" in h)
            out = []
            for row in rows[1:]:
                if len(row) > max(i_code, i_name) and row[i_code].strip():
                    code = row[i_code].strip().upper()
                    name = tidy_english_name(row[i_name]) or code
                    out.append(Company(self.market, code, code, name, name))
            self._listing, self._listing_at = out, time.time()
            return out

    def normalize_ticker(self, raw: str) -> str | None:
        t = re.sub(r"\.(AX|ASX)$", "", (raw or "").strip().upper())
        t = re.sub(r"^ASX:", "", t)
        return t if re.fullmatch(r"[A-Z0-9]{3,6}", t) else None

    def resolve(self, ticker: str) -> Company | None:
        t = self.normalize_ticker(ticker)
        return next((c for c in self.all_listed() if c.ticker == t), None) if t else None

    def search(self, query: str, limit: int = 8) -> list[Company]:
        q = query.strip().lower()
        if len(q) < 2:
            return []
        hits = [c for c in self.all_listed() if q == c.ticker.lower() or q in c.name_en.lower()]
        hits.sort(key=lambda c: (q not in (c.ticker.lower(), c.name_en.lower()), len(c.name_en)))
        return hits[:limit]

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("ASX company page", f"https://www.asx.com.au/markets/company/{company.ticker.lower()}")]

    def should_alert(self, row: dict) -> bool:
        if str(get_secret("ASX_ALERTS", "price_sensitive")).lower() == "all":
            return True
        return bool(row.get("price_sensitive"))

    # ---- filings --------------------------------------------------------
    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        data = self._get(f"{API}/companies/{company.ticker.lower()}/announcements",
                         params={"count": 50, "itemsPerPage": 50}).json()
        items = (data.get("data") or {}).get("items") or []
        tz = ZoneInfo(self.timezone)
        out = []
        for it in items:
            key, headline = it.get("documentKey"), (it.get("headline") or "").strip()
            if not key or not headline:
                continue
            try:
                released = datetime.fromisoformat(str(it.get("date")).replace("Z", "+00:00")).astimezone(tz).date()
            except ValueError:
                continue
            if not start <= released <= end:
                continue
            kind = (it.get("announcementType") or "").strip()
            title = headline if not kind or kind.lower() in headline.lower() else f"{headline} ({kind})"
            out.append(Filing(uid=f"AU:{key}", market=self.market, ticker=company.ticker,
                              company_name=company.name_en, filed_date=released, title_local=title,
                              filer=company.name_en, url=it.get("url") or PDF.format(key), title_en=title,
                              price_sensitive=bool(it.get("isPriceSensitive"))))
        return out

    def fetch_document_text(self, uid: str) -> str:
        from pypdf import PdfReader
        key = uid.split(":", 1)[1]
        r = self._get(PDF.format(key), stream=True)
        chunks, size = [], 0
        for chunk in r.iter_content(65536):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_PDF_BYTES:
                return ""
        content = b"".join(chunks)
        if content[:4] != b"%PDF":
            return ""
        reader = PdfReader(io.BytesIO(content))
        pages = [(page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES]]
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in "\n".join(pages).splitlines()]
        return "\n".join(ln for ln in lines if ln)


def _probe() -> None:
    src = AsxSource()
    listed = src.all_listed()
    print(f"Listed companies: {len(listed)}. Sample: " + ", ".join(f"{c.ticker}={c.name_en}" for c in listed[:6]))
    bhp = src.resolve("BHP")
    if bhp:
        for f in src.list_filings(bhp, src.today() - timedelta(days=60), src.today()):
            print(" ", f.filed_date, "PS" if f.price_sensitive else "  ", f.title_en, f.url)


if __name__ == "__main__":
    _probe()
