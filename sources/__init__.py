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
from .fr_amf import AmfSource
from .hk_hkex import HkexSource
from .il_maya import MayaWebSource
from .il_tase import TaseSource
from .no_oslo import OsloSource
from .nordic import nordic_sources
from .tw_twse import TwseSource
from .uk_nsm import NsmSource
from .jp_edinet import EdinetSource
from .kr_dart import DartSource
from .pl_newconnect import NewConnectSource
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


def visible_sources(user: dict | None) -> list[FilingSource]:
    """Markets a member can use. Personal-use sources (admin_only) are shown to admins only."""
    is_admin = bool(user and user.get("role") == "admin")
    return [s for s in configured_sources() if is_admin or not getattr(s, "admin_only", False)]


register(DartSource())
register(EdgarSource())
register(AsxSource())
register(NewConnectSource())
register(HkexSource())
register(TwseSource())
register(OsloSource())
register(AmfSource())
register(NsmSource())
for _nordic in nordic_sources():
    register(_nordic)
# Israel: the paid TASE feed when a key is set, otherwise the public MAYA website (personal use).
try:
    from core.config import get_secret as _secret
    register(TaseSource() if _secret("TASE_API_KEY") else MayaWebSource())
except Exception:
    register(MayaWebSource())
register(EdinetSource())
