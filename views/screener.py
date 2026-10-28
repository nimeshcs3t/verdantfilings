"""Screener page: announcements across exchanges, your own companies, and the whole US market."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import nav
from core.db import as_utc
from core.ui import esc, html_block, page_header, relative_time
from services import screener as scr, us_screener, watch
from services.classify import CATEGORIES
from sources import get_source, visible_sources

PCT = lambda v: None if v is None else round(v * 100, 1)


def page() -> None:
    user = st.session_state["user"]
    page_header("Screener", "Find companies by what they announce, compare the ones you follow, and screen US stocks "
                "by valuation, growth and margins.")
    t1, t2, t3 = st.tabs(["Announcements", "My companies", "US stocks"])
    with t1:
        announcements(user)
    with t2:
        my_companies(user)
    with t3:
        us_stocks(user)


# ---- announcements ----------------------------------------------------------------------------------------------------
def announcements(user: dict) -> None:
    markets = {s.country: s.market for s in visible_sources(user)}
    counts = scr.event_counts(7)
    c1, c2, c3, c4 = st.columns([1.6, 2, 0.9, 1.2])
    pick_c = c1.multiselect("Countries", list(markets), key="ev-c", placeholder="All countries")
    types = c2.multiselect("Announcement types", scr.EVENT_TYPES, default=["buyback", "insider", "ownership"],
                           format_func=lambda k: f"{CATEGORIES[k][0]} ({counts.get(k, 0)})", key="ev-t")
    days = c3.selectbox("Period", [1, 3, 7, 14, 30], index=2, format_func=lambda d: "Today" if d == 1 else f"{d} days", key="ev-d")
    text = c4.text_input("Search", key="ev-q", placeholder="Company or words")
    rows = scr.events([markets[c] for c in pick_c], types, days, text)
    following = {(w["market"], w["ticker"]) for w in watch.get_watchlist(user["id"])}
    if not rows:
        html_block('<div class="empty">No announcements of these types yet. They are collected from each market\'s '
                   'latest filings every 30 minutes and kept for 30 days.</div>')
        return
    st.caption(f"{len(rows)} announcement{'s' if len(rows) != 1 else ''}"
               + (" (showing the latest 500)" if len(rows) >= 500 else ""))
    shown = st.session_state.get("ev-n", 80)
    for i, r in enumerate(rows[:shown]):
        src = get_source(r["market"])
        country = src.country if src else r["market"]
        is_following = (r["market"], r["ticker"]) in following
        orig = (f'<div class="fl-orig ko">{esc(r["title_local"])}</div>'
                if r.get("title_local") and r.get("title_en") and r["title_local"] != r["title_en"] else "")
        html_block(f'<div class="fl-row"><div class="fl-date">{r["filed_date"]:%d %b}</div><div><div class="fl-co">'
                   f'{esc(r["company"] or r["ticker"] or "")}<span class="fl-tk">{esc(r["ticker"] or "")}</span>'
                   f'<span class="fl-tk">{esc(country)}</span>'
                   + ('<span class="fl-mark keep">Following</span>' if is_following else "")
                   + f'</div><div class="fl-title">{esc(r["title_en"] or r["title_local"])}'
                   f'<span class="chip c-{r["category"]}">{esc(CATEGORIES[r["category"]][0])}</span></div>{orig}</div>'
                   f'<div class="fl-side"><span class="fl-link"><a href="{esc(r["url"])}" target="_blank" '
                   f'rel="noopener noreferrer">Original</a></span></div></div>')
        if r["ticker"] and not is_following:
            if st.button("Follow", key=f"ev-add-{i}-{r['uid']}", type="tertiary", icon=":material/add:"):
                comp, err = watch.add(user, r["market"], r["ticker"])
                st.warning(err) if err else (st.toast(f"Added {comp['name_en']}"), st.rerun())
    if len(rows) > shown and st.button(f"Show more ({len(rows) - shown} left)", key="ev-more"):
        st.session_state["ev-n"] = shown + 80
        st.rerun()
    st.caption("From each exchange's or regulator's list of the day's filings (Korea, USA, Japan, Hong Kong, Taiwan, "
               "France, Norway, the Nordics, Poland), sorted by title. Check the original before acting.")


# ---- my companies -----------------------------------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _my_rows(user_id: int, signature: str) -> list[dict]:
    return scr.my_companies(user_id)


def my_companies(user: dict) -> None:
    wl = watch.get_watchlist(user["id"])
    signature = ",".join(sorted(f"{w['market']}:{w['ticker']}" for w in wl))
    with st.spinner("Gathering prices and figures"):
        rows = _my_rows(user["id"], signature)
    if not rows:
        html_block('<div class="empty">Follow or buy some companies first.</div>')
        return
    df = pd.DataFrame([{
        "Company": r["name"], "Ticker": r["ticker"], "Country": r["country"], "Held": r["held"],
        "Price": r["price"], "Currency": r["currency"], "1-year %": PCT(r["ret1y"]), "P/E": r["pe"], "P/S": r["ps"],
        "Growth %": PCT(r["growth"]), "Op. margin %": PCT(r["op_margin"]), "Net margin %": PCT(r["net_margin"]),
        "From high %": PCT(r["drawdown"]), "vs fair value %": PCT(r["vs_fair"]), "vs target %": PCT(r["vs_target"]),
        "_m": r["market"]} for r in rows])
    for col in df.columns:
        if col not in ("Company", "Ticker", "Country", "Held", "Currency", "_m"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    c1, c2, c3, c4 = st.columns(4)
    countries = c1.multiselect("Country", sorted(df["Country"].unique()), key="mc-c", placeholder="All")
    pe_max = c2.number_input("P/E at most", min_value=0.0, value=0.0, step=5.0, key="mc-pe", format="%.0f", help="0 = no limit")
    growth_min = c3.number_input("Growth at least %", value=-100.0, step=5.0, key="mc-g")
    below_fair = c4.toggle("Below my fair value", key="mc-fv")
    view = df
    if countries:
        view = view[view["Country"].isin(countries)]
    if pe_max:
        view = view[view["P/E"].notna() & (view["P/E"] <= pe_max)]
    if growth_min > -100:
        view = view[view["Growth %"].notna() & (view["Growth %"] >= growth_min)]
    if below_fair:
        view = view[view["vs fair value %"].notna() & (view["vs fair value %"] < 0)]
    st.caption(f"{len(view)} of {len(df)} companies. Click a column name to sort.")
    event = st.dataframe(view.drop(columns=["_m"]), hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key="mc-table",
                         column_config={"Held": st.column_config.CheckboxColumn(width="small"),
                                        "Price": st.column_config.NumberColumn(format="%.2f"),
                                        "P/E": st.column_config.NumberColumn(format="%.1f"),
                                        "P/S": st.column_config.NumberColumn(format="%.1f"),
                                        **{c: st.column_config.NumberColumn(format="%+.1f") for c in
                                           ("1-year %", "Growth %", "Op. margin %", "Net margin %", "From high %",
                                            "vs fair value %", "vs target %")}})
    picked = event.selection.rows if event and getattr(event, "selection", None) else []
    if picked:
        r = view.iloc[picked[0]]
        if st.button(f"Open {r['Company']}", type="primary", key="mc-open"):
            nav.open_company(r["_m"], r["Ticker"])
    st.caption("Figures in each company's own currency, from the latest reported year (Taiwan: year to date, "
               "annualised). vs fair value / target: price compared with your own fair value and journal price target. "
               "Refreshed hourly.")


# ---- US market --------------------------------------------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def _us_rows() -> list[dict]:
    return us_screener.rows()


def us_stocks(user: dict) -> None:
    data = _us_rows()
    info = us_screener.status()
    if not data:
        html_block('<div class="empty">US market data isn\'t loaded yet. It loads with the IPO calendar job (twice a '
                   'day); the admin can run it now on GitHub (Actions, IPO calendar, Run workflow).</div>')
        return
    sectors = sorted({r["sector"] for r in data if r.get("sector")})
    c1, c2, c3 = st.columns([1.6, 1.6, 1])
    pick_s = c1.multiselect("Sector", sectors, key="us-s", placeholder="All sectors")
    bands = c2.multiselect("Size", list(us_screener.CAP_BANDS), key="us-b", placeholder="Any size")
    text = c3.text_input("Search", key="us-q", placeholder="Ticker, name, industry")
    c4, c5, c6, c7, c8 = st.columns(5)
    pe_max = c4.number_input("P/E at most", min_value=0.0, value=0.0, step=5.0, key="us-pe", format="%.0f", help="0 = no limit")
    ps_max = c5.number_input("P/S at most", min_value=0.0, value=0.0, step=1.0, key="us-ps", format="%.0f", help="0 = no limit")
    growth = c6.number_input("Revenue growth ≥ %", value=-100.0, step=5.0, key="us-g", format="%.0f", help="-100 = no limit")
    margin = c7.number_input("Net margin ≥ %", value=-100.0, step=5.0, key="us-nm", format="%.0f", help="-100 = no limit")
    profitable = c8.toggle("Profitable only", key="us-p")
    hits = us_screener.screen(data, pick_s, bands, pe_max or None, ps_max or None,
                              growth / 100 if growth > -100 else None, margin / 100 if margin > -100 else None,
                              None, profitable, text)
    hits.sort(key=lambda r: -(r.get("market_cap") or 0))
    df = pd.DataFrame([{
        "Ticker": r["ticker"], "Company": r.get("name"), "Sector": r.get("sector"), "Industry": r.get("industry"),
        "Price $": r.get("price"), "Day %": r.get("change_pct"), "Market cap $B": (r["market_cap"] / 1e9) if r.get("market_cap") else None,
        "P/E": r.get("pe"), "P/S": r.get("ps"), "P/B": r.get("pb"), "Revenue $M": (r["revenue"] / 1e6) if r.get("revenue") else None,
        "Growth %": PCT(r.get("growth")), "Last qtr growth %": PCT(r.get("growth_q")),
        "Op. margin %": PCT(r.get("op_margin")), "Net margin %": PCT(r.get("net_margin"))} for r in hits[:1000]])
    for col in df.columns:
        if col not in ("Ticker", "Company", "Sector", "Industry"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    st.caption(f"{len(hits):,} of {len(data):,} US stocks" + (" (showing the 1,000 largest)" if len(hits) > 1000 else "")
               + ". Select rows to follow them.")
    if df.empty:
        return
    event = st.dataframe(df, hide_index=True, width="stretch", height=520, on_select="rerun", selection_mode="multi-row",
                         key="us-table", column_config={
                             "Price $": st.column_config.NumberColumn(format="%.2f"),
                             "Day %": st.column_config.NumberColumn(format="%+.2f"),
                             "Market cap $B": st.column_config.NumberColumn(format="%.2f"),
                             "Revenue $M": st.column_config.NumberColumn(format="%,.0f"),
                             **{c: st.column_config.NumberColumn(format="%.1f") for c in ("P/E", "P/S", "P/B")},
                             **{c: st.column_config.NumberColumn(format="%+.1f") for c in
                                ("Growth %", "Last qtr growth %", "Op. margin %", "Net margin %")}})
    picked = list(event.selection.rows) if event and getattr(event, "selection", None) else []
    if picked:
        tickers = [df.iloc[i]["Ticker"] for i in picked]
        if st.button(f"Follow {len(tickers)} selected", type="primary", key="us-follow"):
            added, problems = 0, []
            for t in tickers:
                _, err = watch.add(user, "US", t)
                if err:
                    problems.append(f"{t}: {err}")
                else:
                    added += 1
            st.success(f"Added {added} to your watchlist.") if added else None
            for p in problems[:5]:
                st.warning(p)
    when = f"prices {relative_time(info['prices_at'])}" if info.get("prices_at") else "prices not loaded"
    figures = (f"figures for {info['fiscal_year']}" + (f" and {info['quarter']}" if info.get("quarter") else "")
               if info.get("fiscal_year") else "SEC figures load weekly")
    st.caption(f"Nasdaq stock list ({when}); SEC XBRL filings ({figures}). Ratios use the last reported calendar "
               "year; companies with unusual fiscal years may be a year behind. Not investment advice.")
