"""Germany, Austria and Switzerland: ad hoc and company announcements, found through Google News.

There is no free official feed for these markets that a program can read: the German company register blocks
automated access, and the EQS / SIX websites build their lists in the browser. Regulated announcements are,
however, distributed by newswires (EQS, PTA, IRW, ...) and republished with their wire prefix
("EQS-Adhoc: ...", "PTA-News: ..."). This source searches Google News for those wire headlines about the company.
Titles and links only (the full text is on the linked page). Unofficial. Switch on with ENABLE_DACH = "true"."""
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .doctext import any_text

WIRES = re.compile(r"^\s*(EQS-[A-Za-z]+|DGAP-[A-Za-z]+|PTA-[A-Za-z]+|IRW-[A-Za-z]+|Ad[- ]hoc[- ]?[A-Za-z]*|"
                   r"Corporate News|Original-Research|euro adhoc|Press release|Medienmitteilung)\s*:\s*", re.I)
KIND = {"adhoc": "Ad hoc (inside information)", "dd": "Managers' dealings", "pvr": "Voting rights notice",
        "news": "Company news", "cms": "Capital market notice", "ifr": "Interim report notice", "afr": "Annual report notice",
        "bsn": "Buyback notice", "hv": "Shareholder meeting", "stimmrechte": "Voting rights notice"}
LEGAL = re.compile(r"\b(AG|SE|KGaA|GmbH|Co\.?|N\.V\.|S\.A\.|Holding|Group|Inc\.?|Ltd\.?|plc|& Co\.?)\b", re.I)


def core_name(name: str) -> str:
    n = re.sub(r"\(.*?\)", "", name or "")
    n = LEGAL.sub(" ", n.replace(",", " "))
    return " ".join(n.split())


def wire_kind(prefix: str) -> str:
    p = prefix.lower().replace("dgap-", "eqs-").split("-", 1)[-1].replace(" ", "")
    return KIND.get(p, "Ad hoc (inside information)" if "adhoc" in p.replace("-", "") else prefix.strip().rstrip(":"))


class WireSource(FilingSource):
    regulator = "Newswires via Google News"
    attribution = "Headlines from newswires, found with Google News (unofficial)"
    backfill_days = 120
    incremental_days = 5
    resolve_in_app = True

    def __init__(self, market: str, country: str, suffixes: tuple[str, ...], exchanges: tuple[str, ...], lang: str,
                 tz: str, news: dict, example: str, extra_terms: str):
        self.market, self.country, self.suffixes, self.exchanges = market, country, suffixes, exchanges
        self.source_lang, self.timezone, self.news_local, self.extra_terms = lang, tz, news, extra_terms
        self.ticker_hint = f"{country} ticker, e.g. {example}"

    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_DACH", "false")).lower() in {"1", "true", "yes"}

    def normalize_ticker(self, raw: str) -> str | None:
        t = (raw or "").strip().upper()
        for s in self.suffixes:
            t = re.sub(re.escape(s) + "$", "", t)
        return t if re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,11}", t) else None

    def resolve(self, ticker: str) -> Company | None:
        from services.prices import symbol_details
        t = self.normalize_ticker(ticker)
        if not t:
            return None
        for s in self.suffixes:
            details = symbol_details(f"{t}{s}")
            if details:
                return Company(self.market, t, core_name(details["name"]) or details["name"], details["name"], details["name"])
        return None

    def search(self, query: str, limit: int = 8) -> list[Company]:
        from services.prices import yahoo_search
        out, seen = [], set()
        for hit in yahoo_search(query, self.exchanges, limit * 2):
            t = hit["symbol"]
            for s in self.suffixes:
                t = t.removesuffix(s)
            if t in seen:
                continue
            seen.add(t)
            name = hit.get("longname") or hit.get("shortname") or t
            out.append(Company(self.market, t, core_name(name) or name, name, name))
        return out[:limit]

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        from urllib.parse import quote_plus
        return [("Search announcements", f"https://www.eqs-news.com/search-results/?searchtype=news&searchword={quote_plus(company.source_id)}")]

    def _headlines(self, core: str, start: date, end: date) -> list[dict]:
        from services.news import google_news
        q = (f'"{core}" ({self.extra_terms}) after:{start - timedelta(days=1)} before:{end + timedelta(days=1)}')
        items = google_news(q, limit=100)
        if self.news_local:
            p = self.news_local
            items += google_news(q, hl=p["hl"], gl=p["gl"], ceid=p["ceid"], limit=100)
        return items

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        core = company.source_id or core_name(company.name_en)
        first = core.split()[0].lower() if core else ""
        out, seen = [], set()
        for item in self._headlines(core, start, end):
            title = item.get("title") or ""
            m = WIRES.match(title)
            loose = not m and re.search(r"\bad[- ]hoc\b", title, re.I)       # e.g. "Ad hoc announcement pursuant to Art. 53 LR"
            if not (m or loose) or first not in title.lower():
                continue                    # only wire-distributed or ad hoc announcements that name the company
            when = item["published"].date() if item.get("published") else None
            if not when or not start <= when <= end:
                continue
            body = title[m.end():].strip() if m else title.strip()
            key = re.sub(r"\W+", " ", body.lower()).strip()
            if key in seen:
                continue
            seen.add(key)
            uid = f"{self.market}:g{hashlib.sha1(f'{company.ticker}|{key}'.encode()).hexdigest()[:16]}"
            kind = wire_kind(m.group(1)) if m else "Ad hoc (inside information)"
            out.append(Filing(uid=uid, market=self.market, ticker=company.ticker, company_name=company.name_en,
                              filed_date=when, title_local=body, filer=kind, url=item.get("link") or "",
                              title_en=body if body.isascii() else "",
                              price_sensitive=kind.startswith("Ad hoc")))
        return out

    def fetch_document_text(self, uid: str) -> str:
        from sqlalchemy import select
        import requests
        from core.db import filings, get_engine
        with get_engine().connect() as conn:
            url = conn.execute(select(filings.c.url).where(filings.c.uid == uid)).scalar()
        if not url:
            return ""
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
            text = any_text(r.content, r.headers.get("content-type") or "")
        except Exception:
            return ""
        return text if len(text) > 400 and "news.google" not in r.url else ""


WIRE_TERMS = '"EQS-Adhoc" OR "EQS-News" OR "EQS-DD" OR "EQS-PVR" OR "EQS-CMS" OR "DGAP-Adhoc" OR "PTA-Adhoc" OR "PTA-News" OR "IRW-News"'


def wire_sources() -> list[WireSource]:
    return [
        WireSource("DE", "Germany", (".DE", ".F"), ("GER", "FRA"), "de", "Europe/Berlin",
                   {"hl": "de", "gl": "DE", "ceid": "DE:de"}, "SAP", WIRE_TERMS),
        WireSource("AT", "Austria", (".VI",), ("VIE",), "de", "Europe/Vienna",
                   {"hl": "de", "gl": "AT", "ceid": "AT:de"}, "VOE", WIRE_TERMS + ' OR "euro adhoc"'),
        WireSource("CH", "Switzerland", (".SW",), ("EBS",), "de", "Europe/Zurich",
                   {"hl": "de", "gl": "CH", "ceid": "CH:de"}, "NESN",
                   WIRE_TERMS + ' OR "Ad hoc announcement" OR "Medienmitteilung" OR "Ad-hoc-Mitteilung"'),
    ]
