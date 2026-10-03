"""Widgets shared by several pages."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import streamlit as st

from core import nav
from core.db import as_utc, utcnow
from core.ui import esc, filing_row_html, html_block, news_html, summary_html
from core.config import direct_fetch
from services import ask, github, news, personal, prices, watch
from services.classify import categorize
from services.pipeline import enrich_filing, get_company, is_queued, listings_ready, search_companies
from sources import configured_sources, get_source, visible_sources


@st.cache_data(ttl=3600, show_spinner=False)
def price_history(market: str, ticker: str) -> list:
    try:
        return prices.history(market, ticker)
    except Exception:
        return []


def histories(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], list]:
    """Price histories for several companies at once (in parallel, cached for an hour)."""
    pairs = list(dict.fromkeys(pairs))[:40]
    if not pairs:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(pairs, pool.map(lambda p: price_history(*p), pairs)))


def render_filing(f: dict, key: str, show_company: bool = True, link_company: bool = True,
                  country: str = "", hist: list | None = None, stars: set | None = None) -> None:
    created = as_utc(f.get("created_at"))
    is_new = bool(created and utcnow() - created < timedelta(hours=2))
    category = categorize(f.get("title_en"), f.get("title_local"), f.get("price_sensitive"))
    move = prices.move_html(prices.move_on(hist, f["filed_date"])) if hist else ""
    is_starred = bool(stars and f["uid"] in stars)
    html_block(filing_row_html(f, show_company, is_new, country, category, move, is_starred))
    with st.expander("Overview and translation"):
        if f.get("summary_en"):
            _show_enriched(f)
            _ask_box(f, key)
        elif not direct_fetch() and is_queued(f["uid"]):
            st.caption("The overview is being prepared. It usually takes a few minutes; reload the page then.")
        elif st.button("Translate and summarize", key=f"enrich-{key}-{f['uid']}"):
            with st.spinner("Reading the filing"):
                enriched = enrich_filing(f["uid"])
            if enriched and enriched.get("enrich_pending"):
                github.trigger_sync()
                st.success("Requested. The overview will be ready in a few minutes; reload the page then.")
            elif enriched and enriched.get("summary_en"):
                _show_enriched(enriched)
            else:
                st.warning((enriched or {}).get("enrich_error", "This filing couldn't be processed."))
        else:
            st.caption("Translates the filing to English and writes a short overview.")
        user = st.session_state.get("user")
        cols = st.columns([1, 1, 3])
        if user and cols[0].button("Unstar" if is_starred else "Star", key=f"star-{key}-{f['uid']}", type="tertiary",
                                   icon=":material/star:" if is_starred else ":material/star_border:"):
            personal.toggle_star(user["id"], f["uid"])
            st.rerun()
        if show_company and link_company:
            if cols[1].button("Company page", key=f"co-{key}-{f['uid']}", type="tertiary"):
                nav.open_company(f["market"], f["ticker"])


def _ask_box(f: dict, key: str) -> None:
    if not ask.available():
        return
    with st.form(f"ask-{key}-{f['uid']}", border=False, clear_on_submit=False):
        cols = st.columns([5, 1], vertical_alignment="bottom")
        question = cols[0].text_input("Ask about this filing", placeholder="e.g. What is the deal size?",
                                      key=f"q-{key}-{f['uid']}")
        asked = cols[1].form_submit_button("Ask", width="stretch")
    store = f"answer-{key}-{f['uid']}"
    if asked and question.strip():
        with st.spinner("Reading the filing"):
            st.session_state[store] = ask.answer(question, f)
    if st.session_state.get(store):
        html_block(f'<div class="answer">{esc(st.session_state[store])}</div>')


def _show_enriched(f: dict) -> None:
    html_block(summary_html(f["summary_en"]))
    if f.get("body_en"):
        st.caption("Machine translation of the opening section")
        html_block(f'<div class="body-en">{esc(f["body_en"])}</div>')


def add_company_form(user: dict, key: str = "add") -> None:
    sources = visible_sources(user)
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
        try:
            known = get_company(market, ticker) if ticker else None
        except Exception:
            known = None
        if known:
            _add(user, market, known["ticker"])
        else:
            with st.spinner("Searching"):
                try:
                    found = search_companies(market, query)
                except Exception:
                    found = []
                st.session_state[f"matches-{key}"] = [(c.market, c.ticker, c.name_en, c.name_local) for c in found]
            if not st.session_state[f"matches-{key}"]:
                if not direct_fetch() and not get_source(market).resolve_in_app and not listings_ready(market):
                    st.warning("The company list is still loading. Try again in a few minutes.")
                else:
                    st.warning(f"No listed company matches “{query.strip()}”.")
    matches = st.session_state.get(f"matches-{key}") or []
    if matches:
        st.caption("Choose a company")
        for m, t, name_en, name_local in matches:
            label = f"{name_en}  ({t})" + (f"  {name_local}" if name_local and name_local != name_en else "")
            if st.button(label, key=f"pick-{key}-{m}-{t}", type="tertiary"):
                st.session_state.pop(f"matches-{key}", None)
                _add(user, m, t)


def _add(user: dict, market: str, ticker: str) -> None:
    with st.spinner("Adding company"):
        try:
            comp, err = watch.add(user, market, ticker)
        except Exception:
            comp, err = None, "The company couldn't be added right now. Try again in a few minutes."
    if err:
        st.warning(err)
    else:
        if not direct_fetch() and not comp.get("last_synced"):
            github.trigger_sync()
            st.toast(f"Added {comp['name_en']}. Its filings will appear within a few minutes.")
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
