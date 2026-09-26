from datetime import timedelta

import streamlit as st

from core.ui import esc, html_block, logo_html, page_header
from services import portfolio, prices, watch
from services.pipeline import filings_for, get_company, search_companies
from sources import configured_sources, get_source

from .components import histories


def _gain_html(v: float | None, pct: float | None, cur: str) -> str:
    if v is None:
        return "–"
    cls = "up" if v > 0 else "down" if v < 0 else "flat"
    return f'<span class="mv {cls}">{v:+,.0f} {cur} ({pct * 100:+.1f}%)</span>' if pct is not None else f"{v:+,.0f}"


def page() -> None:
    user = st.session_state["user"]
    page_header("Portfolio", "Your holdings, their value and recent filings, and price alerts.")
    sources = configured_sources()
    markets = {s.country: s.market for s in sources}

    with st.expander("Add or update a holding", icon=":material/add:", expanded=not portfolio.list_holdings(user["id"])):
        with st.form("holding", clear_on_submit=True, border=False):
            c1, c2, c3, c4 = st.columns([1.2, 1.6, 1, 1])
            country = c1.selectbox("Country", list(markets))
            query = c2.text_input("Ticker or name", placeholder="e.g. 005930, AAPL, BHP")
            shares = c3.number_input("Shares", min_value=0.0, step=1.0, format="%.4g")
            avg = c4.number_input("Average price paid", min_value=0.0, step=1.0, format="%.4g")
            also = st.checkbox("Also follow its filings (add to watchlist)", value=True)
            if st.form_submit_button("Save holding", type="primary"):
                market = markets[country]
                src = get_source(market)
                ticker = src.normalize_ticker(query)
                comp = get_company(market, ticker) if ticker else None
                if not comp:
                    found = search_companies(market, query)
                    comp = get_company(market, found[0].ticker) if len(found) == 1 else None
                if not comp:
                    st.warning("Couldn't find that company. Use its ticker, e.g. 005930 or AAPL.")
                else:
                    err = portfolio.save_holding(user["id"], market, comp["ticker"], shares, avg)
                    if err:
                        st.warning(err)
                    else:
                        if also and not watch.is_watching(user["id"], market, comp["ticker"]):
                            watch.add(user, market, comp["ticker"])
                        st.toast(f"Saved {comp['name_en']}")
                        st.rerun()

    items = portfolio.list_holdings(user["id"])
    if not items:
        html_block('<div class="empty">No holdings yet. Add one above to see its value, gain or loss, and filings.</div>')
    else:
        hist = histories([(h["market"], h["ticker"]) for h in items])
        rows = [portfolio.value(h, hist.get((h["market"], h["ticker"])) or []) for h in items]
        totals: dict[str, list[float]] = {}
        for r in rows:
            if r["value"] is not None:
                t = totals.setdefault(r["currency"], [0.0, 0.0])
                t[0] += r["value"]
                t[1] += r["cost"]
        html_block('<div class="stat-grid">' + "".join(
            f'<div class="stat"><div class="k">Total in {esc(c)}</div><div class="v">{val:,.0f}</div>'
            f'<div>{_gain_html(val - cost, (val / cost - 1) if cost else None, c)}</div></div>'
            for c, (val, cost) in totals.items()) + "</div>")
        recent = {}
        for r in rows:
            src = get_source(r["market"])
            n = len(filings_for([(r["market"], r["ticker"])], src.today() - timedelta(days=7))) if src else 0
            recent[(r["market"], r["ticker"])] = n
        names = {}
        for r in rows:
            comp = get_company(r["market"], r["ticker"], resolve=False) or {}
            names[(r["market"], r["ticker"])] = comp.get("name_en", r["ticker"])
        lines = []
        for r in rows:
            key = (r["market"], r["ticker"])
            last = "–" if r["last"] is None else f'{r["last"]:,.2f}'
            worth = "–" if r["value"] is None else f'{r["value"]:,.0f} {r["currency"]}'
            lines.append(f'<tr><td>{logo_html(names[key], r["ticker"])}{esc(names[key])} <span class="fl-tk">{esc(r["ticker"])}</span></td>'
                         f'<td class="num">{r["shares"]:,.4g}</td><td class="num">{r["avg_price"]:,.2f}</td>'
                         f'<td class="num">{last}</td><td class="num">{worth}</td>'
                         f'<td class="num">{_gain_html(r["gain"], r["gain_pct"], "")}</td>'
                         f'<td class="num">{prices.move_html(r["day"])}</td><td class="num">{recent[key] or ""}</td></tr>')
        body = "".join(lines)
        html_block('<div class="tbl-wrap"><table class="tbl"><tr><th>Company</th><th class="num">Shares</th>'
                   '<th class="num">Avg price</th><th class="num">Last</th><th class="num">Value</th>'
                   '<th class="num">Gain / loss</th><th class="num">Today</th><th class="num">Filings (7d)</th></tr>'
                   f'{body}</table></div>')
        st.caption("Prices from free sources and may be delayed. Totals are per currency (no conversion).")
        with st.expander("Remove a holding"):
            for r in rows:
                if st.button(f"Remove {names[(r['market'], r['ticker'])]}", key=f"rm-{r['market']}-{r['ticker']}",
                             type="tertiary"):
                    portfolio.remove_holding(user["id"], r["market"], r["ticker"])
                    st.rerun()

    st.subheader("Price alerts", divider=False)
    st.caption("Sent to Telegram at the next check (about every 10 minutes). Price above / below alerts fire once, "
               "then switch off; daily-move alerts can fire once a day.")
    wl = watch.get_watchlist(user["id"])
    choices = [("*", "*", "Any company on my watchlist")] + [(w["market"], w["ticker"], f'{w["name_en"]} ({w["ticker"]})') for w in wl]
    with st.form("price-alert", clear_on_submit=True, border=False):
        c1, c2, c3, c4 = st.columns([1.8, 1.4, 0.9, 0.7], vertical_alignment="bottom")
        pick = c1.selectbox("Company", range(len(choices)), format_func=lambda i: choices[i][2])
        kind = c2.selectbox("When", list(portfolio.KINDS), format_func=lambda k: portfolio.KINDS[k])
        value = c3.number_input("% or price", min_value=0.0, value=5.0, step=0.5)
        if c4.form_submit_button("Add", width="stretch"):
            m, t, _ = choices[pick]
            if m == "*" and kind != "move":
                st.warning("For the whole watchlist, use a daily-move alert.")
            else:
                err = portfolio.add_alert(user["id"], m, t, kind, value)
                st.warning(err) if err else st.rerun()
    alerts = portfolio.list_alerts(user["id"])
    if not alerts:
        st.caption("No price alerts yet.")
    for a in alerts:
        who = "Any watchlist company" if a["ticker"] == "*" else a["ticker"]
        what = f"moves {a['value']:g}% or more in a day" if a["kind"] == "move" else \
            f"{'rises above' if a['kind'] == 'above' else 'falls below'} {a['value']:,.2f}"
        status = "" if a["active"] else '<span class="fl-tk">triggered</span>'
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        c1.markdown(f"**{esc(who)}** {esc(what)} {status}", unsafe_allow_html=True)
        if c2.button("Remove", key=f"pa-{a['id']}", type="tertiary"):
            portfolio.remove_alert(user["id"], a["id"])
            st.rerun()
