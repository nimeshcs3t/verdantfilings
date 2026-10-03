"""Japan: EDINET (Financial Services Agency). Free API key: https://disclosure2.edinet-fsa.go.jp
-> ログイン (create an account) -> API キー発行.

EDINET data is published under the Public Data License 1.0, which allows commercial use with a
credit line; the app shows it next to Japanese filings.

The API is date-based: one request returns every filing submitted that day, so one call covers
every Japanese company on every watchlist.

Run `python -m sources.jp_edinet` to check the key and see a sample.
"""
from __future__ import annotations

import csv
import io
import re
import sys
import threading
import time
import zipfile
from datetime import date, datetime, timedelta

import requests

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .kr_dart import _xml_to_text, tidy_english_name

API = "https://api.edinet-fsa.go.jp/api/v2"
CODE_LIST = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"
VIEWER = "https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{},,"
PAUSE = 0.4   # EDINET asks for polite request rates
TDNET = "https://www.release.tdnet.info/inbs/"
TDNET_DAYS = 14   # TDnet keeps about a month; history is read for the last two weeks

# docTypeCode -> English name (EDINET form codes)
DOC_TYPES = {
    "030": "Securities registration statement",
    "040": "Amended securities registration statement",
    "080": "Shelf registration statement",
    "090": "Amended shelf registration statement",
    "100": "Shelf registration supplement",
    "120": "Annual securities report",
    "130": "Amended annual securities report",
    "135": "Confirmation letter",
    "136": "Amended confirmation letter",
    "140": "Quarterly report",
    "150": "Amended quarterly report",
    "160": "Semi-annual report",
    "170": "Amended semi-annual report",
    "180": "Extraordinary report",
    "190": "Amended extraordinary report",
    "200": "Parent company status report",
    "220": "Share buyback status report",
    "230": "Amended share buyback status report",
    "235": "Internal control report",
    "236": "Amended internal control report",
    "240": "Tender offer registration statement",
    "250": "Amended tender offer registration statement",
    "260": "Tender offer withdrawal",
    "270": "Tender offer report",
    "280": "Amended tender offer report",
    "290": "Statement of opinion on tender offer",
    "300": "Amended statement of opinion on tender offer",
    "310": "Response to tender offer questions",
    "350": "Large shareholding report (5% rule)",
    "360": "Amended large shareholding report",
}
PERIODIC = {"120", "130", "140", "150", "160", "170"}


class EdinetError(RuntimeError):
    pass


class EdinetSource(FilingSource):
    market = "JP"
    country = "Japan"
    regulator = "EDINET"
    source_lang = "ja"
    timezone = "Asia/Tokyo"
    ticker_hint = "4-character TSE code, e.g. 7203"
    news_local = {"hl": "ja", "gl": "JP", "ceid": "JP:ja"}
    attribution = ("出典：金融庁 EDINET、東京証券取引所 TDnet / Source: EDINET (Financial Services Agency of Japan) and TDnet "
                   "(Tokyo Stock Exchange), translated and summarized")
    backfill_days = 30            # one request per day of history
    incremental_days = 1          # today and yesterday

    def __init__(self):
        self._codes: dict | None = None      # edinetCode -> {"name", "name_en", "ticker", "listed"}
        self._codes_at = 0.0
        self._day_cache: dict[date, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()

    # ---- plumbing -------------------------------------------------------
    def _key(self) -> str | None:
        return get_secret("EDINET_API_KEY")

    def tdnet_on(self) -> bool:
        return str(get_secret("ENABLE_TDNET", "false")).lower() in {"1", "true", "yes"}

    def is_configured(self) -> bool:
        return bool(self._key()) or self.tdnet_on()

    # ---- TDnet (timely disclosures: results flashes, guidance, buybacks...) ----
    def _tdnet_day(self, day: date) -> list[dict]:
        if not hasattr(self, "_td_cache"):
            from .webhttp import PoliteClient
            self._td_cache, self._td_http = {}, PoliteClient("tdnet", pause=0.5)
        fresh = day >= self.today() - timedelta(days=1)
        cached = self._td_cache.get(day)
        if cached and (not fresh or time.time() - cached[0] < 300):
            return cached[1]
        rows, page, last = [], 1, 1
        while page <= min(last, 40):
            try:
                r = self._td_http.get(f"{TDNET}I_list_{page:03d}_{day:%Y%m%d}.html")
            except Exception:
                break
            html = r.content.decode("utf-8", "ignore")
            pages = [int(n) for n in re.findall(r"I_list_(\d{3})_\d{8}\.html", html)]
            last = max(pages + [last])
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
                cell = lambda cls: (re.search(rf'class="[^"]*{cls}[^"]*"[^>]*>(.*?)</td>', tr, re.S) or [None, ""])[1]
                code = re.sub(r"<[^>]+>|\s", "", cell("kjCode"))
                link = re.search(r'href="([^"]+\.pdf)"', cell("kjTitle"))
                title = " ".join(re.sub(r"<[^>]+>", " ", cell("kjTitle")).split())
                if code and link and title:
                    rows.append({"code": code, "name": " ".join(re.sub(r"<[^>]+>", " ", cell("kjName")).split()),
                                 "title": title, "pdf": link.group(1), "time": re.sub(r"<[^>]+>|\s", "", cell("kjTime"))})
            page += 1
        self._td_cache[day] = (time.time(), rows)
        return rows

    def _tdnet_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        out, day = [], max(start, self.today() - timedelta(days=TDNET_DAYS))
        while day <= end:
            if day.weekday() < 5:
                for r in self._tdnet_day(day):
                    if r["code"][:4] != company.ticker[:4]:
                        continue
                    out.append(Filing(uid=f"JP:TD{r['pdf'].removesuffix('.pdf')}"[:64], market=self.market,
                                      ticker=company.ticker, company_name=company.name_en, filed_date=day,
                                      title_local=r["title"], filer=r["name"], url=TDNET + r["pdf"], title_en=""))
            day += timedelta(days=1)
        return out

    def _get(self, path: str, **params) -> requests.Response:
        params["Subscription-Key"] = self._key()
        time.sleep(PAUSE)
        r = requests.get(f"{API}/{path}", params=params, timeout=60)
        if r.status_code in (401, 403):
            raise EdinetError("EDINET refused the API key. Check EDINET_API_KEY.")
        r.raise_for_status()
        return r

    # ---- companies ------------------------------------------------------
    def _code_list(self) -> dict:
        """EDINET code list (all filers, with English names and securities codes). Refreshed daily."""
        with self._lock:
            if self._codes is not None and time.time() - self._codes_at < 86400:
                return self._codes
            r = requests.get(CODE_LIST, timeout=120)
            r.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            rows = list(csv.reader(io.StringIO(zf.read(name).decode("cp932", errors="replace"))))
            hi = next(i for i, row in enumerate(rows[:5]) if any("証券コード" in c for c in row))
            header = [c.strip() for c in rows[hi]]

            def col(*names):
                for n in names:
                    if n in header:
                        return header.index(n)
                return next(i for i, c in enumerate(header) if any(n in c for n in names))

            i_code, i_listed = col("ＥＤＩＮＥＴコード", "EDINETコード"), col("上場区分")
            i_name, i_en, i_sec = col("提出者名"), col("提出者名（英字）"), col("証券コード")
            codes = {}
            for row in rows[hi + 1:]:
                if len(row) <= max(i_code, i_listed, i_name, i_en, i_sec):
                    continue
                sec = row[i_sec].strip()
                ticker = sec[:-1] if len(sec) == 5 and sec.endswith("0") else sec
                codes[row[i_code].strip()] = {
                    "name": row[i_name].strip(), "name_en": tidy_english_name(row[i_en]),
                    "ticker": ticker, "listed": row[i_listed].strip() == "上場"}
            self._codes, self._codes_at = codes, time.time()
            return codes

    def normalize_ticker(self, raw: str) -> str | None:
        t = (raw or "").strip().upper()
        t = re.sub(r"\.(T|JP|TYO)$", "", t)
        if len(t) == 5 and t.endswith("0"):
            t = t[:4]
        return t if re.fullmatch(r"[0-9][0-9A-Z]{3}", t) else None

    def all_listed(self) -> list[Company]:
        return [Company(self.market, c["ticker"], code, c["name"], c["name_en"] or c["name"])
                for code, c in self._code_list().items() if c["listed"] and c["ticker"]]

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
        return [("Google Finance", f"https://www.google.com/finance/quote/{company.ticker}:TYO")]

    # ---- filings --------------------------------------------------------
    def _documents(self, day: date) -> list[dict]:
        """Every filing submitted on one day, cached for 5 minutes (one request per day per run)."""
        cached = self._day_cache.get(day)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        data = self._get("documents.json", date=day.isoformat(), type=2).json()
        status = str((data.get("metadata") or {}).get("status", "200"))
        if status != "200":
            raise EdinetError(f"EDINET error {status}: {(data.get('metadata') or {}).get('message', '')}")
        items = data.get("results") or []
        self._day_cache[day] = (time.time(), items)
        return items

    def _english_filer(self, item: dict) -> str:
        known = self._code_list().get(item.get("edinetCode") or "")
        if known and known["name_en"]:
            return known["name_en"]
        from services.translate import translate_title
        return translate_title(item.get("filerName") or "", "ja")

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        out = self._tdnet_filings(company, start, end) if self.tdnet_on() else []
        day = start if self._key() else end + timedelta(days=1)
        while day <= end:
            for item in self._documents(day):
                related = {item.get("edinetCode"), item.get("issuerEdinetCode"), item.get("subjectEdinetCode")}
                if company.source_id not in related or str(item.get("withdrawalStatus", "0")) != "0":
                    continue
                doc_id, description = item.get("docID"), (item.get("docDescription") or "").strip()
                if not doc_id or not description:
                    continue
                code = str(item.get("docTypeCode") or "")
                title_en = DOC_TYPES.get(code, "")
                if title_en:
                    if code in PERIODIC and item.get("periodEnd"):
                        title_en += f", period ending {item['periodEnd']}"
                    if item.get("edinetCode") != company.source_id:
                        title_en += f", filed by {self._english_filer(item)}"
                submitted = (item.get("submitDateTime") or "")[:10]
                try:
                    filed = datetime.strptime(submitted, "%Y-%m-%d").date()
                except ValueError:
                    filed = day
                out.append(Filing(
                    uid=f"JP:{doc_id}", market=self.market, ticker=company.ticker, company_name=company.name_en,
                    filed_date=filed, title_local=re.sub(r"\s+", " ", description),
                    filer=item.get("filerName") or "", url=VIEWER.format(doc_id), title_en=title_en))
            day += timedelta(days=1)
        return out

    def market_feed(self) -> list[dict]:
        out = []
        if self.tdnet_on():
            for r in self._tdnet_day(self.today()):
                out.append({"uid": f"JP:TD{r['pdf'].removesuffix('.pdf')}"[:64], "ticker": r["code"][:4],
                            "company": r["name"], "title_local": r["title"], "title_en": "",
                            "url": TDNET + r["pdf"], "date": self.today()})
        for day in ((self.today() - timedelta(days=1), self.today()) if self._key() else ()):
            for item in self._documents(day):
                sec = str(item.get("secCode") or "")
                if not sec or str(item.get("withdrawalStatus", "0")) != "0" or not item.get("docID"):
                    continue
                out.append({"uid": f"JP:{item['docID']}", "ticker": sec[:-1] if len(sec) == 5 else sec,
                            "company": item.get("filerName") or "",
                            "title_local": (item.get("docDescription") or "").strip(),
                            "title_en": DOC_TYPES.get(str(item.get("docTypeCode") or ""), ""),
                            "url": VIEWER.format(item["docID"]), "date": day})
        return out

    def fetch_document_text(self, uid: str) -> str:
        """Text of the filing's main documents (the HTML inside the XBRL package), or a TDnet PDF."""
        doc_id = uid.split(":", 1)[1]
        if doc_id.startswith("TD"):
            from .doctext import pdf_text
            if not hasattr(self, "_td_http"):
                self._tdnet_day(self.today())
            return pdf_text(self._td_http.get(f"{TDNET}{doc_id[2:]}.pdf").content)
        r = self._get(f"documents/{doc_id}", type=1)
        if "zip" not in r.headers.get("content-type", "") and not r.content[:2] == b"PK":
            return ""
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        pages = sorted(n for n in zf.namelist()
                       if "PublicDoc" in n and n.lower().endswith((".htm", ".html")))
        parts = []
        for name in pages:
            raw = zf.read(name)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("cp932", errors="ignore")
            parts.append(_xml_to_text(text))
        return "\n\n".join(p for p in parts if p)


def _probe() -> None:
    src = EdinetSource()
    if not src.is_configured():
        sys.exit("EDINET_API_KEY is not set.")
    listed = src.all_listed()
    print(f"Listed companies: {len(listed)}. Sample: " + ", ".join(f"{c.ticker}={c.name_en}" for c in listed[:6]))
    day = src.today()
    for _ in range(7):
        docs = src._documents(day)
        if docs:
            print(f"{day}: {len(docs)} filings. First three:")
            for d in docs[:3]:
                print(" ", d.get("docID"), d.get("secCode"), d.get("docTypeCode"), d.get("filerName"), d.get("docDescription"))
            break
        day -= timedelta(days=1)


if __name__ == "__main__":
    _probe()
