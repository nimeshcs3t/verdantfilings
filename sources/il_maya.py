"""Israel: company reports from MAYA, the Tel Aviv Stock Exchange's public disclosure website.

Unofficial and for personal use: TASE sells the official feed (see il_tase.py, used instead when TASE_API_KEY is
set). The site sits behind a commercial firewall; this source uses only three addresses known to work, keeps
requests light, and if the site ever refuses a request it pauses itself for 24 hours instead of retrying.
Israel is shown to admin accounts only and never posted to the public Telegram channel.
Switch on with ENABLE_MAYA = "true".

Run `python -m sources.il_maya` to check access and see a sample.
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests

from core.config import get_secret

from .base import Company, Filing, FilingSource, SourceBusy
from .kr_dart import tidy_english_name

log = logging.getLogger(__name__)
COMPANY_LIST = "https://api.tase.co.il/api/content/searchentities"
REPORTS = "https://mayaapi.tase.co.il/api/company/reports"
REPORT_PAGE = "https://maya.tase.co.il/reports/details/{}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/140.0.0.0 Safari/537.36",
           "Accept-Language": "he-IL,he;q=0.9,en;q=0.8", "Origin": "https://maya.tase.co.il", "X-Maya-With": "allow"}
# MAYA's English view lists only reports available in English (mostly the exchange's own notices); the Hebrew
# view lists every company report, so reports are read in Hebrew and their titles translated.
PAUSE = 1.0                 # at most one request a second
BLOCK_HOURS = 24
MAX_PAGES = 3               # 30 reports a page


class MayaWebSource(FilingSource):
    market = "IL"
    country = "Israel"
    regulator = "MAYA"
    source_lang = "iw"            # Hebrew (titles are translated to English)
    timezone = "Asia/Jerusalem"
    ticker_hint = "TASE symbol, e.g. LUMI, or company name"
    news_local = {"hl": "he", "gl": "IL", "ceid": "IL:he"}
    attribution = "Source: MAYA, Tel Aviv Stock Exchange (personal use)"
    backfill_days = 180
    incremental_days = 3
    admin_only = True

    def __init__(self):
        self._session: requests.Session | None = None
        self._listing: list[Company] | None = None
        self._listing_at = 0.0
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.websites: dict[str, str] = {}      # company id -> website, seen in report data

    # ---- plumbing -------------------------------------------------------
    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_MAYA", "false")).lower() in {"1", "true", "yes"}

    @staticmethod
    def blocked_until() -> datetime | None:
        from core.usage import get_state
        value = get_state("maya_blocked_until")
        try:
            until = datetime.fromisoformat(value) if value else None
        except ValueError:
            return None
        return until if until and until > datetime.now(timezone.utc) else None

    def _block(self, reason: str) -> None:
        from core.usage import set_state
        until = datetime.now(timezone.utc) + timedelta(hours=BLOCK_HOURS)
        set_state("maya_blocked_until", until.isoformat())
        log.warning("MAYA refused a request (%s); Israel paused for %d hours", reason, BLOCK_HOURS)

    def _get(self, url: str, params: dict, referer: str) -> dict | list:
        if self.blocked_until():
            raise SourceBusy("MAYA paused")
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
        wait = PAUSE - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()
        r = self._session.get(url, params=params, headers={"Referer": referer}, timeout=60)
        if r.status_code in (401, 403, 429) or "json" not in (r.headers.get("content-type") or ""):
            self._block(f"status {r.status_code}")
            raise SourceBusy("MAYA refused the request")
        r.raise_for_status()
        return r.json()

    # ---- companies ------------------------------------------------------
    def all_listed(self) -> list[Company]:
        with self._lock:
            if self._listing is not None and time.time() - self._listing_at < 86400:
                return self._listing
            english = self._get(COMPANY_LIST, {"lang": 1}, "https://www.tase.co.il/")
            hebrew = self._get(COMPANY_LIST, {"lang": 0}, "https://www.tase.co.il/")   # names; lang decides, not the header
            names_en = {str(e.get("Id")): e.get("Name") or "" for e in english if e.get("Type") == 5}
            names_he = {str(e.get("Id")): e.get("Name") or "" for e in hebrew if e.get("Type") == 5}
            out, seen = [], set()
            for e in english:
                company_id, symbol = str(e.get("SubId") or ""), (e.get("Smb") or "").strip().upper()
                if e.get("Type") != 1 or not symbol or company_id not in names_en or company_id in seen:
                    continue
                seen.add(company_id)
                name_en = tidy_english_name(names_en[company_id]) or symbol
                out.append(Company(self.market, symbol, company_id, names_he.get(company_id) or name_en, name_en))
            self._listing, self._listing_at = out, time.time()
            return out

    def normalize_ticker(self, raw: str) -> str | None:
        t = (raw or "").strip().upper().removesuffix(".TA")
        return t if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", t) else None

    def resolve(self, ticker: str) -> Company | None:
        t = self.normalize_ticker(ticker)
        return next((c for c in self.all_listed() if c.ticker == t), None) if t else None

    def search(self, query: str, limit: int = 8) -> list[Company]:
        q = query.strip().lower()
        if len(q) < 2:
            return []
        hits = [c for c in self.all_listed() if q == c.ticker.lower() or q in c.name_en.lower() or q in c.name_local.lower()]
        hits.sort(key=lambda c: (q not in (c.ticker.lower(), c.name_en.lower()), len(c.name_en)))
        return hits[:limit]

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("MAYA company page", f"https://maya.tase.co.il/company/{company.source_id}")]

    # ---- reports --------------------------------------------------------
    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        out = []
        for page in range(1, MAX_PAGES + 1):
            data = self._get(REPORTS, {"companyId": company.source_id, **({"page": page} if page > 1 else {})},
                             "https://maya.tase.co.il/")
            reports = data.get("Reports") or []
            oldest = None
            for rep in reports:
                site = ((rep.get("FormalCompanyData") or {}).get("URL") or "").strip()
                if site:
                    self.websites[company.source_id] = site
                try:
                    published = datetime.fromisoformat(str(rep.get("PubDate"))[:19]).date()
                except ValueError:
                    continue
                oldest = published if oldest is None else min(oldest, published)
                if not start <= published <= end or not rep.get("RptCode"):
                    continue
                subject = re.sub(r"\s+", " ", rep.get("Subject") or "").strip() or "Company report"
                english = subject if subject.isascii() or not re.search(r"[\u0590-\u05FF]", subject) else ""
                out.append(Filing(uid=f"IL:{rep['RptCode']}", market=self.market, ticker=company.ticker,
                                  company_name=company.name_en, filed_date=published, title_local=subject,
                                  filer=company.name_local, url=REPORT_PAGE.format(rep["RptCode"]), title_en=english))
            if not reports or (oldest and oldest < start) or page >= int(data.get("TotalPages") or 1):
                break
        return out

    def fetch_document_text(self, uid: str) -> str:
        return ""     # report documents aren't read yet; titles and links only


def _probe() -> None:
    src = MayaWebSource()
    listed = src.all_listed()
    print(f"Companies with a share symbol: {len(listed)}. Sample: "
          + ", ".join(f"{c.ticker}={c.name_en}" for c in listed[:6]))
    leumi = src.resolve("LUMI")
    if leumi:
        for f in src.list_filings(leumi, src.today() - timedelta(days=14), src.today())[:8]:
            print(" ", f.filed_date, f.title_en or f.title_local, f.url)


if __name__ == "__main__":
    _probe()
