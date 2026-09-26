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
