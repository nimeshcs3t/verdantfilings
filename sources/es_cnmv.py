"""Spain: the CNMV's official registers of listed companies' announcements, read from its public website:
"Otra información relevante" (other relevant information) and "Información privilegiada" (inside information).
Companies are identified by NIF (tax id). Unofficial, for personal use. Switch on with ENABLE_CNMV = "true".

  list    /portal/otra-informacion-relevante/resultado-oir?nif=<NIF>&lang=en&page=<n>   (10 rows a page)
          /portal/informacion-privilegiada/resultado-ip?nif=<NIF>&lang=en&page=<n>
  recent  same pages with dias=<days> instead of nif (all companies)
  doc     /webservices/verdocumento/ver?t=<id>  (PDF)"""
from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .doctext import any_text
from .webhttp import PoliteClient

BASE = "https://www.cnmv.es"
REGISTERS = {"oir": "/portal/otra-informacion-relevante/resultado-oir", "ip": "/portal/informacion-privilegiada/resultado-ip"}
LEGAL = re.compile(r"\b(S\.?A\.?U?|S\.?L\.?|SOCIMI|SICAV|S\.?E\.?|N\.?V\.?|PLC|INC|GROUP|GRUPO|HOLDINGS?)\b\.?", re.I)


def plain_name(name: str) -> str:
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().upper()
    n = LEGAL.sub(" ", n.replace(",", " "))
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", n).split())


def parse_register(html: str, register: str) -> list[dict]:
    """Rows of a CNMV register page: {date, company, nif, category, title, number, url}."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select('a[href*="verdocumento"]'):
        # climb to the element that holds one announcement (it has the date and the company link)
        box = a
        for _ in range(6):
            if box.parent is None:
                break
            box = box.parent
            text = " ".join(box.get_text(" ").split())
            if re.search(r"\d{2}/\d{2}/\d{4}", text) and box.select_one('a[href*="nif="]'):
                break
        text = " ".join(box.get_text(" ").split())
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
        if not m:
            continue
        try:
            when = date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            continue
        co = box.select_one('a[href*="nif="]')
        nif = re.search(r"nif=([A-Z0-9\-]+)", co["href"], re.I).group(1).upper() if co else ""
        number = re.search(r"(?:Register number|N[ºo°]\.? de registro|registro)[:\s]*(\d+)", text, re.I)
        href = a["href"] if a["href"].startswith("http") else BASE + a["href"]
        title = " ".join(a.get_text(" ").split())
        key = number.group(1) if number else href
        if key in seen or not title:
            continue
        seen.add(key)
        company = " ".join(co.get_text(" ").split()) if co else ""
        rest = text.replace(title, " ").replace(company, " ")
        rest = re.sub(r"\d{2}/\d{2}/\d{4}|\d{1,2}:\d{2}|(?:Register number|N[ºo°]\.? de registro)[:\s]*\d+", " ", rest, flags=re.I)
        category = " ".join(rest.split())[:120]
        out.append({"date": when, "company": company, "nif": nif, "category": category, "title": title,
                    "number": number.group(1) if number else None, "url": href, "register": register})
    return out


class CnmvSource(FilingSource):
    market = "ES"
    country = "Spain"
    regulator = "CNMV"
    source_lang = "es"
    timezone = "Europe/Madrid"
    ticker_hint = "Madrid ticker, e.g. ITX"
    news_local = {"hl": "es", "gl": "ES", "ceid": "ES:es"}
    attribution = "Source: CNMV official registers (personal use)"
    backfill_days = 180
    incremental_days = 5
    resolve_in_app = True

    def __init__(self):
        self.http = PoliteClient("cnmv", pause=1.0, headers={"Referer": f"{BASE}/portal/home.aspx?lang=en"})
        self._issuers: dict[str, tuple[str, str]] | None = None      # plain name -> (nif, display name)
        self._at, self._lock = 0.0, threading.Lock()

    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_CNMV", "false")).lower() in {"1", "true", "yes"}

    def _page(self, register: str, **params) -> list[dict]:
        r = self.http.get(BASE + REGISTERS[register], params={"lang": "en", **params}, allow_redirects=True)
        return parse_register(r.text, register)

    def issuers(self) -> dict[str, tuple[str, str]]:
        """Companies that filed in the last 60 days, by plain name (refreshed daily)."""
        with self._lock:
            if self._issuers is None or time.time() - self._at > 86400:
                found: dict[str, tuple[str, str]] = {}
                for register in REGISTERS:
                    for page in range(15):
                        rows = self._page(register, dias=60, page=page)
                        for r in rows:
                            if r["nif"] and r["company"]:
                                found.setdefault(plain_name(r["company"]), (r["nif"], r["company"]))
                        if len(rows) < 10:
                            break
                self._issuers, self._at = found, time.time()
            return self._issuers

    def normalize_ticker(self, raw: str) -> str | None:
        t = re.sub(r"\.MC$", "", (raw or "").strip().upper())
        return t if re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,9}", t) else None

    def _match(self, name: str) -> tuple[str, str] | None:
        want = plain_name(name)
        if not want:
            return None
        issuers = self.issuers()
        if want in issuers:
            return issuers[want]
        first = want.split()[0]
        close = [(k, v) for k, v in issuers.items() if k.startswith(want) or want.startswith(k) or
                 (len(first) >= 4 and k.split()[:1] == [first])]
        return min(close, key=lambda kv: abs(len(kv[0]) - len(want)))[1] if close else None

    def resolve(self, ticker: str) -> Company | None:
        from services.prices import symbol_details
        t = self.normalize_ticker(ticker)
        details = symbol_details(f"{t}.MC") if t else None
        if not details:
            return None
        hit = self._match(details["name"])
        if not hit:
            return None
        return Company(self.market, t, hit[0], hit[1], details["name"])

    def search(self, query: str, limit: int = 8) -> list[Company]:
        from services.prices import yahoo_search
        out = []
        for hit in yahoo_search(query, ("MCE",), limit):
            t = hit["symbol"].removesuffix(".MC")
            name = hit.get("longname") or hit.get("shortname") or t
            out.append(Company(self.market, t, "", name, name))
        return out

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("CNMV filings", f"{BASE}/portal/otra-informacion-relevante/resultado-oir?nif={company.source_id}&lang=en")]

    def _filing(self, r: dict, ticker: str, name: str) -> Filing:
        prefix = "IP" if r["register"] == "ip" else ""
        uid = f"ES:{prefix}{r['number']}" if r.get("number") else f"ES:{prefix}h{hashlib.sha1(r['url'].encode()).hexdigest()[:14]}"
        title = r["title"]
        if r["register"] == "ip" and not title.lower().startswith("inside"):
            title = f"Inside information: {title}"
        return Filing(uid=uid, market=self.market, ticker=ticker, company_name=name, filed_date=r["date"],
                      title_local=title, filer=r.get("category") or "", url=r["url"],
                      title_en=title if title.isascii() and not re.search(r"\b(de|la|del|sobre)\b", title) else "")

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        if not company.source_id:
            found = self.resolve(company.ticker)
            if not found:
                return []
            company = found
        out = []
        for register in REGISTERS:
            for page in range(6):
                rows = self._page(register, nif=company.source_id, page=page)
                out += [self._filing(r, company.ticker, company.name_en) for r in rows if start <= r["date"] <= end]
                if len(rows) < 10 or (rows and min(r["date"] for r in rows) < start):
                    break
        return out

    def market_feed(self) -> list[dict]:
        out = []
        for register in REGISTERS:
            for page in range(5):
                rows = self._page(register, dias=1, page=page)
                for r in rows:
                    f = self._filing(r, "", r["company"])
                    out.append({"uid": f.uid, "ticker": "", "company": r["company"], "title_local": f.title_local,
                                "title_en": f.title_en, "url": f.url, "date": f.filed_date})
                if len(rows) < 10:
                    break
        return out

    def fetch_document_text(self, uid: str) -> str:
        from sqlalchemy import select
        from core.db import filings, get_engine
        with get_engine().connect() as conn:
            url = conn.execute(select(filings.c.url).where(filings.c.uid == uid)).scalar()
        if not url:
            return ""
        r = self.http.get(url)
        return any_text(r.content, r.headers.get("content-type") or "")
