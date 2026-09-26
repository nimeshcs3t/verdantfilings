"""Every country's regulator is a FilingSource. Add a new country by subclassing this and registering it."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo


class SourceBusy(Exception):
    """A regulator call was skipped to stay within a usage budget. Not an error; try again later."""


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
    title_en: str = ""      # set when the source already provides a good English title
    price_sensitive: bool = False   # exchange-marked market-moving announcement (ASX)


class FilingSource(ABC):
    market = ""             # short code used everywhere, e.g. "KR"
    country = ""
    regulator = ""
    source_lang = "en"      # language of filings, for translation
    timezone = "UTC"
    ticker_hint = ""
    news_local: dict | None = None   # Google News params for local-language press
    attribution = ""        # credit line required by the data licence, shown with the data
    backfill_days = 90      # history to load when a company is first added
    incremental_days = 7    # window re-checked on each later sync

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

    def all_listed(self) -> list[Company]:
        """Every listed company, for lookups without calling the regulator."""
        return []

    def external_links(self, company: Company) -> list[tuple[str, str]]:
        return []

    def market_feed(self) -> list[dict]:
        """Latest filings across the whole market, for keyword alerts. Items: uid, ticker or source_id,
        company, title_local, title_en, url, date. Sources without a cheap market-wide feed return []."""
        return []

    def should_alert(self, row: dict) -> bool:
        """Whether a new filing is worth a Telegram alert. Sources can narrow this."""
        return True
