from datetime import timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from core.auth import create_user, update_user
from core.db import companies, filings, get_engine, messages, users, watchlist
from core.db import as_utc, utcnow
from core.ui import esc, html_block, page_header, relative_time
from core.config import direct_fetch
from services import github
from services.pipeline import run_once


def page() -> None:
    me = st.session_state["user"]
    if me["role"] != "admin":
        st.error("Admins only.")
        return
    page_header("Admin", "Health, members, plans and data.")
    health_panel()

    with get_engine().connect() as conn:
        counts = {name: conn.execute(select(func.count()).select_from(t)).scalar_one()
                  for name, t in [("Members", users), ("Companies tracked", companies),
                                  ("Filings stored", filings), ("Messages", messages)]}
        rows = conn.execute(select(users.c.id, users.c.username, users.c.email, users.c.role, users.c.plan,
                                   users.c.is_active, users.c.telegram_chat_id, users.c.created_at)
                            .order_by(users.c.id)).mappings().all()
        wl_counts = dict(conn.execute(select(watchlist.c.user_id, func.count()).group_by(watchlist.c.user_id)).all())
    for col, (label, value) in zip(st.columns(4), counts.items()):
        col.metric(label, f"{value:,}")

    st.subheader("Members", divider=False)
    df = pd.DataFrame([{**dict(r), "telegram": bool(r["telegram_chat_id"]),
                        "companies": wl_counts.get(r["id"], 0)} for r in rows]).drop(columns=["telegram_chat_id"])
    edited = st.data_editor(
        df, hide_index=True, width="stretch", key="users-editor",
        disabled=["id", "username", "email", "created_at", "telegram", "companies"],
        column_config={"role": st.column_config.SelectboxColumn(options=["user", "admin"], required=True),
                       "plan": st.column_config.SelectboxColumn(options=["free", "pro"], required=True),
                       "is_active": st.column_config.CheckboxColumn("active")})
    if st.button("Save member changes", type="primary"):
        changed = 0
        for before, after in zip(df.to_dict("records"), edited.to_dict("records")):
            diff = {k: after[k] for k in ("role", "plan", "is_active") if before[k] != after[k]}
            if diff:
                if after["id"] == me["id"] and (diff.get("role") == "user" or diff.get("is_active") is False):
                    st.warning("You can't remove your own admin access.")
                    continue
                update_user(int(after["id"]), **{k: (bool(v) if k == "is_active" else v) for k, v in diff.items()})
                changed += 1
        st.success(f"Saved {changed} change{'s' if changed != 1 else ''}.")

    st.subheader("Add a member", divider=False)
    with st.form("add-user", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        username = c1.text_input("Username")
        password = c2.text_input("Temporary password", type="password")
        plan = c3.selectbox("Plan", ["free", "pro"])
        if st.form_submit_button("Add member"):
            _, err = create_user(username, password, plan=plan)
            st.error(err) if err else st.success(f"Added {username.lower()}.")

    st.subheader("Sync", divider=False)
    st.caption("The GitHub job checks every company every 10 minutes during Korean market hours and hourly "
               "otherwise, writes overviews and sends Telegram alerts.")
    if direct_fetch():
        if st.button("Sync all companies now"):
            with st.spinner("Syncing"):
                stats = run_once()
            st.success(f"Checked {stats['companies']} companies, found {stats['new']} new filings, "
                       f"sent {stats['alerts']} alerts. {stats['errors']} errors.")
    elif github.configured():
        if st.button("Run the sync job now"):
            ok = github.trigger_sync()
            (st.success if ok else st.error)("Started. Results appear in a minute or two." if ok else
                                             "GitHub didn't accept the request. Check GH_TOKEN and GH_REPO.")
    else:
        st.caption("To run it now, open your GitHub repo, then Actions, Poll filings, Run workflow. "
                   "Add GH_TOKEN and GH_REPO to the app secrets to get a button here instead.")


USAGE_NAMES = {"gemini": "Gemini requests", "gem-limit": "Gemini limit hits", "mymemory": "MyMemory translations",
               "google": "Google translations", "g-refuse": "Google refusals"}


def health_panel() -> None:
    from services import housekeeping
    h = housekeeping.health()
    runs = h["runs"]
    st.subheader("Health", divider=False)
    if not runs:
        st.caption("No background runs recorded yet. They appear after the next GitHub job run.")
    else:
        last = runs[0]
        day = [r for r in runs if utcnow() - as_utc(r["started_at"]) < timedelta(hours=24)]
        avg = sum(r["seconds"] or 0 for r in day) / len(day) if day else 0
        with_warnings = sum(1 for r in day if r["warnings"])
        stats = [("Last run", relative_time(last["started_at"])), ("Runs in 24 h", len(day)),
                 ("Average run", f"{avg:.0f} s"), ("Runs with warnings", with_warnings),
                 ("New filings, last run", last["stats"].get("new", 0)), ("Alerts, last run", last["stats"].get("alerts", 0))]
        html_block('<div class="stat-grid">' + "".join(f'<div class="stat"><div class="k">{k}</div><div class="v">{esc(v)}</div></div>'
                                                       for k, v in stats) + "</div>")
        late = utcnow() - as_utc(last["started_at"]) > timedelta(hours=2)
        if late:
            st.warning("No background run in the last 2 hours. Check GitHub, then Actions, then Poll filings.")
        warn_runs = [r for r in runs if r["warnings"]][:5]
        if warn_runs:
            with st.expander(f"Recent warnings ({len(warn_runs)} runs)"):
                for r in warn_runs:
                    st.caption(f"{relative_time(r['started_at'])}")
                    st.code(r["warnings"][:1500], language=None)
    usage = h["usage"]
    shown = {USAGE_NAMES.get(k, k): v for k, v in usage.items() if k in USAGE_NAMES}
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Today's usage (UTC)")
        if shown:
            html_block('<table class="tbl">' + "".join(f"<tr><td>{esc(k)}</td><td class='num'>{v:,}</td></tr>"
                                                       for k, v in shown.items()) + "</table>")
        else:
            st.caption("No translator or Gemini use recorded today.")
    with c2:
        st.caption("Stored rows")
        sizes = {k: v for k, v in h["sizes"].items() if v}
        top = sorted(sizes.items(), key=lambda kv: -kv[1])[:8]
        html_block('<table class="tbl">' + "".join(f"<tr><td>{esc(k)}</td><td class='num'>{v:,}</td></tr>" for k, v in top) + "</table>")
    c1, c2 = st.columns([3, 1], vertical_alignment="center")
    c1.caption(f"Automatic clean-up runs daily (last: {h['last_cleanup'] or 'not yet'}). It removes filings older than "
               "2 years (except starred), trims stored text after 180 days, and clears expired sign-ins and old logs.")
    if c2.button("Clean up now", width="stretch"):
        removed = housekeeping.cleanup(force=True)
        st.success("Removed: " + (", ".join(f"{k} {v}" for k, v in removed.items()) if removed else "nothing to remove"))
