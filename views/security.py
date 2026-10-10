"""Account security: two-factor sign-in, signed-in devices, recent activity, password, closing the account."""
import streamlit as st

from core import totp
from core.auth import (backup_codes_left, change_password, disable_second_factor, enable_second_factor, get_user,
                       new_backup_codes)
from core.db import as_utc
from core.security import LABELS, events
from core.session import logout
from core.ui import esc, html_block, page_header, relative_time
from services import personal


def _show_codes(codes: list[str]) -> None:
    st.warning("Save these backup codes somewhere safe (a password manager or printed). Each works once if you lose "
               "your phone. They won't be shown again.")
    st.code("\n".join(codes), language=None)
    st.download_button("Download codes (.txt)", "Verdant Filings backup codes\n\n" + "\n".join(codes) + "\n",
                       file_name="verdant-backup-codes.txt", mime="text/plain")


def two_factor(user: dict, forced: bool = False) -> None:
    st.subheader("Two-factor sign-in", divider=False)
    fresh = st.session_state.get("2fa-new-codes")
    if fresh:
        st.success("Two-factor sign-in is on.")
        _show_codes(fresh)
        if st.button("I've saved my codes", type="primary"):
            st.session_state.pop("2fa-new-codes", None)
            st.rerun()
        return
    if user.get("totp_secret"):
        left = backup_codes_left(user)
        st.write(f"**On.** Signing in asks for a code from your authenticator app. Backup codes left: {left}.")
        if left <= 2:
            st.warning("You're running low on backup codes. Make new ones.")
        c1, c2 = st.columns(2)
        with c1.popover("Make new backup codes", width="stretch"):
            st.caption("Your old backup codes stop working.")
            if st.button("Make new codes", key="2fa-newcodes", type="primary"):
                st.session_state["2fa-new-codes"] = new_backup_codes(user["id"])
                st.rerun()
        with c2.popover("Turn off", width="stretch"):
            if user["role"] == "admin" and forced is False:
                from core.auth import admin_requires_2fa
                if admin_requires_2fa():
                    st.caption("Admin accounts must keep two-factor sign-in on.")
                    return
            with st.form("2fa-off", border=False):
                pw = st.text_input("Your password", type="password", autocomplete="current-password")
                if st.form_submit_button("Turn off two-factor sign-in"):
                    err = disable_second_factor(user["id"], pw)
                    st.error(err) if err else st.rerun()
        return

    st.write("Add a second step to signing in: a 6-digit code from an authenticator app on your phone (Google "
             "Authenticator, Microsoft Authenticator, 1Password, Authy and others). A stolen password alone then isn't enough.")
    if "2fa-secret" not in st.session_state:
        if not st.button("Set up two-factor sign-in", type="primary", icon=":material/shield_lock:"):
            return
        st.session_state["2fa-secret"] = totp.new_secret()
        st.rerun()
    secret = st.session_state["2fa-secret"]
    link = totp.uri(secret, user["username"])
    c1, c2 = st.columns([1, 1.6])
    with c1:
        svg = totp.qr_svg(link)
        if svg:
            html_block(f'<div class="qr">{svg}</div>')
    with c2:
        st.markdown("1. In your authenticator app, add an account and **scan this QR code**.  \n"
                    "   Can't scan? Choose \"enter a setup key\" and type:")
        st.code(" ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), language=None)
        st.markdown("2. Enter the 6-digit code the app shows:")
        with st.form("2fa-on", border=False):
            code = st.text_input("Code", max_chars=8, autocomplete="one-time-code", placeholder="123456",
                                 label_visibility="collapsed")
            if st.form_submit_button("Turn on", type="primary"):
                codes, err = enable_second_factor(user["id"], secret, code)
                if err:
                    st.error(err)
                else:
                    st.session_state.pop("2fa-secret", None)
                    st.session_state["2fa-new-codes"] = codes
                    st.rerun()
    if st.button("Cancel set-up", type="tertiary"):
        st.session_state.pop("2fa-secret", None)
        st.rerun()


def devices(user: dict) -> None:
    st.subheader("Where you're signed in", divider=False)
    current = st.session_state.get("cookie_token")
    rows = personal.list_sessions(user["id"], current)
    if not rows:
        st.caption("No sessions recorded yet. They appear from your next sign-in.")
    for r in rows:
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        seen = r.get("last_seen") or r.get("created_at")
        bits = [esc(r.get("device") or "Unknown device")]
        if r.get("ip"):
            bits.append(esc(r["ip"]))
        bits.append(f"active {relative_time(seen)}" if seen else "")
        bits.append("kept signed in" if r.get("remember") else "")
        c1.markdown(("**This device** · " if r["current"] else "") + " · ".join(b for b in bits if b))
        if not r["current"] and c2.button("Sign out", key=f"sess-{r['id']}", width="stretch"):
            personal.end_session_by_id(user["id"], r["id"])
            from core.security import record
            record("session_ended", user["id"], user["username"], r.get("device") or "")
            st.rerun()
    c1, c2, _ = st.columns([1, 1.4, 2])
    if c1.button("Sign out", width="stretch", key="sec-signout"):
        logout()
        st.rerun()
    if c2.button("Sign out on all other devices", width="stretch"):
        n = personal.end_all_sessions(user["id"], keep=current)
        from core.security import record
        record("sessions_ended", user["id"], user["username"], f"{n} other sessions")
        st.success(f"Signed out {n} other session{'s' if n != 1 else ''}.")


def activity(user: dict) -> None:
    with st.expander("Recent security activity"):
        rows = events(user["id"], limit=25)
        if not rows:
            st.caption("Nothing yet.")
            return
        html_block('<div class="tbl-wrap"><table class="tbl"><tr><th>When</th><th>What</th><th>Device</th><th>IP</th></tr>' + "".join(
            f'<tr><td>{as_utc(r["ts"]):%d %b %H:%M} UTC</td><td>{esc(LABELS.get(r["kind"], r["kind"]))}'
            f'{(" · " + esc(r["detail"])) if r.get("detail") and r["kind"] not in ("login", "new_device") else ""}</td>'
            f'<td>{esc(r.get("device") or "")}</td><td>{esc(r.get("ip") or "")}</td></tr>' for r in rows) + "</table></div>")
        st.caption("Something you don't recognise? Change your password, then sign out on all other devices.")


def password(user: dict) -> None:
    st.subheader("Change password", divider=False)
    with st.form("pw", clear_on_submit=True):
        old = st.text_input("Current password", type="password", autocomplete="current-password")
        new = st.text_input("New password", type="password", autocomplete="new-password")
        confirm = st.text_input("Confirm new password", type="password", autocomplete="new-password")
        if st.form_submit_button("Change password"):
            if new != confirm:
                st.error("The new passwords don't match.")
            else:
                err = change_password(user["id"], old, new, keep_session=st.session_state.get("cookie_token"))
                st.error(err) if err else st.success("Password changed. Other devices have been signed out.")


def close_account(user: dict) -> None:
    with st.expander("Delete my account"):
        st.write("Removes your account and everything you've stored: watchlist, portfolio, journal, notes, alerts, "
                 "settings and shared lists you own. This can't be undone. Download your data first (Your data, above).")
        with st.form("delete-account", border=False):
            pw = st.text_input("Your password", type="password", autocomplete="current-password")
            confirm = st.text_input("Type DELETE to confirm")
            if st.form_submit_button("Delete my account permanently"):
                from services.accounts import delete_account
                err = delete_account(user["id"], pw, confirm)
                if err:
                    st.error(err)
                else:
                    logout()
                    st.rerun()


def section(user: dict) -> None:
    two_factor(user)
    devices(user)
    activity(user)
    password(user)
    close_account(user)


def required_page() -> None:
    """Shown instead of the app to an admin without two-factor sign-in, until it's on."""
    user = get_user(st.session_state["user"]["id"])
    _, mid, _ = st.columns([0.4, 2, 0.4])
    with mid:
        page_header("Protect your admin account", "Admin accounts need two-factor sign-in. It takes about a minute.")
        two_factor(user, forced=True)
        st.divider()
        if st.button("Sign out"):
            logout()
            st.rerun()
