import streamlit as st

from core import nav
from core.plans import watchlist_limit
from core.ui import esc, html_block, logo_html, page_header
from services import personal, prices, shared, telegram, watch
from sources import get_source

from .components import add_company_form, histories

LEVELS = {"All filings": "all", "Major only": "major", "Off": "off"}


def my_list() -> None:
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



def page() -> None:
    user = st.session_state["user"]
    lists = shared.lists_for(user["id"])
    mine, together = st.tabs(["My watchlist", f"Shared lists ({len(lists)})"])
    with mine:
        my_list()
    with together:
        shared_lists_tab(user, lists)


def shared_lists_tab(user: dict, lists: list[dict]) -> None:
    from datetime import timedelta
    from services.pipeline import filings_for, get_company
    st.caption("Lists you share with other members: anyone in a list can add companies with a note, and everyone gets a "
               "message when something is added. The owner invites members by username.")
    with st.form("sl-new", clear_on_submit=True, border=False):
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        name = c1.text_input("New shared list", placeholder="e.g. Korea small caps")
        if c2.form_submit_button("Create", width="stretch"):
            err = shared.create(user["id"], name)
            st.warning(err) if err else st.rerun()
    if not lists:
        return
    names = {f'{l["name"]}{" (yours)" if l["owner_id"] == user["id"] else ""}': l for l in lists}
    lst = names[st.selectbox("List", list(names), key="sl-pick")]
    owner = lst["owner_id"] == user["id"]
    ms = shared.members(lst["id"])
    st.caption("Members: " + ", ".join(m["username"] + (" (owner)" if m["owner"] else "") for m in ms))
    mywl = watch.get_watchlist(user["id"])
    with st.form(f"sl-add-{lst['id']}", clear_on_submit=True, border=True):
        c1, c2, c3 = st.columns([2, 2, 0.8], vertical_alignment="bottom")
        opts = {f'{w["name_en"]} ({w["ticker"]})': (w["market"], w["ticker"]) for w in mywl}
        pick = c1.selectbox("Add from your watchlist", list(opts) or ["(your watchlist is empty)"])
        note = c2.text_input("Note (optional)", placeholder="why it's interesting")
        if c3.form_submit_button("Add", width="stretch") and opts:
            err = shared.add_item(user["id"], lst["id"], *opts[pick], note)
            st.warning(err) if err else st.rerun()
    items = shared.items(lst["id"])
    if not items:
        st.caption("No companies yet.")
    watching = {(w["market"], w["ticker"]) for w in mywl}
    for it in items:
        comp = get_company(it["market"], it["ticker"], resolve=False) or {"name_en": it["ticker"]}
        src = get_source(it["market"])
        recent = len(filings_for([(it["market"], it["ticker"])], src.today() - timedelta(days=7))) if src else 0
        c1, c2, c3 = st.columns([5, 1.2, 0.8], vertical_alignment="center")
        c1.markdown(f'<div class="fl-co">{logo_html(comp["name_en"], it["ticker"], market=it["market"])}{esc(comp["name_en"])}'
                    f'<span class="fl-tk">{esc(it["ticker"])}</span><span class="fl-tk">{esc(src.country if src else "")}</span>'
                    f'<span class="fl-tk">{recent} filings this week</span></div>'
                    f'<div class="fl-orig">added by {esc(it.get("username") or "?")}{": " + esc(it["note"]) if it.get("note") else ""}</div>',
                    unsafe_allow_html=True)
        if (it["market"], it["ticker"]) not in watching:
            if c2.button("Add to mine", key=f"sl-take-{lst['id']}-{it['market']}-{it['ticker']}", width="stretch"):
                _, err = watch.add(user, it["market"], it["ticker"])
                st.warning(err) if err else st.rerun()
        else:
            c2.caption("On your watchlist")
        if c3.button("Remove", key=f"sl-rm-{lst['id']}-{it['market']}-{it['ticker']}", type="tertiary"):
            shared.remove_item(user["id"], lst["id"], it["market"], it["ticker"])
            st.rerun()
    st.divider()
    if owner:
        with st.form(f"sl-inv-{lst['id']}", clear_on_submit=True, border=False):
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            who = c1.text_input("Invite a member by username")
            if c2.form_submit_button("Invite", width="stretch"):
                err = shared.invite(user["id"], lst["id"], who)
                st.warning(err) if err else st.rerun()
        others = [m for m in ms if not m["owner"]]
        if others:
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            gone = c1.selectbox("Remove a member", [m["username"] for m in others], key=f"sl-kick-{lst['id']}")
            if c2.button("Remove member", key=f"sl-kick-b-{lst['id']}", width="stretch"):
                shared.remove_member(user["id"], lst["id"], next(m["id"] for m in others if m["username"] == gone))
                st.rerun()
        if st.button("Delete this list", key=f"sl-del-{lst['id']}", type="tertiary"):
            shared.delete_list(user["id"], lst["id"])
            st.rerun()
    elif st.button("Leave this list", key=f"sl-leave-{lst['id']}", type="tertiary"):
        shared.remove_member(user["id"], lst["id"], user["id"])
        st.rerun()
