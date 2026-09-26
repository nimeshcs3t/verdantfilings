import streamlit as st

from core import nav
from core.plans import watchlist_limit
from core.ui import esc, html_block, page_header
from services import telegram, watch

from .components import add_company_form


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
    for w in wl:
        c1, c2, c3, c4 = st.columns([3.2, 1.2, 0.9, 0.9], vertical_alignment="center")
        c1.markdown(f'<div class="fl-co">{esc(w["name_en"])}<span class="fl-tk">{esc(w["ticker"])}</span></div>'
                    f'<div class="fl-orig ko">{esc(w["name_local"])}</div>', unsafe_allow_html=True)
        on = c2.toggle("Alerts", value=bool(w["notify"]), key=f"n-{w['market']}-{w['ticker']}")
        if on != bool(w["notify"]):
            watch.set_notify(user["id"], w["market"], w["ticker"], on)
        if c3.button("Open", key=f"o-{w['market']}-{w['ticker']}", width="stretch"):
            nav.open_company(w["market"], w["ticker"])
        if c4.button("Remove", key=f"r-{w['market']}-{w['ticker']}", width="stretch"):
            watch.remove(user["id"], w["market"], w["ticker"])
            st.rerun()
