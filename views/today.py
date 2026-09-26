from datetime import timedelta

import streamlit as st

from core.ui import html_block, long_date, page_header
from services import watch
from services.pipeline import filings_for, sync_company
from sources import get_source

from .components import add_company_form, cached_english_news, render_filing, render_news

PERIODS = {"Today": 0, "Last 7 days": 7, "Last 30 days": 30}


def page() -> None:
    user = st.session_state["user"]
    wl = watch.get_watchlist(user["id"])
    markets = sorted({w["market"] for w in wl}) or ["KR"]
    today = get_source(markets[0]).today()

    if not wl:
        page_header(long_date(today), "Add the companies you follow and their filings will show up here each day.")
        add_company_form(user, key="today-empty")
        return

    errors = []
    with st.spinner("Checking regulators for new filings"):
        for w in wl:
            try:
                sync_company(w["market"], w["ticker"])
            except Exception:
                errors.append(w["name_en"])

    period = st.session_state.get("today-period") or "Today"
    since = today - timedelta(days=PERIODS[period])
    rows = filings_for([(w["market"], w["ticker"]) for w in wl], since)
    companies_with = len({(r["market"], r["ticker"]) for r in rows})

    if period == "Today":
        noun = "filing" if len(rows) == 1 else "filings"
        subtitle = (f"{len(rows)} {noun} from {companies_with} of your {len(wl)} companies today."
                    if rows else f"None of your {len(wl)} companies has filed yet today.")
    else:
        subtitle = f"{len(rows)} filings from {companies_with} of your {len(wl)} companies, {period.lower()}."
    page_header(long_date(today), subtitle)

    main, side = st.columns([2.1, 1], gap="large")
    with main:
        st.segmented_control("Period", list(PERIODS), default="Today", key="today-period",
                             label_visibility="collapsed")
        if errors:
            st.caption("Couldn't reach the regulator for: " + ", ".join(errors) + ". Showing saved filings.")
        if not rows:
            html_block('<div class="empty">Nothing filed in this period. Filings usually arrive during '
                       'Korean business hours, 8am to 7pm KST. Try a longer period above.</div>')
        for r in rows:
            render_filing(r, key="today")

    with side:
        html_block('<div class="section" style="margin-top:.2rem">Headlines</div>')
        items = []
        for w in wl[:6]:
            for n in cached_english_news(w["name_en"], w["ticker"], 3)[:2]:
                items.append(n)
        items.sort(key=lambda n: n["published"].timestamp() if n.get("published") else 0, reverse=True)
        render_news(items[:10])
