"""Shared watchlists: an owner creates a list and invites members by username; members add companies with a note."""
from __future__ import annotations

from sqlalchemy import delete, insert, or_, select

from core.db import get_engine, shared_list_items, shared_list_members, shared_lists, users, utcnow


def lists_for(user_id: int) -> list[dict]:
    with get_engine().connect() as conn:
        member_of = select(shared_list_members.c.list_id).where(shared_list_members.c.user_id == user_id)
        return [dict(r) for r in conn.execute(select(shared_lists).where(
            or_(shared_lists.c.owner_id == user_id, shared_lists.c.id.in_(member_of))).order_by(shared_lists.c.name)).mappings()]


def _can(user_id: int, list_id: int) -> bool:
    return any(l["id"] == list_id for l in lists_for(user_id))


def create(owner_id: int, name: str) -> str | None:
    name = " ".join((name or "").split())[:80]
    if len(name) < 2:
        return "Give the list a name."
    with get_engine().begin() as conn:
        conn.execute(insert(shared_lists).values(owner_id=owner_id, name=name, created_at=utcnow()))
    return None


def delete_list(owner_id: int, list_id: int) -> None:
    with get_engine().begin() as conn:
        if conn.execute(delete(shared_lists).where(shared_lists.c.id == list_id, shared_lists.c.owner_id == owner_id)).rowcount:
            conn.execute(delete(shared_list_members).where(shared_list_members.c.list_id == list_id))
            conn.execute(delete(shared_list_items).where(shared_list_items.c.list_id == list_id))


def members(list_id: int) -> list[dict]:
    with get_engine().connect() as conn:
        owner = conn.execute(select(users.c.id, users.c.username).select_from(
            shared_lists.join(users, users.c.id == shared_lists.c.owner_id)).where(shared_lists.c.id == list_id)).first()
        rows = conn.execute(select(users.c.id, users.c.username).select_from(
            shared_list_members.join(users, users.c.id == shared_list_members.c.user_id))
            .where(shared_list_members.c.list_id == list_id)).all()
    out = [{"id": owner[0], "username": owner[1], "owner": True}] if owner else []
    return out + [{"id": r[0], "username": r[1], "owner": False} for r in rows]


def invite(owner_id: int, list_id: int, username: str) -> str | None:
    with get_engine().connect() as conn:
        owned = conn.execute(select(shared_lists.c.id).where(shared_lists.c.id == list_id, shared_lists.c.owner_id == owner_id)).first()
        target = conn.execute(select(users.c.id).where(users.c.username == (username or "").strip(), users.c.is_active == True)).scalar()  # noqa: E712
    if not owned:
        return "Only the list's owner can invite."
    if not target:
        return "No member with that username."
    if target == owner_id or any(m["id"] == target for m in members(list_id)):
        return "Already in the list."
    with get_engine().begin() as conn:
        conn.execute(insert(shared_list_members).values(list_id=list_id, user_id=target))
    return None


def remove_member(user_id: int, list_id: int, member_id: int) -> None:
    """The owner can remove anyone; a member can remove themselves (leave)."""
    ms = members(list_id)
    if any(m["owner"] and m["id"] == user_id for m in ms) or user_id == member_id:
        with get_engine().begin() as conn:
            conn.execute(delete(shared_list_members).where(shared_list_members.c.list_id == list_id,
                                                           shared_list_members.c.user_id == member_id))


def items(list_id: int) -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(select(shared_list_items, users.c.username).select_from(
            shared_list_items.outerjoin(users, users.c.id == shared_list_items.c.added_by))
            .where(shared_list_items.c.list_id == list_id).order_by(shared_list_items.c.added_at.desc())).mappings()]


def add_item(user_id: int, list_id: int, market: str, ticker: str, note: str = "") -> str | None:
    if not _can(user_id, list_id):
        return "You're not in this list."
    if any(i["market"] == market and i["ticker"] == ticker for i in items(list_id)):
        return "Already in the list."
    with get_engine().begin() as conn:
        conn.execute(insert(shared_list_items).values(list_id=list_id, market=market, ticker=ticker, added_by=user_id,
                                                      note=(note or "")[:200], added_at=utcnow()))
    try:      # let the others know
        from . import deliver
        from .pipeline import get_company
        name = (get_company(market, ticker, resolve=False) or {}).get("name_en", ticker)
        who = next((m["username"] for m in members(list_id) if m["id"] == user_id), "A member")
        lname = next((l["name"] for l in lists_for(user_id) if l["id"] == list_id), "a shared list")
        for m in members(list_id):
            if m["id"] != user_id:
                deliver.send(m["id"], f"{name} added to {lname}",
                             f"<b>{who}</b> added <b>{name}</b> ({ticker}) to <b>{lname}</b>." + (f"\nNote: {note}" if note else ""))
    except Exception:
        pass
    return None


def remove_item(user_id: int, list_id: int, market: str, ticker: str) -> None:
    if _can(user_id, list_id):
        with get_engine().begin() as conn:
            conn.execute(delete(shared_list_items).where(shared_list_items.c.list_id == list_id,
                                                         shared_list_items.c.market == market, shared_list_items.c.ticker == ticker))
