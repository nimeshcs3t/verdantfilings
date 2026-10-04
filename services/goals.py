"""Goal planner: projection with monthly saving and expected return, and what it takes to reach a target."""
from __future__ import annotations

from datetime import date

from sqlalchemy import delete, insert, select

from core.db import get_engine, goals


def get(user_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(goals).where(goals.c.user_id == user_id)).mappings().first()
    return dict(row) if row else None


def save(user_id: int, target: float, target_date: date, monthly: float, expected_return: float, currency: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(goals).where(goals.c.user_id == user_id))
        conn.execute(insert(goals).values(user_id=user_id, target=target, target_date=target_date, monthly=monthly,
                                          expected_return=expected_return, currency=currency))


def months_between(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def project(value: float, monthly: float, annual: float, months: int) -> list[float]:
    """Value at the end of each month (month 0 = today), adding `monthly` each month."""
    r = (1 + annual) ** (1 / 12) - 1
    out, v = [value], value
    for _ in range(months):
        v = v * (1 + r) + monthly
        out.append(v)
    return out


def needed_monthly(value: float, target: float, annual: float, months: int) -> float:
    if months <= 0:
        return max(0.0, target - value)
    r = (1 + annual) ** (1 / 12) - 1
    grown = value * (1 + r) ** months
    factor = (((1 + r) ** months - 1) / r) if r else months
    return max(0.0, (target - grown) / factor)


def needed_return(value: float, target: float, monthly: float, months: int) -> float | None:
    """Annual return needed to reach the target with this monthly saving (None if out of range)."""
    if months <= 0:
        return None
    lo, hi = -0.5, 2.0
    final = lambda a: project(value, monthly, a, months)[-1]
    if final(hi) < target:
        return None
    if final(lo) >= target:
        return lo
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if final(mid) < target else (lo, mid)
    return hi


def chart_svg(points: list[float], target: float, labels: tuple[str, str]) -> str:
    if len(points) < 2:
        return ""
    w, h, pad = 720, 200, 30
    top = max(max(points), target) * 1.08 or 1
    x = lambda i: pad + i * (w - 2 * pad) / (len(points) - 1)
    y = lambda v: h - pad - v / top * (h - 2 * pad)
    line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(points))
    hit = next((i for i, v in enumerate(points) if v >= target), None)
    mark = (f'<circle cx="{x(hit):.1f}" cy="{y(points[hit]):.1f}" r="5" class="goal-hit"><title>Target reached</title></circle>'
            if hit is not None else "")
    return (f'<div class="perf"><svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" aria-label="Goal projection">'
            f'<line x1="{pad}" x2="{w - pad}" y1="{y(target):.1f}" y2="{y(target):.1f}" class="bench"/>'
            f'<polyline class="port" fill="none" points="{line}"/>{mark}'
            f'<text x="{pad}" y="{h - 8}">{labels[0]}</text><text x="{w - pad}" y="{h - 8}" text-anchor="end">{labels[1]}</text>'
            f'<text x="{w - pad}" y="{y(target) - 6:.1f}" text-anchor="end">Target</text></svg></div>')
