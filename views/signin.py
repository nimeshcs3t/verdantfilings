import time

import streamlit as st

from core.auth import (authenticate, complete_reset, create_user, get_user, invite_ok, needs_second_factor, request_reset,
                       reset_user, signup_mode, verify_second_factor)
from core.config import app_name
from core.session import start
from core.ui import esc, html_block

PENDING_SECONDS = 300


def _brand(sub: str = "Company filings from regulators, in English, the day they are published.") -> None:
    html_block(f'<div class="brand">{esc(app_name())}</div><p class="page-sub">{esc(sub)}</p>')


def _second_factor(pending: dict) -> None:
    _brand("Two-factor sign-in")
    st.write("Open your authenticator app and enter the 6-digit code for Verdant Filings. "
             "Lost your phone? Enter one of your backup codes instead.")
    with st.form("signin-2fa"):
        code = st.text_input("Code", max_chars=12, autocomplete="one-time-code", placeholder="123456")
        if st.form_submit_button("Verify", type="primary", width="stretch"):
            ok, err = verify_second_factor(pending["uid"], code)
            if ok:
                user = get_user(pending["uid"])
                st.session_state.pop("pending_2fa", None)
                start(user, keep=pending["keep"])
                st.rerun()
            st.error(err)
    if st.button("Cancel", type="tertiary"):
        st.session_state.pop("pending_2fa", None)
        st.rerun()


def page() -> None:
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        pending = st.session_state.get("pending_2fa")
        if pending and time.time() - pending["at"] < PENDING_SECONDS:
            _second_factor(pending)
            return
        st.session_state.pop("pending_2fa", None)
        _brand()
        if st.session_state.pop("signed_out_elsewhere", False):
            st.info("You were signed out because this session was ended from another device or the password changed.")
        mode = signup_mode()
        tabs = st.tabs(["Sign in", "Create account"] if mode != "closed" else ["Sign in"])

        with tabs[0]:
            with st.form("signin"):
                username = st.text_input("Username", autocomplete="username")
                password = st.text_input("Password", type="password", autocomplete="current-password")
                keep = st.checkbox("Keep me signed in on this device for 30 days")
                if st.form_submit_button("Sign in", type="primary", width="stretch"):
                    user, err = authenticate(username, password)
                    if err:
                        st.error(err)
                    elif needs_second_factor(user):
                        st.session_state["pending_2fa"] = {"uid": user["id"], "keep": keep, "at": time.time()}
                        st.rerun()
                    else:
                        start(user, keep=keep)
                        st.rerun()
            with st.expander("Forgot your password?"):
                with st.form("forgot", border=False, clear_on_submit=True):
                    ident = st.text_input("Username or email")
                    if st.form_submit_button("Email me a reset link"):
                        request_reset(ident)
                        st.success("If that matches an account with an email address, a reset link is on its way. "
                                   "It works once, for 30 minutes. Check your spam folder too.")
                st.caption("No email on your account? Ask the admin to reset your password.")

        if mode != "closed":
            with tabs[1]:
                with st.form("signup"):
                    username = st.text_input("Username", help="3 to 24 letters, numbers or underscores")
                    email = st.text_input("Email (recommended)", help="Used for password resets and security alerts.")
                    password = st.text_input("Password", type="password", autocomplete="new-password",
                                             help="At least 10 characters, mixing letters with numbers or symbols. "
                                                  "Passwords found in known data breaches are refused.")
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
                                start(user)
                                st.rerun()


def reset_page(token: str) -> None:
    """Opened from the emailed link (?reset=...)."""
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        _brand("Choose a new password")
        if st.session_state.get("reset-done"):
            st.success("Password changed, and every device was signed out. Sign in with your new password.")
            if st.button("Go to sign in", type="primary"):
                st.session_state.pop("reset-done", None)
                st.query_params.clear()
                st.rerun()
            return
        user_id = reset_user(token)
        if not user_id:
            st.error("This reset link has expired or was already used. Ask for a new one from the sign-in page.")
            if st.button("Go to sign in"):
                st.query_params.clear()
                st.rerun()
            return
        user = get_user(user_id)
        st.write(f"Account: **{esc(user['username'])}**")
        with st.form("reset"):
            new = st.text_input("New password", type="password", autocomplete="new-password",
                                help="At least 10 characters, mixing letters with numbers or symbols.")
            confirm = st.text_input("Confirm new password", type="password", autocomplete="new-password")
            if st.form_submit_button("Save new password", type="primary", width="stretch"):
                if new != confirm:
                    st.error("The passwords don't match.")
                else:
                    err = complete_reset(token, new)
                    if err:
                        st.error(err)
                    else:
                        st.session_state["reset-done"] = True
                        st.rerun()
