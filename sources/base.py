"""Every country's regulator is a FilingSource. Add a new country by subclassing this and registering it."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo


@dataclass
class Company:
    market: str
    ticker: str
    source_id: str          # regulator's own id (DART corp_code, SEC CIK, ...)
    name_local: str
    name_en: str


@dataclass
class Filing:
    uid: str                # "<market>:<regulator id>", globally unique
    market: str
    ticker: str
    company_name: str
    filed_date: date
    title_local: str
    filer: str
    url: str


class FilingSource(ABC):
    market = ""             # short code used everywhere, e.g. "KR"
    country = ""
    regulator = ""
    source_lang = "en"      # language of filings, for translation
    timezone = "UTC"
    ticker_hint = ""
    news_local: dict | None = None   # Google News params for local-language press

    def today(self) -> date:
        return datetime.now(ZoneInfo(self.timezone)).date()

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def normalize_ticker(self, raw: str) -> str | None: ...

    @abstractmethod
    def resolve(self, ticker: str) -> Company | None: ...

    @abstractmethod
    def search(self, query: str, limit: int = 8) -> list[Company]: ...

    @abstractmethod
    def list_filings(self, company: Company, start: date, end: date) -> list[Filing]: ...

    @abstractmethod
    def fetch_document_text(self, uid: str) -> str: ...

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return []
