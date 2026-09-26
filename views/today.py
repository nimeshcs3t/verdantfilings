from datetime import timedelta

import streamlit as st

from core.config import direct_fetch
from core.db import as_utc
from core.ui import chip_html, esc, html_block, logo_html, long_date, page_header, relative_time
from services import personal, watch
from services.classify import ORDER, categorize, label
from services.pipeline import filings_for, sync_company
from sources import get_source

from .components import add_company_form, cached_english_news, histories, render_filing, render_news

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

    period = st.session_state.get("today-period") or "Today"
    rows = []
    for m in sorted({w["market"] for w in wl}):
        since = get_source(m).today() - timedelta(days=PERIODS[period])
        rows += filings_for([(w["market"], w["ticker"]) for w in wl if w["market"] == m], since)
    rows.sort(key=lambda r: (r["filed_date"], r["uid"]), reverse=True)
    for r in rows:
        r["category"] = categorize(r["title_en"], r["title_local"], r.get("price_sensitive"))

    n = len(wl)
    yours = "your company" if n == 1 else f"your {n} companies"
    companies_with = len({(r["market"], r["ticker"]) for r in rows})
    source_txt = yours if n == 1 else f"{companies_with} of {yours}"
    if period == "Today":
        noun = "filing" if len(rows) == 1 else "filings"
        subtitle = (f"{len(rows)} {noun} from {source_txt} today." if rows else
                    ("Your company hasn't filed yet today." if n == 1 else f"None of {yours} has filed yet today."))
    else:
        subtitle = f"{len(rows)} filings from {source_txt}, {period.lower()}."
    page_header(long_date(today), subtitle)

    main, side = st.columns([2.2, 1], gap="large")
    with main:
        cols = st.columns([1.1, 1.1, 1]) if len(markets) > 1 else st.columns([1.1, 1, 0.1])
        with cols[0]:
            st.segmented_control("Period", list(PERIODS), default="Today", key="today-period",
                                 label_visibility="collapsed")
        with cols[1]:
            st.segmented_control("View", ["By time", "By company"], default="By time", key="today-view",
                                 label_visibility="collapsed")
        if len(markets) > 1:
            with cols[2]:
                st.segmented_control("Country", ["All"] + [get_source(m).country for m in markets],
                                     default="All", key="today-market", label_visibility="collapsed")

        present = [k for k in ORDER if any(r["category"] == k for r in rows)]
        if rows:
            counts = {k: sum(r["category"] == k for r in rows) for k in present}
            html_block('<div class="brief">' + "".join(
                chip_html(k).replace("</span>", f" {counts[k]}</span>") for k in present) + "</div>")
        picked = st.pills("Categories", [label(k) for k in present], selection_mode="multi", key="today-cats",
                          label_visibility="collapsed") if len(present) > 1 else []
        if picked:
            rows = [r for r in rows if label(r["category"]) in picked]

        if errors:
            st.caption("Couldn't reach the regulator for: " + ", ".join(errors) + ". Showing saved filings.")
        notes = []
        if synced:
            notes.append(f"Last checked {relative_time(min(synced))}")
        if waiting:
            notes.append("loading filings for " + ", ".join(waiting))
        if notes:
            text = ". ".join(notes)
            st.caption(text[:1].upper() + text[1:] + ".")
        if not rows:
            html_block('<div class="empty">Nothing filed in this period. Filings usually arrive during '
                       'local business hours. Try a longer period above.</div>')

        stars = personal.starred(user["id"])
        hist = histories([(r["market"], r["ticker"]) for r in rows])
        label_country = len(markets) > 1 and choice == "All"
        if (st.session_state.get("today-view") or "By time") == "By company":
            groups: dict[tuple[str, str], list[dict]] = {}
            for r in rows:
                groups.setdefault((r["market"], r["ticker"]), []).append(r)
            for (m, t), items in groups.items():
                name = items[0]["company_name"]
                country = f'<span class="fl-tk">{esc(get_source(m).country)}</span>' if label_country else ""
                html_block(f'<div class="group-head">{logo_html(name, t)}{esc(name)}<span class="fl-tk">{esc(t)}</span>'
                           f'{country}<span class="count">{len(items)} filing{"s" if len(items) != 1 else ""}</span></div>')
                for r in items:
                    render_filing(r, key="today", show_company=False, hist=hist.get((m, t)), stars=stars)
        else:
            for r in rows:
                render_filing(r, key="today", country=get_source(r["market"]).country if label_country else "",
                              hist=hist.get((r["market"], r["ticker"])), stars=stars)
        for credit in sorted({get_source(r["market"]).attribution for r in rows} - {""}):
            st.caption(credit)

    with side:
        html_block('<div class="section" style="margin-top:.2rem">Headlines</div>')
        items = []
        for w in all_wl[:6]:
            items += cached_english_news(w["name_en"], w["ticker"], 3)[:2]
        items.sort(key=lambda n: n["published"].timestamp() if n.get("published") else 0, reverse=True)
        render_news(items[:10])
