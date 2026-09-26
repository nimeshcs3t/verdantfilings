"""Settings come from environment variables first (GitHub Actions worker), then Streamlit secrets."""
import os


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
