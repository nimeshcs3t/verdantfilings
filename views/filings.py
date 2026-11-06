"""All filings: one table of every filing from the companies you follow, hold or have as ideas, over a chosen period,
with filters and a CSV download. (Today shows the latest days as a reading list; this is for looking back and scanning.)"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core.ui import html_block, page_header
from services import personal, portfolio, watch
from services.classify import categorize, label
from services.pipeline import filings_for

PERIODS = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "Last 6 months": 182, "Last 12 months": 365}
SCOPES = {"watchlist": "Watchlist", "portfolio": "Portfolio", "ideas": "Stock ideas"}


def _pairs(uid: int, scopes: list[str]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    if "watchlist" in scopes:
        for w in watch.get_watchlist(uid):
            out[(w["market"], w["ticker"])] = w["name_en"]
    if "portfolio" in scopes:
        for h in portfolio.current_holdings(uid):
            out.setdefault((h["market"], h["ticker"]), h.get("name") or h["ticker"])
    if "ideas" in scopes:
        from services import ideas
        for i in ideas.list_ideas(uid):
            out.setdefault((i["market"], i["ticker"]), i.get("name") or i["ticker"])
    return {k: v for k, v in out.items() if k[0] != portfolio.OTHER}     # any-market symbols have no filings source


def page() -> None:
    user = st.session_state["user"]
    uid = user["id"]
    page_header("All filings", "Every filing from your companies in one table")
    c1, c2, c3 = st.columns([1.6, 1, 1.4])
    scopes = c1.pills("Companies from", list(SCOPES), selection_mode="multi", default=["watchlist", "portfolio"],
                      format_func=SCOPES.get, key="af-scope")
    period = c2.selectbox("Period", list(PERIODS), index=1, key="af-period")
    q = c3.text_input("Search titles", placeholder="e.g. dividend, buyback, contract", key="af-q")
    pairs = _pairs(uid, scopes or ["watchlist"])
    if not pairs:
        html_block('<div class="empty">No companies yet. Add some to your watchlist, portfolio or stock ideas.</div>')
        return
    rows = filings_for(list(pairs), date.today() - timedelta(days=PERIODS[period]), limit=3000)
    marks = dict(personal.flags(uid))
    data = []
    for r in rows:
        title_en = r.get("title_en") or ""
        cat = categorize(title_en, r.get("title_local"))
        data.append({"Date": r["filed_date"], "Company": pairs.get((r["market"], r["ticker"])) or r.get("company_name") or r["ticker"],
                     "Ticker": r["ticker"], "Market": r["market"], "Type": label(cat),
                     "Title": title_en or r.get("title_local") or "", "Original title": r.get("title_local") or "",
                     "Flag": {"important": "Important", "keep": "Keep", "read_once": "Read once"}.get(marks.get(r["uid"]), ""),
                     "Link": r.get("url") or None})
    df = pd.DataFrame(data)
    if df.empty:
        st.caption(f"No filings in the {period.lower()} for these companies.")
        return
    c4, c5, c6 = st.columns([1.6, 1.6, 1])
    types = c4.multiselect("Types", sorted(df["Type"].unique()), placeholder="All types", key="af-types")
    companies = c5.multiselect("Companies", sorted(df["Company"].unique()), placeholder="All companies", key="af-cos")
    only_flagged = c6.toggle("Flagged only", key="af-flag")
    if types:
        df = df[df["Type"].isin(types)]
    if companies:
        df = df[df["Company"].isin(companies)]
    if only_flagged:
        df = df[df["Flag"] != ""]
    if q.strip():
        t = q.strip().lower()
        df = df[df["Title"].str.lower().str.contains(t, regex=False) | df["Original title"].str.lower().str.contains(t, regex=False)]
    st.caption(f"{len(df)} filing{'s' if len(df) != 1 else ''} from {df['Company'].nunique()} compan"
               f"{'y' if df['Company'].nunique() == 1 else 'ies'}. Click a column name to sort.")
    st.dataframe(df, hide_index=True, width="stretch", height=min(35 * (len(df) + 1) + 3, 760),
                 column_order=["Date", "Company", "Ticker", "Market", "Type", "Title", "Flag", "Link"],
                 column_config={"Date": st.column_config.DateColumn(format="D MMM YYYY", width="small"),
                                "Ticker": st.column_config.TextColumn(width="small"),
                                "Market": st.column_config.TextColumn(width="small"),
                                "Title": st.column_config.TextColumn(width="large"),
                                "Flag": st.column_config.TextColumn(width=90),
                                "Link": st.column_config.LinkColumn(display_text="Open", width="small")})
    st.download_button("Download as CSV", df.to_csv(index=False).encode(), file_name=f"filings-{date.today()}.csv",
                       mime="text/csv", icon=":material/download:")
