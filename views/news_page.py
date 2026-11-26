"""News: your companies (Google News and Yahoo Finance) and defence industry news by region (US, Europe, Israel,
Korea, with Korean and Hebrew headlines translated)."""
from __future__ import annotations

import streamlit as st

from core.ui import page_header
from .components import cached_defence_news, cached_english_news, cached_tv10, render_news

REGIONS = ["All", "US", "Europe", "Israel", "Korea"]


def _tagged(items: list[dict]) -> list[dict]:
    out = []
    for i in items:
        i = dict(i)
        if i.get("region"):
            i["source"] = f'{i["region"]} · {i.get("source") or ""}'.strip(" ·")
        out.append(i)
    return out


def page() -> None:
    user = st.session_state["user"]
    page_header("News", "Your companies and defence industry news by region")
    kind = st.segmented_control("Show", ["Your companies", "Defence", "Tel Aviv stocks"], default="Your companies",
                                key="news-kind") or "Your companies"
    if kind == "Your companies":
        from .filings import _pairs
        cos = {k: {"market": k[0], "ticker": k[1], "name_en": name or k[1]}
               for k, name in _pairs(user["id"], ["watchlist", "portfolio"]).items()}
        if not cos:
            st.caption("Add companies to your watchlist to see their news here.")
            return
        names = {f'{c["name_en"]} ({c["ticker"]})': c for c in cos.values()}
        picked = st.multiselect("Companies", list(names), placeholder="All your companies", key="news-cos")
        chosen = [names[n] for n in picked] or list(cos.values())
        items = []
        with st.spinner("Collecting headlines"):
            for c in chosen[:25]:
                for i in cached_english_news(c["name_en"], c["ticker"], 8, c["market"]):
                    items.append({**i, "source": f'{c["ticker"]} · {i.get("source") or ""}'})
        items.sort(key=lambda n: n["published"].timestamp() if n.get("published") else 0, reverse=True)
        render_news(items[:80])
        st.caption("Sources: Google News and Yahoo Finance. Each company's page also has local-language news.")
        return
    if kind == "Tel Aviv stocks":
        with st.spinner("Loading TV10"):
            items = cached_tv10()
        render_news([{**i, "source": "TV10 Global"} for i in items])
        st.caption("From TV10 Global's TASE Stocks section (tv10.news), English news on Tel Aviv-listed companies.")
        return
    region = st.pills("Region", REGIONS, default="All", key="news-region") or "All"
    with st.spinner("Collecting headlines"):
        items = cached_defence_news(region)
    render_news(_tagged(items))
    st.caption("Defence news names each region's listed defence companies (Hanwha, LIG Nex1, Elbit, Rafael, "
                   "Rheinmetall, Saab and others) plus the trade press. Korean and Hebrew headlines are translated.")
