"""Login state for a Streamlit session, with an optional 30-day "remember me" cookie."""
import time

import streamlit as st

from .auth import get_user

IDLE_SECONDS = 12 * 3600
COOKIE = "vf_s"


def login(user: dict) -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state["uid"] = user["id"]
    st.session_state["seen"] = time.time()


def remember(user_id: int) -> None:
    """Keep this browser signed in for 30 days (call right after login)."""
    from services.personal import create_session
    token = create_session(user_id)
    st.session_state["cookie_token"] = token
    st.session_state["set_cookie"] = token


def _cookie_token() -> str | None:
    try:
        token = st.context.cookies.get(COOKIE)
    except Exception:
        return None
    return token if isinstance(token, str) and 20 <= len(token) <= 100 else None


def logout() -> None:
    from services.personal import end_session
    end_session(st.session_state.get("cookie_token") or _cookie_token())
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state["clear_cookie"] = True


def apply_cookie_changes() -> None:
    """Write or clear the remember-me cookie in the browser (runs once after sign-in or sign-out)."""
    token = st.session_state.pop("set_cookie", None)
    if token:
        st.html(f'<script>document.cookie="{COOKIE}={token}; Max-Age=2592000; Path=/; SameSite=Lax; Secure";</script>',
                unsafe_allow_javascript=True)
    if st.session_state.pop("clear_cookie", None):
        st.html(f'<script>document.cookie="{COOKIE}=; Max-Age=0; Path=/; SameSite=Lax; Secure";</script>',
                unsafe_allow_javascript=True)
        st.session_state["cookie_cleared"] = True


def current_user() -> dict | None:
    uid = st.session_state.get("uid")
    if not uid and not st.session_state.get("cookie_cleared"):
        from services.personal import session_user
        token = _cookie_token()
        restored = session_user(token)
        if restored:
            user = get_user(restored)
            if user and user["is_active"]:
                login(user)
                st.session_state["cookie_token"] = token
                uid = user["id"]
    if not uid:
        return None
    if time.time() - st.session_state.get("seen", 0) > IDLE_SECONDS and not st.session_state.get("cookie_token"):
        logout()
        return None
    user = get_user(uid)
    if not user or not user["is_active"]:
        logout()
        return None
    st.session_state["seen"] = time.time()
    return user
