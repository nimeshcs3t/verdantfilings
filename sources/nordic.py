"""Sweden, Denmark and Finland: company announcements from Nasdaq Nordic's news service (the one its website uses).
Companies are found by ticker (Yahoo Finance). Unofficial, for personal use. Switch on with ENABLE_NORDIC = "true"."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .doctext import html_text
from .webhttp import PoliteClient

NEWS = "https://api.news.eu.nasdaq.com/news/query.action"
BASE_PARAMS = {"type": "json", "showAttachments": "true", "showCnsSpecific": "true", "showCompany": "true",
               "countResults": "false", "market": "", "cnscategory": "", "globalGroup": "exchangeNotice",
               "globalName": "NordicAllMarkets", "displayLanguage": "en", "language": "", "timeZone": "CET",
               "dateMask": "yyyy-MM-dd HH:mm:ss", "start": 0, "dir": "DESC"}


def _core(name: str) -> str:
    n = re.sub(r"\(.*?\)", "", name or "")
    return " ".join(n.replace("publ.", "").replace("publ", "").split())


class NordicSource(FilingSource):
    regulator = "Nasdaq Nordic"
    attribution = "Source: Nasdaq Nordic company news (personal use)"
    backfill_days = 180
    incremental_days = 3
    resolve_in_app = True
    _http = None

    def __init__(self, market: str, country: str, city: str, suffix: str, exchange: str, lang: str, tz: str,
                 news: dict):
        self.market, self.country, self.city, self.suffix, self.exchange = market, country, city, suffix, exchange
        self.source_lang, self.timezone, self.news_local = lang, tz, news
        self.ticker_hint = f"{city} ticker, e.g. " + {"SE": "VOLV-B", "DK": "NOVO-B", "FI": "NOKIA"}[market]

    @property
    def http(self) -> PoliteClient:
        if NordicSource._http is None:     # one client (and one pause) shared by the three countries
            NordicSource._http = PoliteClient("nasdaq_nordic", pause=0.5, headers={"Origin": "https://www.nasdaq.com",
                                                                                  "Referer": "https://www.nasdaq.com/"})
        return NordicSource._http

    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_NORDIC", "false")).lower() in {"1", "true", "yes"}

    def all_listed(self) -> list[Company]:
        return []

    def normalize_ticker(self, raw: str) -> str | None:
        t = re.sub(re.escape(self.suffix) + "$", "", (raw or "").strip().upper()).replace(" ", "-")
        return t if re.fullmatch(r"[A-Z0-9][A-Z0-9\-]{0,11}", t) else None

    def resolve(self, ticker: str) -> Company | None:
        from services.prices import symbol_details
        t = self.normalize_ticker(ticker)
        details = symbol_details(f"{t}{self.suffix}") if t else None
        if not details:
            return None
        name = _core(details["name"])
        return Company(self.market, t, name, details["name"], name)

    def search(self, query: str, limit: int = 8) -> list[Company]:
        from services.prices import yahoo_search
        out = []
        for hit in yahoo_search(query, (self.exchange,), limit):
            t = hit["symbol"].removesuffix(self.suffix)
            name = _core(hit.get("longname") or hit.get("shortname") or t)
            out.append(Company(self.market, t, name, name, name))
        return out

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("Nasdaq Nordic news", "https://www.nasdaq.com/european-market-activity/news/company-news")]

    def _query(self, **params) -> list[dict]:
        r = self.http.get(NEWS, params={**BASE_PARAMS, "limit": 100, "freeText": "", "company": "", "fromDate": "",
                                        "toDate": "", **params})
        return ((r.json().get("results") or {}).get("item")) or []

    def _items(self, company: Company, start: date, end: date) -> list[dict]:
        dates = {"fromDate": f"{start}", "toDate": f"{end}"}
        items = self._query(company=company.source_id, **dates)
        if not items:      # names differ slightly between sources: search by name and keep exact company matches
            core = company.source_id.split(" ")[0].lower()
            items = [i for i in self._query(freeText=company.source_id, **dates)
                     if (i.get("company") or "").lower().startswith(core)]
        return items

    def _filing(self, i: dict, company: Company) -> Filing | None:
        try:
            when = datetime.strptime(str(i.get("published") or i.get("releaseTime"))[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
        title = " ".join((i.get("headline") or "").split())
        english = title if i.get("language") == "en" else ""
        return Filing(uid=f"{self.market}:{i.get('disclosureId')}", market=self.market, ticker=company.ticker,
                      company_name=company.name_en, filed_date=when, title_local=title,
                      filer=i.get("cnsCategory") or "", url=i.get("messageUrl") or "", title_en=english)

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        items = self._items(company, start, end)
        has_english = {i.get("headline") for i in items if i.get("language") == "en"}
        out = []
        for i in items:
            # the same release often comes in English and the local language; keep English when there is one
            if i.get("language") != "en" and "en" in (i.get("languages") or []) and has_english:
                continue
            f = self._filing(i, company)
            if f and start <= f.filed_date <= end:
                out.append(f)
        return out

    def market_feed(self) -> list[dict]:
        out = []
        for i in self._query(fromDate=f"{self.today() - timedelta(days=1)}", toDate=f"{self.today()}"):
            if self.city.lower() not in (i.get("market") or "").lower() and self.country.lower() not in (i.get("market") or "").lower():
                continue
            name = i.get("company") or ""
            f = self._filing(i, Company(self.market, "", name, name, name))
            if f:
                out.append({"uid": f.uid, "ticker": "", "company": name, "title_local": f.title_local,
                            "title_en": f.title_en, "url": f.url, "date": f.filed_date})
        return out

    def fetch_document_text(self, uid: str) -> str:
        from core.db import filings, get_engine
        from sqlalchemy import select
        with get_engine().connect() as conn:
            url = conn.execute(select(filings.c.url).where(filings.c.uid == uid)).scalar()
        return html_text(self.http.get(url).text) if url else ""


def nordic_sources() -> list[NordicSource]:
    return [NordicSource("SE", "Sweden", "Stockholm", ".ST", "STO", "sv", "Europe/Stockholm", {"hl": "sv", "gl": "SE", "ceid": "SE:sv"}),
            NordicSource("DK", "Denmark", "Copenhagen", ".CO", "CPH", "da", "Europe/Copenhagen", {"hl": "da", "gl": "DK", "ceid": "DK:da"}),
            NordicSource("FI", "Finland", "Helsinki", ".HE", "HEL", "fi", "Europe/Helsinki", {"hl": "fi", "gl": "FI", "ceid": "FI:fi"})]
