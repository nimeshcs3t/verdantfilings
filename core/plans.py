"""Plan limits. Hook for future paid tiers."""
from .config import get_secret


def watchlist_limit(plan: str) -> int:
    if plan == "pro":
        return int(get_secret("PRO_WATCHLIST_LIMIT", 300))
    return int(get_secret("FREE_WATCHLIST_LIMIT", 15))
