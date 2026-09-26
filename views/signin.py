import streamlit as st

from core.auth import authenticate, create_user, invite_ok, signup_mode
from core.config import app_name
from core.session import login
from core.ui import esc, html_block


def page() -> None:
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        html_block(f'<div class="brand">{esc(app_name())}</div>'
                   '<p class="page-sub">Company filings from regulators, in English, the day they are published.</p>')
        mode = signup_mode()
        tabs = st.tabs(["Sign in", "Create account"] if mode != "closed" else ["Sign in"])

        with tabs[0]:
            with st.form("signin"):
                username = st.text_input("Username", autocomplete="username")
                password = st.text_input("Password", type="password", autocomplete="current-password")
                if st.form_submit_button("Sign in", type="primary", width="stretch"):
                    user, err = authenticate(username, password)
                    if err:
                        st.error(err)
                    else:
                        login(user)
                        st.rerun()

        if mode != "closed":
            with tabs[1]:
                with st.form("signup"):
                    username = st.text_input("Username", help="3 to 24 letters, numbers or underscores")
                    email = st.text_input("Email (optional)", help="Only used for account recovery and billing later.")
                    password = st.text_input("Password", type="password", autocomplete="new-password",
                                             help="At least 10 characters, mixing letters with numbers or symbols.")
                    confirm = st.text_input("Confirm password", type="password", autocomplete="new-password")
                    code = st.text_input("Invite code") if mode == "invite" else ""
                    if st.form_submit_button("Create account", type="primary", width="stretch"):
                        if mode == "invite" and not invite_ok(code):
                            st.error("That invite code isn't valid.")
                        elif password != confirm:
                            st.error("The passwords don't match.")
                        else:
                            user, err = create_user(username, password, email or None)
                            if err:
                                st.error(err)
                            else:
                                login(user)
                                st.rerun()
