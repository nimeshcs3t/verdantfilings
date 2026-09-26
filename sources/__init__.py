"""Registry of regulators. To add a country: write sources/<cc>_<name>.py subclassing FilingSource and register it.

Candidates for later:
  US  SEC EDGAR    https://www.sec.gov/edgar/sec-api-documentation (free, no key)
  JP  EDINET       https://disclosure2.edinet-fsa.go.jp (free key)
  HK  HKEXnews     https://www.hkexnews.hk
  TW  MOPS         https://mops.twse.com.tw
  IN  BSE/NSE      corporate announcements feeds
"""
from .base import Company, Filing, FilingSource
from .kr_dart import DartSource

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
