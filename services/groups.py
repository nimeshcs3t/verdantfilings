"""A member's own sectors and structural themes, and which companies belong to them.

A company has at most one sector (choosing another replaces it) and any number of themes. A sector answers
"what does the company do"; a structural theme is a long-running trend it rides (AI infrastructure, ageing
population, electrification...), so one company can sit in several."""
from __future__ import annotations

from sqlalchemy import and_, delete, func, insert, select, update

from core.db import company_groups, get_engine, user_groups, utcnow

KINDS = {"sector": "Sector", "theme": "Structural theme"}
STANDARD_SECTORS = ["Communication services", "Consumer discretionary", "Consumer staples", "Energy", "Financials",
                    "Health care", "Industrials", "Information technology", "Materials", "Real estate", "Utilities"]
MAX_GROUPS = 80


def list_groups(user_id: int, kind: str | None = None) -> list[dict]:
    q = select(user_groups).where(user_groups.c.user_id == user_id)
    if kind:
        q = q.where(user_groups.c.kind == kind)
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(q.order_by(user_groups.c.kind, func.lower(user_groups.c.name))).mappings()]
        counts = dict(conn.execute(select(company_groups.c.group_id, func.count()).where(
            company_groups.c.user_id == user_id).group_by(company_groups.c.group_id)).all())
    for r in rows:
        r["companies"] = counts.get(r["id"], 0)
    return rows


def add_group(user_id: int, name: str, kind: str, note: str = "") -> str | None:
    """Returns an error message, or None when added."""
    name = " ".join((name or "").split())[:60]
    if kind not in KINDS:
        return "Choose sector or structural theme."
    if not name:
        return "Give it a name."
    existing = list_groups(user_id)
    if len(existing) >= MAX_GROUPS:
        return f"You can have up to {MAX_GROUPS} sectors and themes."
    if any(g["kind"] == kind and g["name"].lower() == name.lower() for g in existing):
        return f"You already have the {KINDS[kind].lower()} {next(g['name'] for g in existing if g['kind'] == kind and g['name'].lower() == name.lower())}."
    with get_engine().begin() as conn:
        conn.execute(insert(user_groups).values(user_id=user_id, name=name, kind=kind,
                                                note=(note or "").strip()[:300] or None, created_at=utcnow()))
    return None


def add_standard_sectors(user_id: int) -> int:
    have = {g["name"].lower() for g in list_groups(user_id, "sector")}
    added = 0
    for name in STANDARD_SECTORS:
        if name.lower() not in have and not add_group(user_id, name, "sector"):
            added += 1
    return added


def update_group(user_id: int, group_id: int, name: str, note: str = "") -> str | None:
    name = " ".join((name or "").split())[:60]
    if not name:
        return "Give it a name."
    groups = list_groups(user_id)
    me = next((g for g in groups if g["id"] == group_id), None)
    if not me:
        return "Not found."
    if any(g["id"] != group_id and g["kind"] == me["kind"] and g["name"].lower() == name.lower() for g in groups):
        return f"You already have {name}."
    with get_engine().begin() as conn:
        conn.execute(update(user_groups).where(user_groups.c.id == group_id, user_groups.c.user_id == user_id)
                     .values(name=name, note=(note or "").strip()[:300] or None))
    return None


def delete_group(user_id: int, group_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(company_groups).where(company_groups.c.user_id == user_id, company_groups.c.group_id == group_id))
        conn.execute(delete(user_groups).where(user_groups.c.user_id == user_id, user_groups.c.id == group_id))


def assignments(user_id: int) -> dict[tuple[str, str], dict]:
    """{(market, ticker): {"sector": {id, name} | None, "themes": [{id, name}, ...]}}"""
    with get_engine().connect() as conn:
        rows = conn.execute(select(company_groups.c.market, company_groups.c.ticker, user_groups.c.id, user_groups.c.name,
                                   user_groups.c.kind)
                            .select_from(company_groups.join(user_groups, and_(user_groups.c.id == company_groups.c.group_id,
                                                                               user_groups.c.user_id == company_groups.c.user_id)))
                            .where(company_groups.c.user_id == user_id)).all()
    out: dict[tuple[str, str], dict] = {}
    for m, t, gid, name, kind in rows:
        a = out.setdefault((m, t), {"sector": None, "themes": []})
        if kind == "sector":
            a["sector"] = {"id": gid, "name": name}
        else:
            a["themes"].append({"id": gid, "name": name})
    for a in out.values():
        a["themes"].sort(key=lambda g: g["name"].lower())
    return out


def set_company(user_id: int, market: str, ticker: str, sector_id: int | None, theme_ids: list[int]) -> None:
    """Replace the company's sector and themes with these (ids must be the member's own)."""
    mine = {g["id"]: g["kind"] for g in list_groups(user_id)}
    wanted = ([sector_id] if sector_id in mine and mine[sector_id] == "sector" else []) + \
             [g for g in dict.fromkeys(theme_ids or []) if mine.get(g) == "theme"]
    with get_engine().begin() as conn:
        conn.execute(delete(company_groups).where(company_groups.c.user_id == user_id, company_groups.c.market == market,
                                                  company_groups.c.ticker == ticker))
        if wanted:
            conn.execute(insert(company_groups), [{"user_id": user_id, "group_id": g, "market": market, "ticker": ticker}
                                                  for g in wanted])


def companies_in(user_id: int, group_id: int) -> set[tuple[str, str]]:
    with get_engine().connect() as conn:
        return set(conn.execute(select(company_groups.c.market, company_groups.c.ticker).where(
            company_groups.c.user_id == user_id, company_groups.c.group_id == group_id)).all())
