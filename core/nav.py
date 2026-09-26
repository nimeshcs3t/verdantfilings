"""Page registry so views can jump to each other."""
import streamlit as st

PAGES: dict = {}


def go(name: str) -> None:
    st.switch_page(PAGES[name])


def open_company(market: str, ticker: str) -> None:
    st.session_state["company_sel"] = (market, ticker)
    go("company")
