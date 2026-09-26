from datetime import timedelta

import streamlit as st

from core.config import direct_fetch
from core.db import as_utc
from core.ui import html_block, long_date, page_header, relative_time
from services import watch
from services.pipeline import filings_for, sync_company
from sources import get_source

from .components import add_company_form, cached_english_news, render_filing, render_news

PERIODS = {"Today": 0, "Last 7 days": 7, "Last 30 days": 30}


def page() -> None:
    user = st.session_state["user"]
    wl = watch.get_watchlist(user["id"])
    markets = sorted({w["market"] for w in wl}) or ["KR"]
    today = max(get_source(m).today() for m in markets)

    if not wl:
        page_header(long_date(today), "Add the companies you follow and their filings will show up here each day.")
        add_company_form(user, key="today-empty")
        return

    errors, waiting = [], [w["name_en"] for w in wl if not w.get("last_synced")]
    if direct_fetch():
        with st.spinner("Checking regulators for new filings"):
            for w in wl:
                try:
                    sync_company(w["market"], w["ticker"])
                except Exception:
                    errors.append(w["name_en"])
    synced = [as_utc(w["last_synced"]) for w in wl if w.get("last_synced")]

    all_wl = wl
    choice = st.session_state.get("today-market") or "All"
    if len(markets) > 1 and choice != "All":
        wl = [w for w in wl if get_source(w["market"]).country == choice] or all_wl
        today = get_source(wl[0]["market"]).today()

    # Each country's "today" is its own local date.
    period = st.session_state.get("today-period") or "Today"
    rows = []
    for m in sorted({w["market"] for w in wl}):
        since = get_source(m).today() - timedelta(days=PERIODS[period])
        rows += filings_for([(w["market"], w["ticker"]) for w in wl if w["market"] == m], since)
    rows.sort(key=lambda r: (r["filed_date"], r["uid"]), reverse=True)
    companies_with = len({(r["market"], r["ticker"]) for r in rows})

    n = len(wl)
    yours = "your company" if n == 1 else f"your {n} companies"
    source_txt = yours if n == 1 else f"{companies_with} of {yours}"
    if period == "Today":
        noun = "filing" if len(rows) == 1 else "filings"
        subtitle = (f"{len(rows)} {noun} from {source_txt} today." if rows else
                    ("Your company hasn't filed yet today." if n == 1 else f"None of {yours} has filed yet today."))
    else:
        subtitle = f"{len(rows)} filings from {source_txt}, {period.lower()}."
    page_header(long_date(today), subtitle)

    main, side = st.columns([2.1, 1], gap="large")
    with main:
        c1, c2 = st.columns([1, 1]) if len(markets) > 1 else (st.container(), None)
        with c1:
            st.segmented_control("Period", list(PERIODS), default="Today", key="today-period",
                                 label_visibility="collapsed")
        if c2 is not None:
            with c2:
                st.segmented_control("Country", ["All"] + [get_source(m).country for m in markets],
                                     default="All", key="today-market", label_visibility="collapsed")
        if errors:
            st.caption("Couldn't reach the regulator for: " + ", ".join(errors) + ". Showing saved filings.")
        if synced:
            st.caption(f"Last checked {relative_time(min(synced))}. New filings are picked up every 10 minutes.")
        if waiting:
            st.caption("Loading filings for " + ", ".join(waiting) + ". They'll appear within a few minutes.")
        if not rows:
            html_block('<div class="empty">Nothing filed in this period. Filings usually arrive during '
                       'local business hours. Try a longer period above.</div>')
        label_country = len(markets) > 1 and choice == "All"
        for r in rows:
            render_filing(r, key="today", country=get_source(r["market"]).country if label_country else "")

    with side:
        html_block('<div class="section" style="margin-top:.2rem">Headlines</div>')
        items = []
        for w in wl[:6]:
            for n in cached_english_news(w["name_en"], w["ticker"], 3)[:2]:
                items.append(n)
        items.sort(key=lambda n: n["published"].timestamp() if n.get("published") else 0, reverse=True)
        render_news(items[:10])
