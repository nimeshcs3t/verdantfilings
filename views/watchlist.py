import streamlit as st

from core import nav
from core.plans import watchlist_limit
from core.ui import esc, html_block, logo_html, page_header
from services import personal, prices, telegram, watch
from sources import get_source

from .components import add_company_form, histories

LEVELS = {"All filings": "all", "Major only": "major", "Off": "off"}


def page() -> None:
    user = st.session_state["user"]
    wl = watch.get_watchlist(user["id"])
    limit = watchlist_limit(user["plan"])
    page_header("Watchlist", f"You follow {len(wl)} of {limit} companies on the {user['plan']} plan.")
    add_company_form(user, key="wl")

    if not wl:
        html_block('<div class="empty">Your watchlist is empty. Add a company above by ticker or name.</div>')
        return

    if telegram.enabled() and not user.get("telegram_chat_id"):
        st.caption("Connect Telegram on the Account page to get an alert when these companies file.")

    html_block('<div class="section">Companies</div>')
    st.caption("Alerts: All filings, Major only (results, dividends, buybacks, deals, capital raises, stake changes, "
               "reports), or Off. Set digest and keyword alerts on the Account page.")
    several = len({w["market"] for w in wl}) > 1
    levels = personal.levels(user["id"])
    hist = histories([(w["market"], w["ticker"]) for w in wl])
    for w in wl:
        pair = (w["market"], w["ticker"])
        h = hist.get(pair) or []
        country = f'<span class="fl-tk">{esc(get_source(w["market"]).country)}</span>' if several else ""
        c1, c2, c3, c4, c5 = st.columns([3, 1.6, 1.4, 0.8, 0.9], vertical_alignment="center")
        c1.markdown(f'<div class="fl-co">{logo_html(w["name_en"], w["ticker"], market=w["market"])}{esc(w["name_en"])}'
                    f'<span class="fl-tk">{esc(w["ticker"])}</span>{country}</div>'
                    f'<div class="fl-orig ko" style="margin-left:33px">'
                    f'{esc(w["name_local"]) if w["name_local"] != w["name_en"] else ""}</div>', unsafe_allow_html=True)
        if h:
            c2.markdown(f'<div class="wl-price">{prices.sparkline_svg(h)}<span class="mv flat">{h[-1][1]:,.2f}</span>'
                        f'{prices.move_html(prices.last_move(h))}</div>', unsafe_allow_html=True)
        current = "off" if not w["notify"] else levels.get(pair, "all")
        chosen = c3.selectbox("Alerts", list(LEVELS), index=list(LEVELS.values()).index(current),
                              key=f"lv-{w['market']}-{w['ticker']}", label_visibility="collapsed")
        if LEVELS[chosen] != current:
            watch.set_notify(user["id"], w["market"], w["ticker"], LEVELS[chosen] != "off")
            if LEVELS[chosen] != "off":
                personal.set_level(user["id"], w["market"], w["ticker"], LEVELS[chosen])
            st.toast(f"Alerts for {w['name_en']}: {chosen}")
        if c4.button("Open", key=f"o-{w['market']}-{w['ticker']}", width="stretch"):
            nav.open_company(w["market"], w["ticker"])
        if c5.button("Remove", key=f"r-{w['market']}-{w['ticker']}", width="stretch"):
            watch.remove(user["id"], w["market"], w["ticker"])
            st.rerun()
