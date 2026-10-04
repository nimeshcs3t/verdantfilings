"""Investment journal: dated entries per company (thesis, buys, sells, updates, reviews, lessons), with
conviction, tags and review reminders sent by Telegram/email."""
from __future__ import annotations

from datetime import date

from sqlalchemy import delete, insert, select, update

from core.db import get_engine, journal

KINDS = {"thesis": "Thesis", "buy": "Buy", "sell": "Sell", "update": "Update", "review": "Review", "lesson": "Lesson"}


def entries(user_id: int, market: str | None = None, ticker: str | None = None) -> list[dict]:
    q = select(journal).where(journal.c.user_id == user_id)
    if market and ticker:
        q = q.where(journal.c.market == market, journal.c.ticker == ticker)
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(q.order_by(journal.c.entry_date.desc(), journal.c.id.desc())).mappings()]


def add(user_id: int, market: str | None, ticker: str | None, entry_date: date, kind: str, title: str, body: str,
        conviction: int | None, tags: str, review_on: date | None) -> str | None:
    if kind not in KINDS or not (title or body).strip():
        return "Add a title or some text."
    with get_engine().begin() as conn:
        conn.execute(insert(journal).values(user_id=user_id, market=market, ticker=ticker, entry_date=entry_date, kind=kind,
                                            title=(title or "")[:200], body=(body or "")[:20000],
                                            conviction=conviction, tags=(tags or "")[:200], review_on=review_on,
                                            reminded=False))
    return None


def edit(user_id: int, entry_id: int, **values) -> None:
    values = {k: v for k, v in values.items() if k in ("entry_date", "kind", "title", "body", "conviction", "tags", "review_on")}
    if "review_on" in values:
        values["reminded"] = False
    with get_engine().begin() as conn:
        conn.execute(update(journal).where(journal.c.id == entry_id, journal.c.user_id == user_id).values(**values))


def remove(user_id: int, entry_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(journal).where(journal.c.id == entry_id, journal.c.user_id == user_id))


def due_reminders() -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(select(journal).where(
            journal.c.review_on.is_not(None), journal.c.review_on <= date.today(), journal.c.reminded == False)).mappings()]  # noqa: E712


def send_reminders() -> int:
    """Worker: remind members of journal reviews that are due."""
    from . import deliver
    from .pipeline import get_company
    sent = 0
    for e in due_reminders():
        name = (get_company(e["market"], e["ticker"], resolve=False) or {}).get("name_en", e["ticker"]) if e["ticker"] else "your portfolio"
        text = (f"<b>Journal review due</b>\n<b>{name}</b>" + (f"  {e['ticker']}" if e["ticker"] else "")
                + f"\nFrom your {KINDS.get(e['kind'], e['kind']).lower()} on {e['entry_date']:%d %b %Y}: {e['title'] or ''}\n"
                + ((e["body"] or "")[:500]) + "\n\nOpen the Journal page to add a review.")
        sent += deliver.send(e["user_id"], f"Journal review: {name}", text)
        with get_engine().begin() as conn:
            conn.execute(update(journal).where(journal.c.id == e["id"]).values(reminded=True))
    return sent


def stats(rows: list[dict]) -> dict:
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    convictions = [r["conviction"] for r in rows if r.get("conviction")]
    return {"entries": len(rows), "companies": len({(r["market"], r["ticker"]) for r in rows if r["ticker"]}),
            "by_kind": by_kind, "avg_conviction": (sum(convictions) / len(convictions)) if convictions else None,
            "reviews_due": sum(1 for r in rows if r.get("review_on") and r["review_on"] <= date.today() and not r["reminded"])}
