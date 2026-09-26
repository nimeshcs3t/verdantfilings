"""Registry of regulators. To add a country: write sources/<cc>_<name>.py subclassing FilingSource and register it.

Candidates for later:
  US  SEC EDGAR    https://www.sec.gov/edgar/sec-api-documentation (free, no key)
  IL  TASE MAYA      added (paid feed with free trial)
  JP  EDINET       added (free key)
  HK  HKEXnews     https://www.hkexnews.hk
  TW  MOPS         https://mops.twse.com.tw
  IN  BSE/NSE      corporate announcements feeds
"""
from .au_asx import AsxSource
from .base import Company, Filing, FilingSource, SourceBusy
from .il_tase import TaseSource
from .jp_edinet import EdinetSource
from .kr_dart import DartSource
from .us_edgar import EdgarSource

_REGISTRY: dict[str, FilingSource] = {}


def register(source: FilingSource) -> None:
    _REGISTRY[source.market] = source


def get_source(market: str) -> FilingSource | None:
    return _REGISTRY.get(market)


def all_sources() -> list[FilingSource]:
    return list(_REGISTRY.values())


def configured_sources() -> list[FilingSource]:
    return [s for s in _REGISTRY.values() if s.is_configured()]


register(DartSource())
register(EdgarSource())
register(AsxSource())
register(TaseSource())
register(EdinetSource())
