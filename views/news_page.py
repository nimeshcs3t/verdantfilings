"""News: your companies (Google News and Yahoo Finance), defence industry news by region (US, Europe, Israel,
Korea, with Korean and Hebrew headlines translated), and market wraps by region."""
from __future__ import annotations

import streamlit as st

from core.ui import page_header
from .components import cached_defence_news, cached_english_news, cached_market_news, render_news

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
    page_header("News", "Your companies, defence industry news by region, and market wraps")
    kind = st.segmented_control("Show", ["Your companies", "Defence", "Markets"], default="Your companies",
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
    region = st.pills("Region", REGIONS + (["Japan", "Australia"] if kind == "Markets" else []), default="All",
                      key=f"news-region-{kind}") or "All"
    with st.spinner("Collecting headlines"):
        items = cached_defence_news(region) if kind == "Defence" else cached_market_news(region)
    render_news(_tagged(items))
    if kind == "Defence":
        st.caption("Defence news names each region's listed defence companies (Hanwha, LIG Nex1, Elbit, Rafael, "
                   "Rheinmetall, Saab and others) plus the trade press. Korean and Hebrew headlines are translated.")
