"""Israel: Tel Aviv Stock Exchange (TASE) Data Hub, "DataWise" API. Key at https://datahub.tase.co.il

- Company list: free TASE endpoints (basic-securities).
- Announcements: the paid "Market Announcements feed (MAYA)" product, with a free trial of
  100 requests per 4 weeks. Its endpoint path is shown in the TASE developer portal after you
  subscribe; put it in the TASE_MAYA_PATH setting, e.g. "some-group/reports-by-date/{yyyy}/{m}/{d}".
  Placeholders: {yyyy} {m} {d} {mm} {dd} {date} (YYYY-MM-DD).

Run `python -m sources.il_tase` (or the "TASE probe" GitHub workflow) to print sample responses.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from datetime import date, datetime, timedelta

import requests
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from core.config import get_secret
from core.db import api_usage, as_utc, get_engine, utcnow

from .base import Company, Filing, FilingSource, SourceBusy
from .kr_dart import tidy_english_name

API = "https://datawise.tase.co.il/v1"
MAYA_REPORT_URL = "https://maya.tase.co.il/reports/details/{}"

# The MAYA response format isn't public, so fields are matched by the likely names.
ID_KEYS = ("reportId", "reportID", "reportNumber", "rptId", "id")
ISSUER_KEYS = ("issuerId", "companyId", "issuerNumber", "companyNumber")
TITLE_KEYS = ("reportTitle", "title", "subject", "reportSubject", "header", "reportName", "description")
DATE_KEYS = ("publishDate", "publicationDate", "pubDate", "reportDate", "publishTime", "date")
URL_KEYS = ("reportUrl", "url", "reportLink", "link", "pdfUrl")


class TaseError(RuntimeError):
    pass


def _results(data) -> list[dict]:
    """First list of objects anywhere in the response (TASE wraps results as {"xList": {"result": [...]}})."""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in ("result", "results", "data", "items"):
            if isinstance(data.get(key), list):
                return _results(data[key])
        for value in data.values():
            found = _results(value)
            if found:
                return found
    return []


def _pick(item: dict, keys) -> object:
    lowered = {k.lower(): v for k, v in item.items()}
    for k in keys:
        v = lowered.get(k.lower())
        if v not in (None, ""):
            return v
    return None


def _parse_date(value, fallback: date) -> date:
    if isinstance(value, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%d/%m/%Y %H:%M"):
            try:
                return datetime.strptime(value[:19] if "T" in value else value, fmt).date()
            except ValueError:
                continue
    return fallback


class TaseSource(FilingSource):
    market = "IL"
    country = "Israel"
    regulator = "TASE MAYA"
    source_lang = "auto"          # titles may come in Hebrew or English
    timezone = "Asia/Jerusalem"
    ticker_hint = "TASE symbol, e.g. TEVA or LUMI"
    news_local = {"hl": "he", "gl": "IL", "ceid": "IL:he"}
    backfill_days = 2             # one request per day of history, so keep this small
    incremental_days = 0          # today; the first call of each day also covers yesterday

    def __init__(self):
        self._listing: list[Company] | None = None
        self._listing_at = 0.0
        self._day_cache: dict[date, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()
        self._with_yesterday: date | None = None

    # ---- plumbing -------------------------------------------------------
    def _key(self) -> str | None:
        return get_secret("TASE_API_KEY")

    def is_configured(self) -> bool:
        return bool(self._key())

    def _get(self, path: str, lang: str = "en-US", metered: bool = False):
        if metered:
            self._spend()
        r = requests.get(f"{API}/{path.lstrip('/')}", timeout=30, headers={
            "apikey": self._key(), "accept": "application/json", "accept-language": lang})
        if r.status_code in (401, 403):
            raise TaseError(f"TASE refused the request ({r.status_code}). Check TASE_API_KEY and that the "
                            "product is subscribed.")
        if r.status_code == 429:
            raise TaseError("TASE request limit reached.")
        r.raise_for_status()
        return r.json()

    def _usage_today(self):
        with get_engine().connect() as conn:
            return conn.execute(select(api_usage.c.calls, api_usage.c.last_call).where(
                api_usage.c.market == self.market, api_usage.c.day == self.today().isoformat())).first()

    def _spend(self) -> None:
        """Meter calls so the trial (100 requests / 4 weeks) lasts: at most TASE_DAILY_CALL_LIMIT a day,
        spread across the day. Calls within the same run (2 minutes) aren't spaced."""
        limit = int(get_secret("TASE_DAILY_CALL_LIMIT", 3))
        gap = float(get_secret("TASE_MIN_MINUTES_BETWEEN", 1440 / limit if limit <= 48 else 0))
        usage = self._usage_today()
        used, last = (usage[0], as_utc(usage[1])) if usage else (0, None)
        if used >= limit:
            raise SourceBusy("daily TASE budget used")
        if last:
            since = (utcnow() - last).total_seconds() / 60
            if 2 < since < gap:
                raise SourceBusy("spacing TASE calls")
        day, now = self.today().isoformat(), utcnow()
        with get_engine().begin() as conn:
            if usage is None:
                try:
                    conn.execute(insert(api_usage).values(market=self.market, day=day, calls=1, last_call=now))
                    return
                except IntegrityError:
                    pass
            conn.execute(update(api_usage).where(api_usage.c.market == self.market, api_usage.c.day == day)
                         .values(calls=api_usage.c.calls + 1, last_call=now))

    # ---- companies ------------------------------------------------------
    def normalize_ticker(self, raw: str) -> str | None:
        t = (raw or "").strip().upper().removesuffix(".TA")
        return t if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", t) else None

    def _symbols(self) -> dict[int, str]:
        """issuerId -> share symbol, from the latest trading day's securities list (free endpoint)."""
        day = self.today()
        for _ in range(8):
            rows = _results(self._get(f"basic-securities/trade-securities-list/{day.year}/{day.month}/{day.day}"))
            if rows:
                best: dict[int, str] = {}
                for row in rows:
                    issuer, symbol = row.get("issuerId"), (row.get("symbol") or "").strip().upper()
                    if issuer is None or not symbol:
                        continue
                    # Shares normally carry the shortest symbol of an issuer's securities.
                    if issuer not in best or (len(symbol), symbol) < (len(best[issuer]), best[issuer]):
                        best[issuer] = symbol
                return best
            day -= timedelta(days=1)
        return {}

    def all_listed(self) -> list[Company]:
        with self._lock:
            if self._listing is not None and time.time() - self._listing_at < 86400:
                return self._listing
            english = _results(self._get("basic-securities/companies-list", lang="en-US"))
            hebrew = {c.get("issuerId"): c.get("companyName")
                      for c in _results(self._get("basic-securities/companies-list", lang="he-IL"))}
            symbols = self._symbols()
            out = []
            for c in english:
                issuer = c.get("issuerId")
                symbol = symbols.get(issuer)
                if issuer is None or not symbol:
                    continue
                name_en = tidy_english_name(c.get("companyName") or "")
                out.append(Company(self.market, symbol, str(issuer), (hebrew.get(issuer) or name_en).strip(),
                                   name_en or symbol))
            self._listing, self._listing_at = out, time.time()
            return out

    def resolve(self, ticker: str) -> Company | None:
        t = self.normalize_ticker(ticker)
        return next((c for c in self.all_listed() if c.ticker == t), None) if t else None

    def search(self, query: str, limit: int = 8) -> list[Company]:
        q = query.strip().lower()
        if len(q) < 2:
            return []
        hits = [c for c in self.all_listed()
                if q == c.ticker.lower() or q in c.name_en.lower() or q in c.name_local.lower()]
        hits.sort(key=lambda c: (q not in (c.ticker.lower(), c.name_en.lower()), len(c.name_en)))
        return hits[:limit]

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("TASE company page",
                 f"https://market.tase.co.il/en/market_data/company/{company.source_id}/reports_maya")]

    # ---- filings --------------------------------------------------------
    def _maya_path(self, day: date) -> str:
        template = get_secret("TASE_MAYA_PATH")
        if not template:
            raise TaseError("TASE_MAYA_PATH isn't set. Copy the reports-by-date path from the TASE developer portal.")
        return template.format(yyyy=day.year, m=day.month, d=day.day, mm=f"{day.month:02d}",
                               dd=f"{day.day:02d}", date=day.isoformat())

    def _reports_for(self, day: date) -> list[dict]:
        """All MAYA reports for one day, cached for 5 minutes so one worker run makes one call per day."""
        cached = self._day_cache.get(day)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        items = _results(self._get(self._maya_path(day), metered=True))
        self._day_cache[day] = (time.time(), items)
        return items

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        today = self.today()
        if start == end == today and (self._with_yesterday == today or
                                      (not self._usage_today() and not self._day_cache.get(today))):
            self._with_yesterday = today   # first run of the day: also pick up yesterday's late reports
            start -= timedelta(days=1)
        out, day = [], start
        while day <= end:
            for item in self._reports_for(day):
                if str(_pick(item, ISSUER_KEYS)) != company.source_id:
                    continue
                report_id = _pick(item, ID_KEYS)
                title = str(_pick(item, TITLE_KEYS) or "").strip()
                if report_id is None or not title:
                    continue
                url = _pick(item, URL_KEYS) or MAYA_REPORT_URL.format(report_id)
                out.append(Filing(
                    uid=f"IL:{report_id}", market=self.market, ticker=company.ticker, company_name=company.name_en,
                    filed_date=_parse_date(_pick(item, DATE_KEYS), day), title_local=re.sub(r"\s+", " ", title),
                    filer=company.name_local, url=str(url)))
            day += timedelta(days=1)
        return out

    def fetch_document_text(self, uid: str) -> str:
        # Report bodies are PDFs/HTML on MAYA; added once the feed's format is confirmed.
        return ""


def _probe() -> None:
    """Print sample responses so the MAYA field mapping can be checked. Never prints the key."""
    import json
    src = TaseSource()
    if not src.is_configured():
        sys.exit("TASE_API_KEY is not set.")
    show = lambda label, data: print(f"\n=== {label} ===\n" + json.dumps(data, ensure_ascii=False, indent=1)[:3000])
    companies = src._get("basic-securities/companies-list")
    show("companies-list (first items)", _results(companies)[:3])
    print(f"\nListed companies with a symbol: {len(src.all_listed())}. Sample: "
          + ", ".join(f"{c.ticker}={c.name_en}" for c in src.all_listed()[:8]))
    if get_secret("TASE_MAYA_PATH"):
        day = src.today()
        for _ in range(4):
            path = src._maya_path(day)
            data = src._get(path, metered=True)
            items = _results(data)
            show(f"MAYA {path} ({len(items)} items)", data if not items else items[:3])
            if items:
                break
            day -= timedelta(days=1)
    else:
        print("\nTASE_MAYA_PATH is not set, so the MAYA feed was not called.")


if __name__ == "__main__":
    _probe()
