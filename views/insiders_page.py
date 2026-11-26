"""Insider trades: every insider transaction for your companies in one table (and one downloadable file), so you
don't have to open each insider filing. Parsed trades where the app reads them (US Form 4, Korea), and the insider
filings themselves for other markets."""
from __future__ import annotations

import io
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import select

from core.db import company_meta, filings, get_engine, insider_tx
from core.ui import html_block, page_header
from services import personal
from services.classify import categorize
from .filings import SCOPES, _pairs

PERIODS = {"Last 30 days": 30, "Last 90 days": 90, "Last 6 months": 182, "Last 12 months": 365, "Last 2 years": 730}
CODES = {"P": "Buy", "S": "Sell", "A": "Grant / award", "M": "Option exercise", "X": "Option exercise", "F": "Tax withholding",
         "G": "Gift", "D": "Returned to company", "C": "Conversion", "J": "Other", "+": "Increase", "-": "Decrease"}


SYMBOL = {"USD": "$", "EUR": "€", "KRW": "₩", "GBP": "£", "JPY": "¥", "ILS": "₪", "AUD": "A$", "HKD": "HK$", "CHF": "CHF ",
          "SEK": "SEK ", "NOK": "NOK ", "DKK": "DKK ", "PLN": "PLN ", "TWD": "NT$", "ISK": "ISK "}
VERBS = {"P": "bought", "S": "sold", "A": "was granted", "M": "exercised options for", "X": "exercised options for",
         "F": "handed back for tax", "G": "gifted", "D": "returned to the company", "C": "converted",
         "+": "added", "-": "reduced holding by"}


def money(market: str, price: float | None) -> str:
    from services.portfolio import CURRENCY
    if not price:
        return ""
    cur = CURRENCY.get(market, "")
    sym = SYMBOL.get(cur, f"{cur} " if cur else "")
    return f"{sym}{price:,.0f}" if price >= 1000 else f"{sym}{price:,.2f}"


def sentence(market: str, person: str, role: str, code: str, shares: float | None, price: float | None,
             shares_out: float | None = None) -> str:
    """'CFO bought 2,000 shares at $56.00 (0.01% of shares)'."""
    who = (role or "").strip() or (person or "").strip() or "An insider"
    if role and person and len(role) <= 12:
        who = f"{role} {person}"
    verb = VERBS.get(code or "", "traded")
    n = f"{shares:,.0f} shares" if shares else "shares"
    at = f" at {money(market, price)}" if price else ""
    share = shares / shares_out * 100 if shares and shares_out else 0
    pct = f" ({share:.2f}% of shares)" if share >= 0.01 else ""
    return f"{who} {verb} {n}{at}{pct}"


def shares_out(pairs) -> dict:
    with get_engine().connect() as conn:
        rows = conn.execute(select(company_meta.c.market, company_meta.c.ticker, company_meta.c.shares)
                            .where(company_meta.c.shares.is_not(None))).all()
    return {(m, t): sh for m, t, sh in rows if (m, t) in pairs and sh}


def _trades(pairs: dict, since: date) -> pd.DataFrame:
    if not pairs:
        return pd.DataFrame()
    markets = {m for m, _ in pairs}
    with get_engine().connect() as conn:
        rows = conn.execute(select(insider_tx, filings.c.url).select_from(
            insider_tx.outerjoin(filings, filings.c.uid == insider_tx.c.uid))
            .where(insider_tx.c.market.in_(markets), insider_tx.c.seq >= 0, insider_tx.c.tx_date >= since)
            .order_by(insider_tx.c.tx_date.desc())).mappings().all()
    data = []
    outstanding = shares_out(pairs)
    for r in rows:
        key = (r["market"], r["ticker"])
        if key not in pairs:
            continue
        value = (r["shares"] or 0) * (r["price"] or 0) if r["price"] else None
        if value and r["code"] in ("S", "-", "F", "D"):
            value = -abs(value)
        data.append({"Date": r["tx_date"], "Company": pairs[key], "Ticker": r["ticker"], "Market": r["market"],
                     "What happened": sentence(r["market"], r["person"], r["role"], r["code"], r["shares"], r["price"],
                                               outstanding.get(key)),
                     "Person": r["person"] or "", "Role": r["role"] or "", "Type": CODES.get(r["code"] or "", r["code"] or ""),
                     "Shares": r["shares"], "Price": r["price"], "Value": value, "Holding after": r["after"],
                     "Shares outstanding": outstanding.get(key),
                     "% of shares": (r["shares"] / outstanding[key] * 100) if r["shares"] and outstanding.get(key) else None,
                     "Filing": r["url"] or None, "_uid": r["uid"]})
    df = pd.DataFrame(data)
    for col in ("Shares outstanding", "% of shares", "Price", "Value", "Holding after", "Shares"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _insider_filings(pairs: dict, since: date, parsed_uids: set[str]) -> pd.DataFrame:
    from services.pipeline import filings_for
    data = []
    for r in filings_for(list(pairs), since, limit=4000):
        if r["uid"] in parsed_uids or categorize(r.get("title_en"), r.get("title_local")) != "insider":
            continue
        data.append({"Date": r["filed_date"], "Company": pairs.get((r["market"], r["ticker"])) or r["ticker"],
                     "Ticker": r["ticker"], "Market": r["market"],
                     "Title": r.get("title_en") or r.get("title_local") or "", "Filing": r.get("url") or None, "_uid": r["uid"]})
    return pd.DataFrame(data)


def _excel(trades: pd.DataFrame, other: pd.DataFrame, by_company: pd.DataFrame) -> bytes | None:
    try:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xl:
            for name, df in (("By company", by_company), ("Insider trades", trades), ("Other insider filings", other)):
                (df.drop(columns=[c for c in df.columns if c.startswith("_")]) if not df.empty
                 else pd.DataFrame({"": ["Nothing in this period"]})).to_excel(xl, sheet_name=name, index=False)
        return buf.getvalue()
    except Exception:
        return None


def page() -> None:
    user = st.session_state["user"]
    uid = user["id"]
    page_header("Insider trades", "Every insider transaction for your companies, in one place")
    c1, c2 = st.columns([1.6, 1])
    scopes = c1.pills("Companies from", list(SCOPES), selection_mode="multi", default=["watchlist", "portfolio"],
                      format_func=SCOPES.get, key="it-scope")
    period = c2.selectbox("Period", list(PERIODS), index=2, key="it-period")
    pairs = _pairs(uid, scopes or ["watchlist"])
    if not pairs:
        html_block('<div class="empty">No companies yet. Add some to your watchlist, portfolio or stock ideas.</div>')
        return
    since = date.today() - timedelta(days=PERIODS[period])
    trades = _trades(pairs, since)
    other = _insider_filings(pairs, since, set(trades["_uid"]) if not trades.empty else set())
    readable = other[other["Market"].isin(["US", "KR"])] if not other.empty else other
    if not readable.empty:
        with get_engine().connect() as conn:
            done = set(conn.execute(select(insider_tx.c.uid).where(insider_tx.c.uid.in_(list(readable["_uid"])))).scalars())
        readable = readable[~readable["_uid"].isin(done)]
    if not readable.empty:                     # US Form 4s and Korean reports the worker hasn't read yet: read them now
        us = [u for u in readable[readable["Market"] == "US"]["_uid"]]
        kr = set(readable[readable["Market"] == "KR"]["Ticker"])
        tried = st.session_state.setdefault("it-tried", set())
        fresh_us = [u for u in us if u not in tried][:12]
        fresh_kr = {t for t in kr if ("KR", t) not in tried}
        if fresh_us or fresh_kr:
            from services import insiders as insider_svc
            with st.spinner(f"Reading {len(fresh_us) + len(fresh_kr)} insider filings"):
                try:
                    insider_svc.read_now(fresh_us, fresh_kr)
                except Exception as exc:
                    st.caption(f"Some insider filings couldn't be read just now ({exc.__class__.__name__}).")
            tried.update(fresh_us)
            tried.update(("KR", t) for t in fresh_kr)
            st.rerun()

    prefs = personal.get_prefs(uid)
    hide = st.toggle("Keep insider filings off Today and alerts (they're all here instead)",
                     value=bool(prefs.get("skip_insider")), key="it-hide")
    if hide != bool(prefs.get("skip_insider")):
        personal.save_prefs(uid, skip_insider=hide)
        st.toast("Saved.")

    # who is buying / selling
    by_company = pd.DataFrame()
    if not trades.empty:
        t = trades.copy()
        t["Bought"] = t.apply(lambda r: r["Value"] if r["Type"] in ("Buy", "Increase") and r["Value"] else 0, axis=1)
        t["Sold"] = t.apply(lambda r: -r["Value"] if r["Type"] in ("Sell", "Decrease") and r["Value"] else 0, axis=1)
        by_company = (t.groupby(["Company", "Ticker", "Market"])
                      .agg(Trades=("Type", "size"), Buyers=("Person", lambda s: s[t.loc[s.index, "Type"].isin(["Buy", "Increase"])].nunique()),
                           Sellers=("Person", lambda s: s[t.loc[s.index, "Type"].isin(["Sell", "Decrease"])].nunique()),
                           Bought=("Bought", "sum"), Sold=("Sold", "sum"), Latest=("Date", "max"))
                      .reset_index())
        by_company["Net"] = by_company["Bought"] - by_company["Sold"]
        by_company = by_company.sort_values("Net", ascending=False)
        html_block('<div class="section">By company</div>')
        st.dataframe(by_company, hide_index=True, width="stretch",
                     column_config={"Bought": st.column_config.NumberColumn(format="compact", help="Value of open-market buys"),
                                    "Sold": st.column_config.NumberColumn(format="compact", help="Value of open-market sales"),
                                    "Net": st.column_config.NumberColumn(format="compact"),
                                    "Latest": st.column_config.DateColumn(format="D MMM YYYY")})
        st.caption("Values are in each company's trading currency. Grants, option exercises and tax withholding are "
                   "listed below but not counted as buying or selling.")

        html_block('<div class="section">All trades</div>')
        f1, f2 = st.columns([1.6, 1])
        kinds = f1.multiselect("Types", sorted(trades["Type"].unique()), default=[k for k in ("Buy", "Sell", "Increase", "Decrease")
                                                                                   if k in set(trades["Type"])],
                               placeholder="All types", key="it-types")
        who = f2.text_input("Person or company", key="it-who")
        shown = trades[trades["Type"].isin(kinds)] if kinds else trades
        if who.strip():
            w = who.strip().lower()
            shown = shown[shown["Person"].str.lower().str.contains(w, regex=False) | shown["Company"].str.lower().str.contains(w, regex=False)]
        empty = [c for c in ("Shares outstanding", "% of shares") if c in shown and shown[c].isna().all()]
        st.dataframe(shown.drop(columns=["_uid", *empty]), hide_index=True, width="stretch", height=min(35 * (len(shown) + 1) + 3, 640),
                     column_config={"Date": st.column_config.DateColumn(format="D MMM YYYY"),
                                    "Shares": st.column_config.NumberColumn(format="%,.0f"),
                                    "Price": st.column_config.NumberColumn(format="%,.2f"),
                                    "Value": st.column_config.NumberColumn(format="compact"),
                                    "Holding after": st.column_config.NumberColumn(format="compact"),
                                    "What happened": st.column_config.TextColumn(width="large"),
                                    "Shares outstanding": st.column_config.NumberColumn(format="compact"),
                                    "% of shares": st.column_config.NumberColumn(format="%.3f%%"),
                                    "Filing": st.column_config.LinkColumn(display_text="Open")})
    else:
        st.caption("No parsed insider trades for these companies in this period. Parsed trades come from US Form 4 "
                   "filings and Korean insider reports.")

    if not other.empty:
        html_block('<div class="section">Insider filings from other markets</div>')
        st.caption("Managers' transactions, director dealings and similar notices from markets where the app lists the "
                   "filing but doesn't read the numbers. Open one to see the details, or use its Overview on Today.")
        st.dataframe(other.drop(columns=["_uid"]), hide_index=True, width="stretch", height=min(35 * (len(other) + 1) + 3, 520),
                     column_config={"Date": st.column_config.DateColumn(format="D MMM YYYY"),
                                    "Title": st.column_config.TextColumn(width="large"),
                                    "Filing": st.column_config.LinkColumn(display_text="Open")})

    if trades.empty and other.empty:
        return
    d1, d2, d3 = st.columns([1, 1, 1.6])
    xl = _excel(trades, other, by_company)
    if xl:
        d1.download_button("Download Excel file", xl, file_name=f"insider-trades-{date.today()}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/download:", type="primary")
    both = pd.concat([trades.assign(Kind="Trade"), other.assign(Kind="Filing")], ignore_index=True)
    d2.download_button("Download CSV", both.drop(columns=["_uid"]).to_csv(index=False).encode(),
                       file_name=f"insider-trades-{date.today()}.csv", mime="text/csv", icon=":material/download:")
    uids = sorted(set(trades["_uid"])) if not trades.empty else []
    if uids and d3.button(f"Mark the {len(uids)} parsed filings as Read once", key="it-readonce",
                          help="So they're cleared with the other Read once filings when you free up space. Their trades "
                               "stay in this table. Filings from other markets aren't included: deleting them would lose "
                               "the details."):
        for u in uids:
            personal.set_flag(uid, u, "read_once")
        st.toast(f"Marked {len(uids)} filings as Read once.")
