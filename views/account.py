import secrets

import streamlit as st

from core.auth import change_password, update_user
from core.session import logout
from core.ui import page_header
from services import telegram


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
                st.error(err) if err else st.success("Password changed.")

    st.divider()
    if st.button("Sign out"):
        logout()
        st.rerun()
