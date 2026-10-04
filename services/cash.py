"""Cash entries: deposits, withdrawals, interest, dividends received in cash, and account fees."""
from __future__ import annotations

from sqlalchemy import delete, insert, select

from core.db import cash_moves, get_engine

KINDS = {"deposit": "Deposit", "withdraw": "Withdrawal", "dividend": "Dividend received", "interest": "Interest",
         "fee": "Account fee"}


def list_moves(user_id: int) -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(select(cash_moves).where(cash_moves.c.user_id == user_id)
                                              .order_by(cash_moves.c.move_date.desc(), cash_moves.c.id.desc())).mappings()]


def add(user_id: int, move_date, kind: str, amount: float, currency: str, note: str = "") -> str | None:
    if kind not in KINDS or amount <= 0 or not currency:
        return "Choose a type and enter a positive amount."
    with get_engine().begin() as conn:
        conn.execute(insert(cash_moves).values(user_id=user_id, move_date=move_date, kind=kind, amount=amount,
                                               currency=currency.upper()[:8], note=(note or "")[:200]))
    return None


def remove(user_id: int, move_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(cash_moves).where(cash_moves.c.id == move_id, cash_moves.c.user_id == user_id))
