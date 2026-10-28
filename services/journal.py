"""Investment journal: dated entries per company (thesis, buys, sells, updates, reviews, lessons), with
conviction, tags and review reminders sent by Telegram/email."""
from __future__ import annotations

from datetime import date

from sqlalchemy import delete, func, insert, select, update

from core.db import filings, get_engine, journal

KINDS = {"thesis": "Thesis", "buy": "Buy", "sell": "Sell", "update": "Update", "review": "Review", "lesson": "Lesson",
         "filing": "Filing note"}


def entries(user_id: int, market: str | None = None, ticker: str | None = None, filing_uid: str | None = None) -> list[dict]:
    """Journal entries, newest first. Notes written on a filing carry that filing's date, title and link."""
    q = (select(journal, filings.c.filed_date.label("filing_date"), filings.c.title_en.label("filing_title"),
                filings.c.url.label("filing_url"))
         .select_from(journal.outerjoin(filings, filings.c.uid == journal.c.filing_uid))
         .where(journal.c.user_id == user_id))
    if market and ticker:
        q = q.where(journal.c.market == market, journal.c.ticker == ticker)
    if filing_uid:
        q = q.where(journal.c.filing_uid == filing_uid)
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(q.order_by(journal.c.entry_date.desc(), journal.c.id.desc())).mappings()]


def note_counts(user_id: int) -> dict[str, int]:
    """filing uid -> number of journal notes this member wrote on it."""
    with get_engine().connect() as conn:
        return dict(conn.execute(select(journal.c.filing_uid, func.count()).where(
            journal.c.user_id == user_id, journal.c.filing_uid.is_not(None)).group_by(journal.c.filing_uid)).all())


def add(user_id: int, market: str | None, ticker: str | None, entry_date: date, kind: str, title: str, body: str,
        conviction: int | None, tags: str, review_on: date | None, target_price: float | None = None,
        filing_uid: str | None = None) -> str | None:
    if kind not in KINDS or not (title or body).strip():
        return "Add a title or some text."
    with get_engine().begin() as conn:
        conn.execute(insert(journal).values(user_id=user_id, market=market, ticker=ticker, entry_date=entry_date, kind=kind,
                                            title=(title or "")[:200], body=(body or "")[:20000],
                                            conviction=conviction, tags=(tags or "")[:200], review_on=review_on,
                                            target_price=target_price if target_price and target_price > 0 else None,
                                            filing_uid=filing_uid, reminded=False))
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



def targets(user_id: int, market: str, ticker: str) -> list[tuple[float, str]]:
    """Price targets from journal entries, newest first, one per entry type."""
    out, seen = [], set()
    for e in entries(user_id, market, ticker):
        if e.get("target_price") and e["kind"] not in seen:
            seen.add(e["kind"])
            out.append((e["target_price"], f"{KINDS.get(e['kind'], e['kind'])} target ({e['entry_date']:%b %Y})"))
    return out[:3]
