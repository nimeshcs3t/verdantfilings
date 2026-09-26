import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from core.auth import create_user, update_user
from core.db import companies, filings, get_engine, messages, users, watchlist
from core.ui import page_header
from services.pipeline import run_once


def page() -> None:
    me = st.session_state["user"]
    if me["role"] != "admin":
        st.error("Admins only.")
        return
    page_header("Admin", "Members, plans and data.")

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
    st.caption("The background worker does this every 10 minutes. Run it now to check every watched company "
               "and send pending Telegram alerts.")
    if st.button("Sync all companies now"):
        with st.spinner("Syncing"):
            stats = run_once()
        st.success(f"Checked {stats['companies']} companies, found {stats['new']} new filings, "
                   f"sent {stats['alerts']} alerts. {stats['errors']} errors.")
