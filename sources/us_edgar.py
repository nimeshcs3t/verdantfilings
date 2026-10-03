"""USA: SEC EDGAR. Free, no key. The SEC requires every request to carry a User-Agent with a
contact email (set SEC_CONTACT_EMAIL) and allows at most 10 requests per second.
Covers NYSE, Nasdaq, and OTC companies that report to the SEC, plus foreign issuers listed in the
US (6-K, 20-F, 40-F filers such as large Canadian and Israeli companies).

Run `python -m sources.us_edgar` to check access and see a sample.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from core.config import app_name, get_secret

from .base import Company, Filing, FilingSource
from .kr_dart import tidy_english_name

TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{}/{}"
CURRENT_FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=&company=&dateb=&owner=include"
                "&start={}&count=100&output=atom")
PAUSE = 0.15            # stays under the SEC's 10 requests/second
MAX_DOC_BYTES = 4_000_000

FORMS = {
    "10-K": "Annual report", "10-K/A": "Amended annual report",
    "10-Q": "Quarterly report", "10-Q/A": "Amended quarterly report",
    "8-K": "Current report", "8-K/A": "Amended current report",
    "6-K": "Foreign issuer report", "20-F": "Annual report (foreign issuer)", "40-F": "Annual report (Canadian issuer)",
    "4": "Insider transaction (Form 4)", "4/A": "Amended insider transaction (Form 4)",
    "3": "Initial insider ownership (Form 3)", "5": "Annual insider ownership (Form 5)",
    "144": "Notice of proposed insider sale (Form 144)",
    "SC 13D": "Activist 5% stake (13D)", "SC 13D/A": "Amended activist 5% stake (13D)",
    "SCHEDULE 13D": "Activist 5% stake (13D)", "SCHEDULE 13D/A": "Amended activist 5% stake (13D)",
    "SC 13G": "Passive 5% stake (13G)", "SC 13G/A": "Amended passive 5% stake (13G)",
    "SCHEDULE 13G": "Passive 5% stake (13G)", "SCHEDULE 13G/A": "Amended passive 5% stake (13G)",
    "DEF 14A": "Proxy statement", "DEFA14A": "Additional proxy materials", "PRE 14A": "Preliminary proxy statement",
    "S-1": "IPO registration statement", "S-1/A": "Amended IPO registration statement",
    "S-3": "Shelf registration statement", "S-4": "Merger registration statement", "S-8": "Employee stock plan registration",
    "424B4": "Final prospectus", "424B5": "Prospectus supplement",
    "SC TO-T": "Tender offer by third party", "SC TO-I": "Issuer tender offer (buyback)", "SC 14D9": "Response to tender offer",
    "11-K": "Employee plan annual report", "NT 10-K": "Late annual report notice", "NT 10-Q": "Late quarterly report notice",
    "25-NSE": "Delisting notice", "15-12B": "Deregistration", "CORRESP": "Letter to the SEC", "UPLOAD": "Letter from the SEC",
}
ITEMS_8K = {
    "1.01": "Material agreement", "1.02": "Material agreement ended", "1.03": "Bankruptcy",
    "1.05": "Cybersecurity incident", "2.01": "Acquisition or disposal completed", "2.02": "Results (earnings)",
    "2.03": "New debt or obligation", "2.04": "Debt acceleration", "2.05": "Restructuring costs",
    "2.06": "Impairment", "3.01": "Listing standards notice", "3.02": "Unregistered share sale",
    "3.03": "Change to shareholder rights", "4.01": "Auditor change", "4.02": "Financials no longer reliable",
    "5.01": "Change in control", "5.02": "Director or officer change", "5.03": "Bylaw or charter change",
    "5.05": "Code of ethics change", "5.07": "Shareholder vote results", "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
}
# Filed in large numbers by banks and investment firms, and not about the company's own business.
DEFAULT_EXCLUDE = "424B2,FWP,13F-HR,13F-HR/A,13F-NT,N-PX,ABS-15G"


TX_CODES = {"P": "bought {n} shares", "S": "sold {n} shares", "A": "was granted {n} shares",
            "M": "exercised options for {n} shares", "F": "had {n} shares withheld for taxes",
            "G": "gifted {n} shares", "D": "returned {n} shares to the company", "C": "converted {n} shares",
            "X": "exercised rights for {n} shares", "J": "reported another change of {n} shares"}
ENTITY_WORDS = {"INC", "LLC", "LP", "L.P.", "CORP", "TRUST", "FUND", "LTD", "HOLDINGS", "CAPITAL", "PARTNERS",
                "GROUP", "MANAGEMENT", "ADVISORS", "BANK", "CO", "COMPANY", "FOUNDATION", "PLC", "SA", "AG"}


def _person(name: str) -> str:
    """SEC lists people surname first ("Cook Timothy D"); entities are left as they are."""
    words = name.split()
    if len(words) >= 2 and not ({w.strip(".,").upper() for w in words} & ENTITY_WORDS):
        words = words[1:] + words[:1]
    return " ".join(w.capitalize() if len(w) > 1 else w for w in words)


def _num(value: str) -> str:
    try:
        f = float(value)
        return f"{f:,.0f}" if f == int(f) else f"{f:,.2f}"
    except (TypeError, ValueError):
        return value or ""


def parse_ownership(xml: str) -> list[dict]:
    """Structured rows from a Form 3/4/5 ownership document."""
    soup = BeautifulSoup(xml, "html.parser")
    val = lambda node, tag: (node.find(tag).find("value").get_text(strip=True)
                             if node and node.find(tag) and node.find(tag).find("value") else "")
    owner = soup.find("rptownername")
    name = _person(owner.get_text(strip=True)) if owner else "An insider"
    rel = soup.find("reportingownerrelationship")
    roles = []
    if rel:
        if rel.find("officertitle"):
            roles.append(rel.find("officertitle").get_text(strip=True))
        if rel.find("isdirector") and rel.find("isdirector").get_text(strip=True) in ("1", "true"):
            roles.append("Director")
        if rel.find("istenpercentowner") and rel.find("istenpercentowner").get_text(strip=True) in ("1", "true"):
            roles.append("10% owner")
    num = lambda s: float(s) if re.fullmatch(r"-?\d+(\.\d+)?", s or "") else None
    rows = []
    for tx in soup.find_all("nonderivativetransaction"):
        code_tag = tx.find("transactioncode")
        day = val(tx, "transactiondate")
        rows.append({"person": name, "role": ", ".join(roles), "code": code_tag.get_text(strip=True) if code_tag else "",
                     "shares": num(val(tx, "transactionshares")), "price": num(val(tx, "transactionpricepershare")),
                     "after": num(val(tx, "sharesownedfollowingtransaction")), "security": val(tx, "securitytitle"),
                     "date": day[:10] if day else ""})
    if not rows and soup.find("nonderivativeholding"):
        rows.append({"person": name, "role": ", ".join(roles), "code": "H", "shares": None, "price": None,
                     "after": num(val(soup.find("nonderivativeholding"), "sharesownedfollowingtransaction")),
                     "security": "", "date": ""})
    return rows


def _insider_text(xml: str) -> str:
    """Plain-English lines from a Form 3/4/5 ownership document."""
    lines = []
    for r in parse_ownership(xml):
        who = f"{r['person']} ({r['role']})" if r["role"] else r["person"]
        if r["code"] == "H":
            lines.append(f"{who} reported holding {_num(r['after'])} shares.")
            continue
        line = f"{who} " + TX_CODES.get(r["code"], "reported a change of {n} shares").format(n=_num(r["shares"]))
        if r["security"] and r["security"].lower() not in ("common stock", "common shares", "ordinary shares"):
            line += f" of {r['security']}"
        if r["price"]:
            line += f" at ${_num(r['price'])} per share"
        if r["date"]:
            line += f" on {r['date']}"
        if r["after"] is not None:
            line += f". Holds {_num(r['after'])} shares after the transaction"
        lines.append(line + ".")
    return "\n".join(lines)


class EdgarError(RuntimeError):
    pass


class EdgarSource(FilingSource):
    market = "US"
    country = "USA"
    regulator = "SEC EDGAR"
    source_lang = "en"
    timezone = "America/New_York"
    ticker_hint = "US ticker, e.g. AAPL, BRK.B or an OTC symbol"
    news_local = None
    backfill_days = 60
    incremental_days = 3          # covers weekends

    def __init__(self):
        self._tickers: list[Company] | None = None
        self._tickers_at = 0.0
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.websites: dict[str, str] = {}      # CIK -> website, seen in SEC submissions data

    # ---- plumbing -------------------------------------------------------
    def is_configured(self) -> bool:
        return bool(get_secret("SEC_CONTACT_EMAIL"))

    def _get(self, url: str, stream: bool = False) -> requests.Response:
        wait = PAUSE - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()
        r = requests.get(url, timeout=60, stream=stream, headers={
            "User-Agent": f"{app_name()} {get_secret('SEC_CONTACT_EMAIL')}",
            "Accept-Encoding": "gzip, deflate"})
        if r.status_code == 403:
            raise EdgarError("The SEC refused the request. Check SEC_CONTACT_EMAIL is a real address.")
        r.raise_for_status()
        return r

    # ---- companies ------------------------------------------------------
    def all_listed(self) -> list[Company]:
        with self._lock:
            if self._tickers is not None and time.time() - self._tickers_at < 86400:
                return self._tickers
            data = self._get(TICKERS).json()
            fields = data["fields"]
            i_cik, i_name, i_ticker = fields.index("cik"), fields.index("name"), fields.index("ticker")
            seen, out = set(), []
            for row in data["data"]:
                ticker = str(row[i_ticker] or "").upper()
                if not ticker or ticker in seen:
                    continue
                seen.add(ticker)
                name = tidy_english_name(str(row[i_name] or "")) or ticker
                out.append(Company(self.market, ticker, str(row[i_cik]), name, name))
            self._tickers, self._tickers_at = out, time.time()
            return out

    def normalize_ticker(self, raw: str) -> str | None:
        t = (raw or "").strip().upper().replace(".", "-").replace("/", "-")
        t = re.sub(r"^(NYSE|NASDAQ|OTC|AMEX):", "", t)
        return t if re.fullmatch(r"[A-Z][A-Z0-9\-]{0,9}", t) else None

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
        return [("SEC filings page", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                                     f"&CIK={company.source_id}&owner=include&count=40")]

    # ---- filings --------------------------------------------------------
    @staticmethod
    def _title(form: str, items: str) -> str:
        base = FORMS.get(form, f"Form {form}")
        if form in ("8-K", "8-K/A") and items:
            names = [ITEMS_8K[i] for i in (x.strip() for x in items.split(",")) if i in ITEMS_8K]
            if names:
                return f"{base}: {', '.join(names)}"
        return base

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        cik = int(company.source_id)
        data = self._get(SUBMISSIONS.format(cik)).json()
        site = (data.get("website") or data.get("investorWebsite") or "").strip()
        if site:
            self.websites[str(cik)] = site
        recent = data.get("filings", {}).get("recent", {})
        excluded = {f.strip().upper() for f in str(get_secret("SEC_EXCLUDE_FORMS", DEFAULT_EXCLUDE)).split(",") if f.strip()}
        out = []
        n = len(recent.get("accessionNumber", []))

        def col(key: str, i: int) -> str:
            values = recent.get(key) or []
            return values[i] if i < len(values) and values[i] is not None else ""

        for i in range(n):
            form = str(col("form", i))
            if form.upper() in excluded:
                continue
            try:
                filed = datetime.strptime(col("filingDate", i), "%Y-%m-%d").date()
            except ValueError:
                continue
            if not start <= filed <= end:
                continue
            acc = col("accessionNumber", i)
            doc = col("primaryDocument", i)
            folder = ARCHIVE.format(cik, acc.replace("-", ""))
            url = f"{folder}/{doc}" if doc else f"{folder}/{acc}-index.htm"
            title = self._title(form, str(col("items", i) or ""))
            out.append(Filing(uid=f"US:{cik}:{acc}", market=self.market, ticker=company.ticker,
                              company_name=company.name_en, filed_date=filed, title_local=title,
                              filer=company.name_en, url=url, title_en=title))
        return out

    def market_feed(self) -> list[dict]:
        import feedparser
        excluded = {f.strip().upper() for f in str(get_secret("SEC_EXCLUDE_FORMS", DEFAULT_EXCLUDE)).split(",") if f.strip()}
        by_cik = {c.source_id: c.ticker for c in self.all_listed()}
        out, seen = [], set()
        for start in (0, 100):
            feed = feedparser.parse(self._get(CURRENT_FEED.format(start)).content)
            for e in feed.entries:
                m = re.match(r"^(?P<form>.+?) - (?P<name>.+) \((?P<cik>\d{10})\) \((?P<role>[^)]+)\)$", e.get("title", ""))
                acc = re.search(r"AccNo:</b>\s*([\d-]+)", e.get("summary", ""))
                if not m or not acc or m["role"] == "Reporting" or m["form"].upper() in excluded:
                    continue
                cik = str(int(m["cik"]))
                uid = f"US:{cik}:{acc.group(1)}"
                if uid in seen:
                    continue
                seen.add(uid)
                items = ",".join(re.findall(r"Item (\d\.\d\d)", e.get("summary", "")))
                title = self._title(m["form"], items)
                filed = re.search(r"Filed:</b>\s*(\d{4}-\d{2}-\d{2})", e.get("summary", ""))
                out.append({"uid": uid, "ticker": by_cik.get(cik, ""), "company": tidy_english_name(m["name"]),
                            "title_local": title, "title_en": title, "url": e.get("link", ""),
                            "date": date.fromisoformat(filed.group(1)) if filed else self.today()})
        return out

    def fetch_submission(self, uid: str) -> str:
        """The filing's full submission text (first few MB)."""
        _, cik, acc = uid.split(":", 2)
        r = self._get(f"{ARCHIVE.format(cik, acc.replace('-', ''))}/{acc}.txt", stream=True)
        raw, size = [], 0
        for chunk in r.iter_content(65536):
            raw.append(chunk)
            size += len(chunk)
            if size >= MAX_DOC_BYTES:
                break
        r.close()
        return b"".join(raw).decode("utf-8", errors="ignore")

    def fetch_document_text(self, uid: str) -> str:
        """Main document plus press-release exhibits (EX-99), from the filing's full submission file."""
        text = self.fetch_submission(uid)
        parts = []
        for block in re.findall(r"<DOCUMENT>(.*?)(?:</DOCUMENT>|$)", text, re.S):
            doc_type = (re.search(r"<TYPE>([^\s<]+)", block) or [None, ""])[1].upper()
            is_main = not parts and not doc_type.startswith(("EX-", "GRAPHIC", "ZIP", "XML", "JSON", "EXCEL", "PDF"))
            if not (is_main or doc_type.startswith("EX-99")):
                continue
            body = (re.search(r"<TEXT>(.*?)(?:</TEXT>|$)", block, re.S) or [None, ""])[1]
            if "<ownershipDocument>" in body:
                parts.append(_insider_text(body))
                continue
            soup = BeautifulSoup(body, "html.parser")
            for tag in soup.find_all(["style", "script", "ix:header"]):
                tag.decompose()
            for tr in soup.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                tr.replace_with("\n" + " | ".join(c for c in cells if c) + "\n")
            lines = [re.sub(r"\s+", " ", ln).strip() for ln in soup.get_text("\n").splitlines()]
            parts.append("\n".join(ln for ln in lines if ln and ln != "|"))
        return "\n\n".join(p for p in parts if p)


def _probe() -> None:
    src = EdgarSource()
    if not src.is_configured():
        sys.exit("SEC_CONTACT_EMAIL is not set.")
    listed = src.all_listed()
    print(f"Tickers: {len(listed)}. Sample: " + ", ".join(f"{c.ticker}={c.name_en}" for c in listed[:6]))
    apple = src.resolve("AAPL")
    if apple:
        for f in src.list_filings(apple, src.today() - timedelta(days=30), src.today())[:5]:
            print(" ", f.filed_date, f.title_en, f.url)


if __name__ == "__main__":
    _probe()
