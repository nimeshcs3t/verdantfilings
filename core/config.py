"""Settings come from environment variables first (GitHub Actions worker), then Streamlit secrets."""
import os
import time


def get_secret(key: str, default=None):
    value = os.environ.get(key)
    if value not in (None, ""):
        return value
    try:
        import streamlit as st

        if key in st.secrets:
            value = st.secrets[key]
            if value not in (None, ""):
                return value
    except Exception:
        pass
    return default


def app_name() -> str:
    return get_secret("APP_NAME", "Verdant Filings")


def direct_fetch() -> bool:
    """True only in the background worker. The website never calls regulators directly, because some
    (like DART) block the cloud servers Streamlit runs on."""
    return str(get_secret("DIRECT_FETCH", "false")).lower() in {"1", "true", "yes"}


DEFAULT_TZ = "America/New_York"


def app_tz() -> str:
    """The app's day boundary: 'today' starts at midnight in this time zone (APP_TIMEZONE, default US Eastern)."""
    return str(get_secret("APP_TIMEZONE", DEFAULT_TZ) or DEFAULT_TZ)


def _apply_timezone() -> None:
    """Make date.today() and local times follow the app's time zone in the website and both background jobs.
    Stored timestamps stay in UTC (they always carry their time zone)."""
    try:
        os.environ["TZ"] = app_tz()
        time.tzset()
    except Exception:
        pass


_apply_timezone()
