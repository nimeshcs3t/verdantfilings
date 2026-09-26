"""Login state for a Streamlit session."""
import time

import streamlit as st

from .auth import get_user

IDLE_SECONDS = 12 * 3600


def login(user: dict) -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state["uid"] = user["id"]
    st.session_state["seen"] = time.time()


def logout() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def current_user() -> dict | None:
    uid = st.session_state.get("uid")
    if not uid:
        return None
    if time.time() - st.session_state.get("seen", 0) > IDLE_SECONDS:
        logout()
        return None
    user = get_user(uid)
    if not user or not user["is_active"]:
        logout()
        return None
    st.session_state["seen"] = time.time()
    return user
