"""Closing an account: removes everything the member stored."""
from __future__ import annotations

from sqlalchemy import delete, select, update

from core.db import get_engine, login_attempts, metadata, shared_list_items, shared_list_members, shared_lists, users


def delete_account(user_id: int, password: str, confirm: str) -> str | None:
    from core.auth import get_user, verify_password
    from core.security import record
    user = get_user(user_id)
    if not user:
        return "No such account."
    if confirm.strip().upper() != "DELETE":
        return 'Type DELETE in capitals to confirm.'
    if not verify_password(password or "", user["pw_hash"]):
        return "Your password is incorrect."
    if user["role"] == "admin":
        with get_engine().connect() as conn:
            admins = conn.execute(select(users.c.id).where(users.c.role == "admin", users.c.is_active == True)).all()  # noqa: E712
        if len(admins) <= 1:
            return "You're the only admin. Make another member admin first, or keep this account."
    with get_engine().begin() as conn:
        owned = conn.execute(select(shared_lists.c.id).where(shared_lists.c.owner_id == user_id)).scalars().all()
        if owned:
            conn.execute(delete(shared_list_items).where(shared_list_items.c.list_id.in_(owned)))
            conn.execute(delete(shared_list_members).where(shared_list_members.c.list_id.in_(owned)))
            conn.execute(delete(shared_lists).where(shared_lists.c.id.in_(owned)))
        conn.execute(update(shared_list_items).where(shared_list_items.c.added_by == user_id).values(added_by=None))
        for table in metadata.sorted_tables:
            if table.name != "users" and "user_id" in table.c:
                conn.execute(delete(table).where(table.c.user_id == user_id))
        conn.execute(delete(login_attempts).where(login_attempts.c.username.in_([user["username"], f"2fa:{user_id}"])))
        conn.execute(delete(users).where(users.c.id == user_id))
    record("account_deleted", None, user["username"])
    return None
