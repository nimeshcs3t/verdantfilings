"""UK: regulated announcements from the FCA's National Storage Mechanism (the official store of UK listed
companies' disclosures), through the search service its website uses. Companies are found by London ticker
(Yahoo Finance) and identified by LEI (GLEIF open data). Unofficial, for personal use.
Switch on with ENABLE_UK_NSM = "true"."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from functools import lru_cache

from core.config import get_secret

from .base import Company, Filing, FilingSource
from .doctext import any_text
from .webhttp import PoliteClient

NSM = "https://api.data.fca.org.uk/search?index=nsm-search"
ARTEFACTS = "https://data.fca.org.uk/artefacts/"
GLEIF = "https://api.gleif.org/api/v1/lei-records"


def _plain(name: str) -> str:
    n = re.sub(r"\b(ORD|ORDINARY|SHS|SHARES)\b.*$", "", (name or "").upper())
    return " ".join(n.replace(".", " ").split())


class NsmSource(FilingSource):
    market = "UK"
    country = "UK"
    regulator = "FCA NSM"
    source_lang = "en"
    timezone = "Europe/London"
    ticker_hint = "London ticker, e.g. SHEL or BP."
    news_local = None
    attribution = "Source: FCA National Storage Mechanism (personal use); LEIs from GLEIF"
    backfill_days = 180
    incremental_days = 3
    resolve_in_app = True

    def __init__(self):
        self.http = PoliteClient("fca_nsm", pause=0.5, headers={"Origin": "https://data.fca.org.uk",
                                                                  "Referer": "https://data.fca.org.uk/",
                                                                  "Accept": "application/json, text/plain, */*"})

    def is_configured(self) -> bool:
        return str(get_secret("ENABLE_UK_NSM", "false")).lower() in {"1", "true", "yes"}

    def all_listed(self) -> list[Company]:
        return []        # no full list; companies are looked up one at a time

    def normalize_ticker(self, raw: str) -> str | None:
        t = re.sub(r"\.L$", "", (raw or "").strip().upper()).rstrip(".")
        return t if re.fullmatch(r"[A-Z0-9]{1,5}", t) else None

    @staticmethod
    @lru_cache(maxsize=256)
    def _lei_for(name: str) -> tuple[str, str] | None:
        import requests
        plain = _plain(name)
        for params in ({"filter[entity.legalName]": plain}, {"filter[fulltext]": plain, "filter[entity.legalAddress.country]": "GB"},
                       {"filter[fulltext]": plain}):
            try:
                data = requests.get(GLEIF, params={**params, "page[size]": 5}, timeout=20).json().get("data", [])
            except Exception:
                continue
            for rec in data:
                entity = rec.get("attributes", {}).get("entity", {})
                legal = (entity.get("legalName") or {}).get("name") or ""
                if entity.get("status", "ACTIVE") == "ACTIVE" and _plain(legal).startswith(plain.split(" ")[0]):
                    return rec["id"], legal
        return None

    def resolve(self, ticker: str) -> Company | None:
        from services.prices import symbol_details
        t = self.normalize_ticker(ticker)
        details = symbol_details(f"{t}.L") if t else None
        if not details:
            return None
        found = self._lei_for(details["name"])
        if not found:
            return None
        lei, legal = found
        return Company(self.market, t, lei, legal, details["name"])

    def search(self, query: str, limit: int = 8) -> list[Company]:
        from services.prices import yahoo_search
        out = []
        for hit in yahoo_search(query, ("LSE",), limit):
            t = hit["symbol"].removesuffix(".L")
            name = hit.get("longname") or hit.get("shortname") or t
            out.append(Company(self.market, t, "", name, name))
        return out

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return [("FCA NSM", "https://data.fca.org.uk/#/nsm/nationalstoragemechanism")]

    def _hits(self, lei: str, size: int = 100) -> list[dict]:
        today = date.today() + timedelta(days=1)
        body = {"from": 0, "size": size, "sort": "submitted_date", "sortorder": "desc",
                "criteriaObj": {"criteria": [{"name": "company_lei", "value": ["", lei, "disclose_org", "related_org"]},
                                             {"name": "latest_flag", "value": "Y"}],
                                "dateCriteria": [{"name": "publication_date", "value": {"from": None, "to": f"{today}T23:59:59Z"}},
                                                 {"name": "submitted_date", "value": {"from": None, "to": f"{today}T23:59:59Z"}}]}}
        return [h.get("_source") or {} for h in ((self.http.post(NSM, json=body).json().get("hits") or {}).get("hits") or [])]

    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]:
        out = []
        for s in self._hits(company.source_id):
            if s.get("lei") and s.get("lei") != company.source_id:
                continue
            try:
                when = datetime.fromisoformat(str(s.get("publication_date") or s.get("submitted_date")).replace("Z", "+00:00")).date()
            except ValueError:
                continue
            if not start <= when <= end:
                continue
            title = " ".join((s.get("headline") or s.get("type") or "Announcement").split())
            link = s.get("download_link")
            out.append(Filing(uid=f"UK:{s.get('disclosure_id') or s.get('seq_id')}"[:64], market=self.market,
                              ticker=company.ticker, company_name=company.name_en, filed_date=when, title_local=title,
                              filer=s.get("type") or "", url=(ARTEFACTS + link) if link else (s.get("html_link") or ""),
                              title_en=title))
        return out

    def fetch_document_text(self, uid: str) -> str:
        from core.db import filings, get_engine
        from sqlalchemy import select
        with get_engine().connect() as conn:
            url = conn.execute(select(filings.c.url).where(filings.c.uid == uid)).scalar()
        if not url:
            return ""
        r = self.http.get(url)
        return any_text(r.content, r.headers.get("content-type") or "")
