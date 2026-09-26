"""Poland: NewConnect (GPW's growth market) company reports, ESPI and EBI, read from newconnect.pl.

Unofficial: the reports list and pages on the exchange's website (the official distributor, PAP, sells the
feed). GPW's legal notice on the site is a disclaimer only, and the reports are disclosures companies must
publish by law. Switch on with ENABLE_NEWCONNECT = "true"; "false" turns Poland off.

Run `python -m sources.pl_newconnect` to check access and see a sample.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from core.config import get_secret

from .base import Company, Filing, FilingSource

BASE = "https://newconnect.pl"
REPORTS_PAGE = f"{BASE}/spolki-komunikaty-spolek"
COMPANIES_PAGE = f"{BASE}/spolki"
AJAX = f"{BASE}/ajaxindex.php"
REPORT = f"{BASE}/komunikat?geru_id={{}}"
COMPANY_PAGE = f"{BASE}/spolka?isin={{}}"
TAB_FALLBACK = (f"{AJAX}?start=reportsTab&type={{kind}}&gls_id=2003&target_show_{{name}}_page=spolka"
                f"&action=GPWListaSp&gls_isin={{isin}}&format=html&lang=PL")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/140.0.0.0 Safari/537.36",
           "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8"}
PAUSE = 0.5
PAGE_SIZE = 100
MAX_PAGES = 80
ISIN_RE = re.compile(r"\(([A-Z]{2}[A-Z0-9]{9}\d)\)")
PL_SUFFIX = re.compile(r"\s*(SPÓŁKA AKCYJNA|SPOLKA AKCYJNA|S\.\s?A\.?|SA|SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ|"
                       r"SP\.\s?Z O\.\s?O\.?|PLC|LIMITED|LTD\.?|INC\.?)\s*$", re.I)
TYPE_EN = {"Bieżący": "", "Kwartalny": "Quarterly report", "Półroczny": "Half-year report",
           "Roczny": "Annual report", "Okresowy": "Periodic report"}


def tidy_polish_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name or "").strip()
    for _ in range(2):
        name = PL_SUFFIX.sub("", name).strip(" ,")
    if name.isupper():
        cap = lambda w: "-".join(p.capitalize() for p in w.split("-"))
        name = " ".join(w if len(w) <= 3 or "." in w else cap(w) for w in name.split())
    return name


class NewConnectSource(FilingSource):
    market = "PL"
    country = "Poland"
    regulator = "NewConnect"
    source_lang = "pl"
    timezone = "Europe/Warsaw"
    ticker_hint = "NewConnect symbol, e.g. 4MB, or company name"
    news_local = {"hl": "pl", "gl": "PL", "ceid": "PL:pl"}
    attribution = "Source: NewConnect, Warsaw Stock Exchange (GPW)"
    backfill_days = 180           # loaded from the company's own ESPI and EBI tabs
    incremental_days = 1          # later runs use the market-wide scan

    def __init__(self):
        self._session: requests.Session | None = None
        self._listing: list[Company] | None = None
        self._listing_at = 0.0
        self._scan: tuple[float, date, list[dict]] | None = None
        self._lock = threading.Lock()
        self._last_call = 0.0

    # ---- plumbing -------------------------------------------------------
    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_NEWCONNECT", "false")).lower() in {"1", "true", "yes"}

    def _http(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
        return self._session

    def _call(self, method: str, url: str, **kwargs) -> requests.Response:
        wait = PAUSE - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()
        if method == "POST" or "ajaxindex.php" in url:
            kwargs.setdefault("headers", {})["X-Requested-With"] = "XMLHttpRequest"
        r = self._http().request(method, url, timeout=60, **kwargs)
        r.raise_for_status()
        return r

    def _form_fields(self, page_url: str, form_id: str) -> list[tuple[str, str]]:
        """The fields a browser would send for a search form, ticked boxes included."""
        soup = BeautifulSoup(self._call("GET", page_url).text, "html.parser")
        form = soup.find("form", id=form_id)
        if form is None:
            raise RuntimeError(f"NewConnect page changed: form {form_id} not found")
        fields = []
        for tag in form.find_all(["input", "select"]):
            name = tag.get("name")
            if not name:
                continue
            kind = (tag.get("type") or "").lower()
            if kind in ("checkbox", "radio"):
                fields.append((name, tag.get("value") or "on"))
            elif tag.name == "select":
                option = tag.find("option", selected=True) or tag.find("option")
                fields.append((name, (option.get("value") if option else "") or ""))
            else:
                fields.append((name, tag.get("value") or ""))
        return fields

    @staticmethod
    def _with(fields: list[tuple[str, str]], **overrides) -> list[tuple[str, str]]:
        out = [(k, v) for k, v in fields if k not in overrides]
        return out + [(k, str(v)) for k, v in overrides.items()]

    # ---- companies ------------------------------------------------------
    def _companies_from_list(self) -> list[Company]:
        fields = self._form_fields(COMPANIES_PAGE, "search-form")
        seen: dict[str, Company] = {}
        for size in (PAGE_SIZE, 10):
            offset, failed = 0, False
            while offset < 2000:
                try:
                    html = self._call("POST", AJAX, data=self._with(fields, offset=offset, limit=size)).text
                except requests.RequestException:
                    failed = True
                    break
                found = self._parse_companies(html)
                new = [c for c in found if c.source_id not in seen]
                for c in new:
                    seen[c.source_id] = c
                if not new:
                    break
                offset += size
            if seen and not failed:
                break
        return list(seen.values())

    @staticmethod
    def _parse_companies(html: str) -> list[Company]:
        out = []
        for a in BeautifulSoup(html, "html.parser").find_all("a", href=re.compile(r"spolka\?isin=")):
            isin = re.search(r"isin=([A-Z0-9]{12})", a["href"])
            text = " ".join(a.get_text(" ", strip=True).split())
            m = re.match(r"^(.*)\(([A-Z0-9]{2,6})\)\s*$", text)
            if isin and m:
                full = m.group(1).strip()
                out.append(Company("PL", m.group(2), isin.group(1), full, tidy_polish_name(full) or m.group(2)))
        return out

    def all_listed(self) -> list[Company]:
        with self._lock:
            if self._listing is not None and time.time() - self._listing_at < 86400:
                return self._listing
            companies = []
            try:
                companies = self._companies_from_list()
            except Exception:
                companies = []
            if len(companies) < 50:
                # Fallback: companies seen in the last two weeks of reports, keyed by ISIN.
                known = {c.source_id for c in companies}
                for r in self._reports_since(self.today() - timedelta(days=14)):
                    if r["isin"] not in known:
                        known.add(r["isin"])
                        companies.append(Company("PL", r["isin"], r["isin"], r["company"],
                                                 tidy_polish_name(r["company"])))
            self._listing, self._listing_at = companies, time.time()
            return companies

    def normalize_ticker(self, raw: str) -> str | None:
        t = (raw or "").strip().upper().removesuffix(".WA")
        return t if re.fullmatch(r"[A-Z0-9]{2,4}|[A-Z]{2}[A-Z0-9]{9}\d", t) else None

    def resolve(self, ticker: str) -> Company | None:
        t = self.normalize_ticker(ticker)
        return next((c for c in self.all_listed() if t in (c.ticker, c.source_id)), None) if t else None

    def search(self, query: str, limit: int = 8) -> list[Company]:
        q = query.strip().lower()
        if len(q) < 2:
            return []
        hits = [c for c in self.all_listed()
                if q in (c.ticker.lower(), c.source_id.lower()) or q in c.name_en.lower() or q in c.name_local.lower()]
        hits.sort(key=lambda c: (q not in (c.ticker.lower(), c.name_en.lower()), len(c.name_en)))
        return hits[:limit]

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("NewConnect page", f"{BASE}/spolka?isin={company.source_id}")]

    # ---- reports --------------------------------------------------------
    @staticmethod
    def _parse_reports(html: str) -> list[dict]:
        out = []
        for li in BeautifulSoup(html, "html.parser").find_all("li"):
            link = li.find("a", href=re.compile(r"geru_id=\d+"))
            if not link:
                continue
            gid = re.search(r"geru_id=(\d+)", link["href"]).group(1)
            text = " ".join(li.get_text(" ", strip=True).split()).replace("więcej >", "").strip()
            when = re.search(r"(\d{2})-(\d{2})-(\d{4}) (\d{2}):(\d{2})", text)
            company_text = " ".join(link.get_text(" ", strip=True).split())
            isin = ISIN_RE.search(company_text)
            if not (when and isin):
                continue
            parts = [p.strip() for p in text.split("|")]
            kind = parts[1] if len(parts) > 1 else ""
            system = parts[2] if len(parts) > 2 else ""
            number = (re.search(r"(\d+/\d{4})", parts[3]) or [None, ""])[1] if len(parts) > 3 else ""
            title = text.split(company_text, 1)[1].strip() if company_text in text else ""
            out.append({"gid": gid, "date": date(int(when[3]), int(when[2]), int(when[1])), "kind": kind,
                        "system": system, "number": number, "isin": isin.group(1),
                        "company": company_text[:isin.start()].strip(), "title": title})
        return out

    def _reports_since(self, start: date) -> list[dict]:
        """Newest reports back to `start`, all companies, shared across one run (cached 5 minutes)."""
        if self._scan and time.time() - self._scan[0] < 300 and self._scan[1] <= start:
            return self._scan[2]
        fields = self._form_fields(REPORTS_PAGE, "espi-union-reports")
        rows, seen = [], set()
        for page in range(MAX_PAGES):
            html = self._call("POST", AJAX, data=self._with(fields, offset=page * PAGE_SIZE, limit=PAGE_SIZE)).text
            batch = [r for r in self._parse_reports(html) if r["gid"] not in seen]
            if not batch:
                break
            for r in batch:
                seen.add(r["gid"])
            rows += batch
            if min(r["date"] for r in batch) < start:
                break
        self._scan = (time.time(), start, rows)
        return rows

    def _tab_urls(self, isin: str) -> list[tuple[str, str]]:
        """The company page's ESPI and EBI tab addresses (read from the page, with a known fallback)."""
        urls = []
        try:
            soup = BeautifulSoup(self._call("GET", COMPANY_PAGE.format(isin)).text, "html.parser")
            for a in soup.find_all("a", attrs={"data-href": re.compile(r"start=reportsTab")}):
                href = a["data-href"]
                system = "ESPI" if "type=e" in href else "EBI"
                if (system, href) not in urls:
                    urls.append((system, href))
        except Exception:
            pass
        return urls or [("ESPI", TAB_FALLBACK.format(kind="e", name="espi", isin=isin)),
                        ("EBI", TAB_FALLBACK.format(kind="b", name="ebi", isin=isin))]

    @staticmethod
    def _parse_tab(html: str, system: str, company: str, isin: str) -> list[dict]:
        out, seen = [], set()
        for a in BeautifulSoup(html, "html.parser").find_all("a", href=re.compile(r"geru_id=\d+")):
            gid = re.search(r"geru_id=(\d+)", a["href"]).group(1)
            if gid in seen:
                continue
            row = a.find_parent(["tr", "li"]) or a.parent
            text = " ".join(row.get_text(" ", strip=True).split())
            iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
            dmy = re.search(r"(\d{2})-(\d{2})-(\d{4})", text)
            if iso:
                day = date(int(iso[1]), int(iso[2]), int(iso[3]))
            elif dmy:
                day = date(int(dmy[3]), int(dmy[2]), int(dmy[1]))
            else:
                continue
            seen.add(gid)
            kind = next((k for k in TYPE_EN if k in text), "Bieżący")
            number = (re.search(r"(\d+/\d{4})", text) or [None, ""])[1]
            title = " ".join(a.get_text(" ", strip=True).split())
            if not title or re.fullmatch(r"[\d\-: ]+", title):
                title = re.sub(r"^.*?\d+/\d{4}\s*", "", text)
            out.append({"gid": gid, "date": day, "kind": kind, "system": system, "number": number,
                        "isin": isin, "company": company, "title": title})
        return out

    def _company_history(self, company: Company) -> list[dict]:
        rows = []
        for system, url in self._tab_urls(company.source_id):
            try:
                rows += self._parse_tab(self._call("GET", url).text, system, company.name_local, company.source_id)
            except Exception:
                continue   # history is a bonus; the market scan still finds new reports
        return rows

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        out, seen = [], set()
        # First load: read this company's own report tabs, however rarely it files.
        history = self._company_history(company) if (end - start).days > 7 else []
        for r in history + self._reports_since(max(start, end - timedelta(days=7))):
            if r["gid"] in seen or r["isin"] != company.source_id or not start <= r["date"] <= end:
                continue
            seen.add(r["gid"])
            label = f"{r['system']} {r['number']}".strip()
            title = r["title"] or r["kind"]
            out.append(Filing(uid=f"PL:{r['gid']}", market=self.market, ticker=company.ticker,
                              company_name=company.name_en, filed_date=r["date"],
                              title_local=f"{title} ({label})" if label else title,
                              filer=r["company"], url=REPORT.format(r["gid"]),
                              title_en=TYPE_EN.get(r["kind"], "") and f"{TYPE_EN[r['kind']]} ({label})"))
        return out

    def fetch_document_text(self, uid: str) -> str:
        gid = uid.split(":", 1)[1]
        soup = BeautifulSoup(self._call("GET", REPORT.format(gid)).text, "html.parser")
        body = soup.find(class_="report-data") or soup.find(class_="dane")
        if body is None:
            return ""
        for tr in body.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            tr.replace_with("\n" + " | ".join(c for c in cells if c) + "\n")
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in body.get_text("\n").splitlines()]
        text = "\n".join(ln for ln in lines if ln and ln != "|")
        # Many reports carry the company's own English version; prefer it when present. The heading also
        # appears in the table of contents, so use its last occurrence (the section itself).
        marks = [m.end() for m in re.finditer(r"MESSAGE \(ENGLISH VERSION\)", text)]
        if marks:
            rest = text[marks[-1]:]
            stop = re.search(r"INFORMACJE O PODMIOCIE|PODPISY OSÓB|\n\d+\.\s*[A-ZŁŚŻŹĆŃÓĘĄ ]{8,}\n", rest)
            english = (rest[:stop.start()] if stop else rest).strip()
            if len(english) > 80:
                return english
        return text


def _probe() -> None:
    src = NewConnectSource()
    listed = src.all_listed()
    with_symbol = [c for c in listed if c.ticker != c.source_id]
    print(f"Companies: {len(listed)} ({len(with_symbol)} with a symbol). Sample: "
          + ", ".join(f"{c.ticker}={c.name_en}" for c in listed[:6]))
    rows = src._reports_since(src.today() - timedelta(days=3))
    print(f"Reports in the last 3 days: {len(rows)}")
    for r in rows[:5]:
        print(" ", r["date"], r["system"], r["number"], r["company"], "|", r["title"][:80])
    if rows:
        text = src.fetch_document_text(f"PL:{rows[0]['gid']}")
        print("Report text sample:", " ".join(text.split())[:400])


if __name__ == "__main__":
    _probe()
