"""Source status: for each market, when it last worked, what it last found, and any pause or recent error."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from core.db import as_utc, bot_state, companies, filings, get_engine, ipos, listed_companies, run_log, utcnow

PAUSE_KEYS = {"IL": ["maya"], "HK": ["hkex"], "NO": ["oslo"], "UK": ["fca_nsm"], "FR": ["amf"], "TW": ["twse"],
              "SE": ["nasdaq_nordic"], "DK": ["nasdaq_nordic"], "FI": ["nasdaq_nordic"], "JP": ["tdnet"]}
IPO_PAUSE = {"US": "ipo_nasdaq", "JP": "ipo_jpx", "HK": "ipo_hkex", "AU": "ipo_asx"}


def _paused(names: list[str]) -> datetime | None:
    with get_engine().connect() as conn:
        rows = conn.execute(select(bot_state.c.value).where(bot_state.c.key.in_([f"{n}_blocked_until" for n in names]))).scalars().all()
    until = None
    for v in rows:
        try:
            t = datetime.fromisoformat(v)
        except (TypeError, ValueError):
            continue
        if t > datetime.now(timezone.utc) and (until is None or t > until):
            until = t
    return until


def markets() -> list[dict]:
    from sources import configured_sources
    since = utcnow() - timedelta(days=7)
    with get_engine().connect() as conn:
        synced = dict(conn.execute(select(companies.c.market, func.max(companies.c.last_synced)).group_by(companies.c.market)).all())
        followed = dict(conn.execute(select(companies.c.market, func.count()).group_by(companies.c.market)).all())
        newest = dict(conn.execute(select(filings.c.market, func.max(filings.c.created_at)).group_by(filings.c.market)).all())
        week = dict(conn.execute(select(filings.c.market, func.count()).where(filings.c.created_at >= since).group_by(filings.c.market)).all())
        listed = dict(conn.execute(select(listed_companies.c.market, func.count()).group_by(listed_companies.c.market)).all())
        warnings = [w for (w,) in conn.execute(select(run_log.c.warnings).where(run_log.c.started_at >= utcnow() - timedelta(days=2))
                                                 .order_by(run_log.c.id.desc())).all() if w]
    errors: dict[str, str] = {}
    for block in warnings:
        for line in block.splitlines():
            m = re.search(r"\b(?:for|failed for|list failed for)\s+([A-Z]{2})\b[:\s]", line)
            if m and m[1] not in errors:
                errors[m[1]] = line[:160]
    out = []
    for src in configured_sources():
        m = src.market
        last_ok = as_utc(synced.get(m)) if synced.get(m) else None
        paused = _paused(PAUSE_KEYS.get(m, []))
        stale = bool(followed.get(m)) and (not last_ok or utcnow() - last_ok > timedelta(hours=6))
        state = "paused" if paused else "problem" if (stale or (m in errors and not last_ok)) else "warning" if m in errors else "ok"
        if not followed.get(m):
            state = "idle"
        out.append({"market": m, "country": src.country, "source": src.regulator, "state": state, "last_ok": last_ok,
                    "followed": followed.get(m, 0), "listed": listed.get(m, 0), "newest": as_utc(newest[m]) if newest.get(m) else None,
                    "week": week.get(m, 0), "paused": paused, "error": errors.get(m)})
    return out


def ipo_sources() -> list[dict]:
    """One row per IPO source (official feeds and websites): state, rows read last time, last success, last error."""
    from services.ipo_web import MARKETS, source_status
    out = []
    for r in source_status():
        last_ok = datetime.fromisoformat(r["last_ok"]) if r.get("last_ok") else None
        failing = bool(r.get("error"))
        state = ("problem" if failing and (not last_ok or utcnow() - last_ok > timedelta(hours=24)) else
                 "warning" if failing else "ok" if last_ok and utcnow() - last_ok < timedelta(hours=12) else
                 "problem" if last_ok else "idle")
        market = r["market"]
        country = "Euronext" if market == "EURONEXT" else MARKETS.get(market, (market,))[0]
        out.append({"source": r["source"], "market": market, "country": country, "state": state, "last_ok": last_ok,
                    "count": r.get("count", 0), "error": r.get("error") or ""})
    return out
