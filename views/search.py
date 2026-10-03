from datetime import timedelta

import streamlit as st

from core.ui import html_block, page_header
from services import personal
from services.classify import ORDER, label
from services.search import search, to_csv
from sources import configured_sources, get_source, visible_sources

from .components import histories, render_filing

PERIODS = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "Last year": 365, "Everything": None}


def page() -> None:
    user = st.session_state["user"]
    page_header("Search", "Every filing stored for the companies anyone here follows.")
    c1, c2, c3 = st.columns([2.2, 1, 1], vertical_alignment="bottom")
    text = c1.text_input("Words", placeholder="e.g. buyback, rights offering, Samsung", key="s-text")
    markets = {s.country: s.market for s in visible_sources(user)}
    country = c2.selectbox("Country", ["All countries"] + list(markets), key="s-country")
    period = c3.selectbox("Period", list(PERIODS), index=2, key="s-period")
    c4, c5 = st.columns([3, 1], vertical_alignment="center")
    cats = c4.pills("Categories", [label(k) for k in ORDER], selection_mode="multi", key="s-cats",
                    label_visibility="collapsed")
    only_starred = c5.toggle("Starred only", key="s-starred")

    market = markets.get(country)
    days = PERIODS[period]
    since = (get_source(market).today() if market else max(s.today() for s in configured_sources())) - timedelta(days=days) \
        if days else None
    stars = personal.starred(user["id"])
    rows = search(text, market, since, stars if only_starred else None)
    if cats:
        rows = [r for r in rows if label(r["category"]) in cats]

    left, right = st.columns([3, 1], vertical_alignment="center")
    left.caption(f"{len(rows)} filing{'s' if len(rows) != 1 else ''}" + (" (showing the latest 300)" if len(rows) >= 300 else ""))
    if rows:
        right.download_button("Download CSV", to_csv(rows), file_name="filings.csv", mime="text/csv", width="stretch",
                              icon=":material/download:")
    else:
        html_block('<div class="empty">No filings match. Try fewer words or a longer period.</div>')
    several = len({r["market"] for r in rows}) > 1
    hist = histories([(r["market"], r["ticker"]) for r in rows[:60]])
    for r in rows[:150]:
        render_filing(r, key="s", country=get_source(r["market"]).country if several and get_source(r["market"]) else "",
                      hist=hist.get((r["market"], r["ticker"])), stars=stars)
    if len(rows) > 150:
        st.caption("Showing the first 150 here. The CSV has them all.")
