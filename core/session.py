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
    start(get_user(user_id) or {"id": user_id}, keep=True)


def start(user: dict, keep: bool = False) -> str:
    """Finish signing in: session state, a session row (listed on the Account page), the remember-me token when asked
    for, a security log entry and a new-device alert."""
    from services.personal import create_session
    from .security import check_new_device, client, record
    login(user)
    ip, device = client()
    token = create_session(user["id"], remember=keep, device=device, ip=ip)
    st.session_state["cookie_token"] = token
    st.session_state["remembered"] = keep
    st.session_state["touched"] = time.time()
    if keep:
        st.session_state["set_cookie"] = token
    if user.get("username"):
        record("login", user["id"], user["username"], "kept signed in for 30 days" if keep else "", ip, device)
        check_new_device(user, ip, device)
    return token


def _valid(token) -> str | None:
    return token if isinstance(token, str) and 20 <= len(token) <= 100 and token.replace("-", "").replace("_", "").isalnum() else None


def _cookie_token() -> str | None:
    import os
    if os.environ.get("VF_IGNORE_COOKIES"):        # used in testing to mimic Streamlit Community Cloud
        return None
    try:
        return _valid(st.context.cookies.get(COOKIE))
    except Exception:
        return None


def _storage_token() -> str | None:
    """The token kept in this browser's local storage. Works where cookies don't reach the app (Community Cloud).
    Returns None on the first run while the browser answers; the app reruns as soon as it does."""
    try:
        from streamlit_js_eval import streamlit_js_eval
    except Exception:
        return None
    value = streamlit_js_eval(js_expressions=f"localStorage.getItem('{COOKIE}') || 'none'", key="vf-storage-read")
    return _valid(value)


def _storage_run(expression: str, key: str) -> None:
    try:
        from streamlit_js_eval import streamlit_js_eval
        streamlit_js_eval(js_expressions=expression, key=key)
    except Exception:
        pass


def logout() -> None:
    from services.personal import end_session
    end_session(st.session_state.get("cookie_token") or _cookie_token())
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state["clear_cookie"] = True


def apply_cookie_changes() -> None:
    """Write or clear the remember-me cookie in the browser (runs once after sign-in or sign-out)."""
    token = st.session_state.get("set_cookie")
    if token:
        st.html(f'<script>document.cookie="{COOKIE}={token}; Max-Age=2592000; Path=/; SameSite=Lax; Secure";</script>',
                unsafe_allow_javascript=True)
        _storage_run(f"localStorage.setItem('{COOKIE}', '{token}'); 'saved'", key=f"vf-storage-save-{token[:8]}")
        st.session_state["set_cookie_runs"] = st.session_state.get("set_cookie_runs", 0) + 1
        if st.session_state["set_cookie_runs"] >= 2:      # keep it on screen for two runs so the browser can save it
            st.session_state.pop("set_cookie", None)
    if st.session_state.get("clear_cookie"):
        st.html(f'<script>document.cookie="{COOKIE}=; Max-Age=0; Path=/; SameSite=Lax; Secure";</script>',
                unsafe_allow_javascript=True)
        _storage_run(f"localStorage.removeItem('{COOKIE}'); 'removed'", key="vf-storage-clear")
        st.session_state["cookie_cleared"] = True


def current_user() -> dict | None:
    uid = st.session_state.get("uid")
    if not uid and not st.session_state.get("cookie_cleared"):
        from services.personal import session_user
        token = _cookie_token() or _storage_token()
        restored = session_user(token)
        if restored:
            user = get_user(restored)
            if user and user["is_active"]:
                login(user)
                st.session_state["cookie_token"] = token
                st.session_state["remembered"] = True
                uid = user["id"]
    if not uid:
        return None
    token = st.session_state.get("cookie_token")
    if token:
        # The session can be ended from another device (Account page) or by a password change: check it each run.
        from services.personal import session_user, touch_session
        if session_user(token) != uid:
            logout()
            st.session_state["signed_out_elsewhere"] = True
            return None
        if time.time() - st.session_state.get("touched", 0) > 300:
            from .security import client
            touch_session(token, client()[0])
            st.session_state["touched"] = time.time()
    elif time.time() - st.session_state.get("seen", 0) > IDLE_SECONDS:
        logout()
        return None
    user = get_user(uid)
    if not user or not user["is_active"]:
        logout()
        return None
    st.session_state["seen"] = time.time()
    return user
