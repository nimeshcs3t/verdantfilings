"""Spain: the CNMV's official registers of listed companies' announcements, read from its public website:
"Otra información relevante" (other relevant information) and "Información privilegiada" (inside information).
Companies are identified by NIF (tax id). Unofficial, for personal use. Switch on with ENABLE_CNMV = "true".

  list    /portal/otra-informacion-relevante/resultado-oir?nif=<NIF>&lang=en&page=<n>   (10 rows a page)
          /portal/informacion-privilegiada/resultado-ip?nif=<NIF>&lang=en&page=<n>
  (there is no market-wide list: the site only shows all companies' filings inside a browser search session)
  NIF     from GLEIF's open LEI register (registeredAs), or the CNMV's own company-name search, checked on the list
  doc     /webservices/verdocumento/ver?t=<id>  (PDF)"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .doctext import any_text
from .webhttp import PoliteClient

log = logging.getLogger(__name__)

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
        self._at, self._lock = 0.0, threading.Lock()

    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_CNMV", "false")).lower() in {"1", "true", "yes"}

    def _get(self, register: str, params: dict) -> str:
        """GET a register page. The site answers bad parameters with a redirect to /portal/Error?errorcode=HE."""
        import requests
        for path in (BASE + REGISTERS[register], BASE + REGISTERS[register] + ".aspx"):
            try:
                r = self.http.get(path, params=params, allow_redirects=True)
            except requests.HTTPError as exc:
                last = exc
                continue
            if "/Error" not in r.url:
                return r.text
            last = RuntimeError(f"CNMV refused the query ({r.url})")
        raise last

    def _page(self, register: str, **params) -> list[dict]:
        """One page of a company's register (nif=..., page=...)."""
        p = {"lang": "en", **params}
        if p.get("page") == 0:
            p.pop("page")
        return parse_register(self._get(register, p), register)

    def normalize_ticker(self, raw: str) -> str | None:
        t = re.sub(r"\.MC$", "", (raw or "").strip().upper())
        return t if re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,9}", t) else None

    def _warm(self) -> None:
        """The CNMV site keeps search state in a session; open the home page once so requests carry its cookies."""
        if not getattr(self, "_warmed", False):
            try:
                self.http.get(f"{BASE}/portal/home.aspx", params={"lang": "en"})
            except Exception as exc:
                if exc.__class__.__name__ == "SourceBusy":
                    raise
            self._warmed = True

    def _nif_from_gleif(self, name: str) -> list[str]:
        """Spanish tax ids (NIF) of companies with this name, from GLEIF's open LEI register (registeredAs)."""
        import requests
        want = plain_name(name)
        out = []
        for params in ({"filter[entity.legalName]": name}, {"filter[fulltext]": want}):
            try:
                data = requests.get("https://api.gleif.org/api/v1/lei-records",
                                    params={**params, "filter[entity.legalAddress.country]": "ES", "page[size]": 8},
                                    timeout=20).json().get("data", [])
            except Exception:
                continue
            for rec in data:
                entity = (rec.get("attributes") or {}).get("entity") or {}
                legal = (entity.get("legalName") or {}).get("name") or ""
                reg = re.sub(r"[^A-Z0-9]", "", str(entity.get("registeredAs") or "").upper())
                if not re.fullmatch(r"[A-Z]\d{8}|[A-Z]\d{7}[A-Z0-9]", reg):
                    continue
                if plain_name(legal).split()[:1] == want.split()[:1]:
                    nif = f"{reg[0]}-{reg[1:]}"
                    if nif not in out:
                        out.append(nif)
            if out:
                break
        return out

    def _nif_from_search(self, name: str) -> list[str]:
        """The CNMV's own company-name search (an ASP.NET form): fill the first text box and submit."""
        from bs4 import BeautifulSoup
        try:
            page = self.http.get(f"{BASE}/portal/Consultas/BusquedaPorEntidad", params={"lang": "en"})
            soup = BeautifulSoup(page.text, "html.parser")
            form = soup.find("form")
            if not form:
                return []
            data = {i["name"]: i.get("value", "") for i in form.find_all("input", attrs={"type": "hidden"}) if i.get("name")}
            box = form.find("input", attrs={"type": "text"})
            button = form.find("input", attrs={"type": "submit"})
            if not box or not box.get("name"):
                return []
            data[box["name"]] = name
            if button and button.get("name"):
                data[button["name"]] = button.get("value", "")
            action = form.get("action") or page.url
            if action.startswith("/"):
                action = BASE + action
            elif not action.startswith("http"):
                action = page.url
            r = self.http.post(action, data=data, allow_redirects=True)
        except Exception as exc:
            if exc.__class__.__name__ == "SourceBusy":
                raise
            return []
        found = re.findall(r"nif=([A-Z]-?\d{7,8}[A-Z0-9]?)", r.url + " " + r.text, re.I)
        return list(dict.fromkeys(n.upper() if "-" in n else f"{n[0].upper()}-{n[1:]}" for n in found))[:5]

    def _check(self, nif: str) -> str | None:
        """The company's name on the CNMV, if this NIF has a filings page there."""
        try:
            rows = self._page("oir", nif=nif)
        except Exception as exc:
            if exc.__class__.__name__ == "SourceBusy":
                raise
            return None
        return rows[0]["company"] if rows else None

    def resolve(self, ticker: str) -> Company | None:
        from services.prices import symbol_details
        t = self.normalize_ticker(ticker)
        details = symbol_details(f"{t}.MC") if t else None
        if not details:
            return None
        self._warm()
        name = details["name"]
        for nif in self._nif_from_gleif(name) + self._nif_from_search(plain_name(name).title()):
            on_cnmv = self._check(nif)
            if on_cnmv:
                return Company(self.market, t, nif, on_cnmv, name)
        log.warning("CNMV company not found for ES:%s (%s)", t, name)
        return None

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
        self._warm()
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
        """None: the CNMV only lists all companies' filings inside a browser search session."""
        return []

    def fetch_document_text(self, uid: str) -> str:
        from sqlalchemy import select
        from core.db import filings, get_engine
        with get_engine().connect() as conn:
            url = conn.execute(select(filings.c.url).where(filings.c.uid == uid)).scalar()
        if not url:
            return ""
        r = self.http.get(url)
        return any_text(r.content, r.headers.get("content-type") or "")
