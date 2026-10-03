"""Taiwan: material information of TWSE-listed companies from the exchange's official open data (OpenAPI).
The feed lists the current day's announcements with full text, so history builds up from when it's switched on.
Switch on with ENABLE_TWSE = "true"."""
from __future__ import annotations

import hashlib
import re
import threading
import time
from datetime import date

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .webhttp import PoliteClient

API = "https://openapi.twse.com.tw/v1/opendata"
MOPS = "https://mops.twse.com.tw/mops/#/web/t05st01"


def roc_date(text: str) -> date | None:
    """'1151002' (Republic of China calendar) -> 2026-10-02."""
    t = re.sub(r"\D", "", str(text or ""))
    if len(t) < 6:
        return None
    try:
        return date(int(t[:-4]) + 1911, int(t[-4:-2]), int(t[-2:]))
    except ValueError:
        return None


class TwseSource(FilingSource):
    market = "TW"
    country = "Taiwan"
    regulator = "TWSE"
    source_lang = "zh-TW"
    timezone = "Asia/Taipei"
    ticker_hint = "TWSE code, e.g. 2330"
    news_local = {"hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}
    attribution = "Source: Taiwan Stock Exchange open data"
    backfill_days = 7
    incremental_days = 3

    def __init__(self):
        self.http = PoliteClient("twse", pause=0.3)
        self._listing, self._at = None, 0.0
        self._today, self._today_at = None, 0.0
        self._lock = threading.Lock()
        self.websites: dict[str, str] = {}

    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_TWSE", "false")).lower() in {"1", "true", "yes"}

    def all_listed(self) -> list[Company]:
        with self._lock:
            if self._listing is None or time.time() - self._at > 86400:
                out = []
                for c in self.http.get(f"{API}/t187ap03_L").json():
                    code = str(c.get("公司代號") or "").strip()
                    if not code:
                        continue
                    short = (c.get("公司簡稱") or c.get("公司名稱") or code).strip()
                    english = (c.get("英文簡稱") or "").strip() or short
                    out.append(Company(self.market, code, code, short, english))
                    if (c.get("網址") or "").strip():
                        self.websites[code] = c["網址"].strip()
                self._listing, self._at = out, time.time()
            return self._listing

    def normalize_ticker(self, raw: str) -> str | None:
        t = re.sub(r"\.TWO?$", "", (raw or "").strip().upper())
        return t if re.fullmatch(r"\d{4,6}[A-Z]?", t) else None

    def resolve(self, ticker: str) -> Company | None:
        t = self.normalize_ticker(ticker)
        return next((c for c in self.all_listed() if c.ticker == t), None) if t else None

    def search(self, query: str, limit: int = 8) -> list[Company]:
        q = query.strip().lower()
        hits = [c for c in self.all_listed() if q == c.ticker or q in c.name_en.lower() or q in c.name_local.lower()]
        return hits[:limit]

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("MOPS", MOPS)]

    def _announcements(self) -> list[dict]:
        with self._lock:
            if self._today is None or time.time() - self._today_at > 300:
                self._today, self._today_at = self.http.get(f"{API}/t187ap04_L").json(), time.time()
            return self._today

    def _filing(self, a: dict, name_en: str = "") -> Filing | None:
        code = str(a.get("公司代號") or "").strip()
        day = roc_date(a.get("發言日期"))
        subject = " ".join(str(a.get("主旨 ") or a.get("主旨") or "").split())
        if not code or not day or not subject:
            return None
        key = hashlib.md5(subject.encode("utf-8")).hexdigest()[:8]
        body = str(a.get("說明") or "").replace("\r\n", "\n").strip()
        return Filing(uid=f"TW:{code}:{day:%Y%m%d}{str(a.get('發言時間') or '').zfill(6)}:{key}", market=self.market,
                      ticker=code, company_name=name_en or str(a.get("公司名稱") or code), filed_date=day,
                      title_local=subject[:500], filer=str(a.get("公司名稱") or ""), url=MOPS, body=body)

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        out = []
        for a in self._announcements():
            if str(a.get("公司代號") or "").strip() == company.ticker:
                f = self._filing(a, company.name_en)
                if f and start <= f.filed_date <= end:
                    out.append(f)
        return out

    def market_feed(self) -> list[dict]:
        out = []
        for a in self._announcements():
            f = self._filing(a)
            if f:
                out.append({"uid": f.uid, "ticker": f.ticker, "company": f.company_name, "title_local": f.title_local,
                            "title_en": "", "url": f.url, "date": f.filed_date})
        return out

    def fetch_document_text(self, uid: str) -> str:
        return ""        # the full text is stored when the announcement is first seen
