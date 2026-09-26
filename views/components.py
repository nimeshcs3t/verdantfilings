"""Widgets shared by several pages."""
from __future__ import annotations

from datetime import timedelta

import streamlit as st

from core import nav
from core.db import as_utc, utcnow
from core.ui import esc, filing_row_html, html_block, news_html, summary_html
from services import news, watch
from services.pipeline import enrich_filing
from sources import configured_sources, get_source


def render_filing(f: dict, key: str, show_company: bool = True, link_company: bool = True) -> None:
    created = as_utc(f.get("created_at"))
    is_new = bool(created and utcnow() - created < timedelta(hours=2))
    html_block(filing_row_html(f, show_company, is_new))
    with st.expander("Overview and translation"):
        if f.get("summary_en"):
            _show_enriched(f)
        elif st.button("Translate and summarize", key=f"enrich-{key}-{f['uid']}"):
            with st.spinner("Reading the filing"):
                enriched = enrich_filing(f["uid"])
            if enriched and enriched.get("summary_en"):
                _show_enriched(enriched)
            else:
                st.warning((enriched or {}).get("enrich_error", "This filing couldn't be processed."))
        else:
            st.caption("Downloads the filing, translates it to English and writes a short overview.")
        if show_company and link_company:
            if st.button("Company page", key=f"co-{key}-{f['uid']}", type="tertiary"):
                nav.open_company(f["market"], f["ticker"])


def _show_enriched(f: dict) -> None:
    html_block(summary_html(f["summary_en"]))
    if f.get("body_en"):
        st.caption("Machine translation of the opening section")
        html_block(f'<div class="body-en">{esc(f["body_en"])}</div>')


def add_company_form(user: dict, key: str = "add") -> None:
    sources = configured_sources()
    if not sources:
        st.error("No regulator is connected yet. Add DART_API_KEY to the app secrets.")
        return
    with st.form(f"form-{key}", border=False):
        cols = st.columns([1, 3, 1], vertical_alignment="bottom") if len(sources) > 1 else st.columns([4, 1], vertical_alignment="bottom")
        if len(sources) > 1:
            market = cols[0].selectbox("Market", [s.market for s in sources],
                                       format_func=lambda m: get_source(m).country)
            query_col, btn_col = cols[1], cols[2]
        else:
            market, query_col, btn_col = sources[0].market, cols[0], cols[1]
        query = query_col.text_input("Add a company", placeholder=f"Ticker or name, e.g. 005930 or Samsung",
                                     key=f"q-{key}")
        submitted = btn_col.form_submit_button("Add", type="primary", width="stretch")
    if submitted and query.strip():
        src = get_source(market)
        ticker = src.normalize_ticker(query)
        if ticker:
            _add(user, market, ticker)
        else:
            with st.spinner("Searching"):
                st.session_state[f"matches-{key}"] = [(c.market, c.ticker, c.name_en, c.name_local)
                                                      for c in src.search(query)]
            if not st.session_state[f"matches-{key}"]:
                st.warning(f"No listed company matches “{query.strip()}”.")
    matches = st.session_state.get(f"matches-{key}") or []
    if matches:
        st.caption("Choose a company")
        for m, t, name_en, name_local in matches:
            if st.button(f"{name_en}  ({t})  {name_local}", key=f"pick-{key}-{m}-{t}", type="tertiary"):
                st.session_state.pop(f"matches-{key}", None)
                _add(user, m, t)


def _add(user: dict, market: str, ticker: str) -> None:
    with st.spinner("Adding company"):
        comp, err = watch.add(user, market, ticker)
    if err:
        st.warning(err)
    else:
        st.toast(f"Added {comp['name_en']}")
        st.rerun()


@st.cache_data(ttl=1800, show_spinner=False)
def cached_english_news(name_en: str, ticker: str, limit: int = 10) -> list[dict]:
    return news.english_news(name_en, ticker, limit)


@st.cache_data(ttl=1800, show_spinner=False)
def cached_local_news(market: str, name_local: str, limit: int = 10) -> list[dict]:
    src = get_source(market)
    if not src or not src.news_local:
        return []
    return news.local_news(name_local, src.news_local, src.source_lang, limit)


def render_news(items: list[dict]) -> None:
    if items:
        html_block(news_html(items))
    else:
        html_block('<div class="empty">No recent articles found.</div>')
