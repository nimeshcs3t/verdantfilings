import secrets

import streamlit as st

from core.auth import change_password, update_user
from core.session import logout
from core.ui import esc, html_block, page_header
from services import personal, telegram
from sources import configured_sources

TIMEZONES = ["UTC", "Asia/Seoul", "Asia/Tokyo", "Asia/Kolkata", "Asia/Singapore", "Asia/Hong_Kong", "Asia/Dubai",
             "Asia/Jerusalem", "Australia/Sydney", "Europe/London", "Europe/Warsaw", "Europe/Paris",
             "America/New_York", "America/Chicago", "America/Los_Angeles"]


def page() -> None:
    user = st.session_state["user"]
    page_header("Account", f"Signed in as {user['username']}. Plan: {user['plan'].capitalize()}.")

    st.subheader("Telegram alerts", divider=False)
    if not telegram.enabled():
        st.caption("Telegram alerts aren't set up on this site yet.")
    elif user.get("telegram_chat_id"):
        st.write(f"Alerts go to your Telegram chat ending in {str(user['telegram_chat_id'])[-4:]}.")
        c1, c2, _ = st.columns([1, 1, 2])
        if c1.button("Send a test alert", width="stretch"):
            ok = telegram.send_message(user["telegram_chat_id"], "Test alert: your filings alerts are connected.")
            (st.success if ok else st.error)("Test alert sent." if ok else "Telegram didn't accept the message.")
        if c2.button("Disconnect Telegram", width="stretch"):
            update_user(user["id"], telegram_chat_id=None, tg_link_code=None)
            st.rerun()
    else:
        code = user.get("tg_link_code")
        if not code:
            code = secrets.token_urlsafe(12)
            update_user(user["id"], tg_link_code=code)
        bot = telegram.bot_username()
        if not bot:
            st.error("The Telegram bot couldn't be reached. Check TELEGRAM_BOT_TOKEN.")
        else:
            st.write("1. Open the bot and press Start.  \n2. Come back here and confirm.")
            c1, c2, _ = st.columns([1, 1, 2])
            c1.link_button("Open the bot", f"https://t.me/{bot}?start={code}", width="stretch")
            if c2.button("I pressed Start", type="primary", width="stretch"):
                chat_id = telegram.find_chat_for_code(code)
                if chat_id:
                    update_user(user["id"], telegram_chat_id=chat_id, tg_link_code=None)
                    telegram.send_message(chat_id, "Connected. You'll get an alert here when a company on your watchlist files.")
                    st.rerun()
                else:
                    st.warning("No Start message found yet. Press Start in the bot chat, then try again.")

    alert_settings(user)
    keyword_settings(user)

    st.subheader("Appearance", divider=False)
    st.caption("Dark mode follows your phone or computer setting. To choose yourself, open the ⋮ menu at the top "
               "right of the app, then Settings, then pick Light or Dark.")

    st.subheader("Change password", divider=False)
    with st.form("pw", clear_on_submit=True):
        old = st.text_input("Current password", type="password", autocomplete="current-password")
        new = st.text_input("New password", type="password", autocomplete="new-password")
        confirm = st.text_input("Confirm new password", type="password", autocomplete="new-password")
        if st.form_submit_button("Change password"):
            if new != confirm:
                st.error("The new passwords don't match.")
            else:
                err = change_password(user["id"], old, new)
                if err:
                    st.error(err)
                else:
                    personal.end_all_sessions(user["id"])
                    st.success("Password changed. Other devices have been signed out.")

    st.divider()
    c1, c2, _ = st.columns([1, 1.4, 2])
    if c1.button("Sign out", width="stretch"):
        logout()
        st.rerun()
    if c2.button("Sign out on all devices", width="stretch"):
        personal.end_all_sessions(user["id"])
        logout()
        st.rerun()


def alert_settings(user: dict) -> None:
    st.subheader("Alerts", divider=False)
    prefs = personal.get_prefs(user["id"])
    with st.form("alert-prefs", border=False):
        mode = st.radio("How to receive filing alerts", ["Instant", "Daily digest"], horizontal=True,
                        index=0 if prefs["alert_mode"] == "instant" else 1,
                        help="Instant sends each filing as it arrives. Daily digest sends one message a day.")
        c1, c2 = st.columns(2)
        hour = c1.selectbox("Digest time", list(range(24)), index=int(prefs["digest_hour"]),
                            format_func=lambda h: f"{h:02d}:00")
        tz = c2.selectbox("Your timezone", TIMEZONES,
                          index=TIMEZONES.index(prefs["tz"]) if prefs["tz"] in TIMEZONES else 0)
        skip = st.checkbox("Skip insider-trade alerts (Form 4, director dealings)", value=bool(prefs["skip_insider"]))
        weekly = st.checkbox("Send a weekly report on Monday at the digest time", value=bool(prefs["weekly"]))
        if st.form_submit_button("Save alert settings", type="primary"):
            personal.save_prefs(user["id"], alert_mode="instant" if mode == "Instant" else "digest",
                                digest_hour=hour, tz=tz, skip_insider=skip, weekly=weekly)
            st.success("Saved.")
    st.caption("Per-company choices (All filings, Major only, Off) are on the Watchlist page.")


def keyword_settings(user: dict) -> None:
    st.subheader("Keyword alerts", divider=False)
    st.caption("Get a Telegram message when any filing title contains a word, even from companies you don't follow "
               "(Korea, USA, Poland and Japan are checked market-wide). Write keywords in English; they're matched "
               "in each market's language too.")
    markets = {"All countries": "*", **{s.country: s.market for s in configured_sources()}}
    with st.form("add-keyword", clear_on_submit=True, border=False):
        c1, c2, c3 = st.columns([2, 1.2, 0.8], vertical_alignment="bottom")
        word = c1.text_input("Keyword", placeholder="e.g. buyback, acquisition, rights offering")
        where = c2.selectbox("Country", list(markets))
        if c3.form_submit_button("Add", width="stretch"):
            err = personal.add_keyword(user["id"], word, markets[where])
            st.warning(err) if err else st.rerun()
    names = {v: k for k, v in markets.items()}
    for kw in personal.list_keywords(user["id"]):
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        c1.markdown(f'<span class="chip c-buyback" style="margin-left:0">{esc(kw["keyword"])}</span>'
                    f'<span class="fl-tk">{esc(names.get(kw["market"], kw["market"]))}</span>', unsafe_allow_html=True)
        if c2.button("Remove", key=f"kw-{kw['id']}", type="tertiary"):
            personal.remove_keyword(user["id"], kw["id"])
            st.rerun()
