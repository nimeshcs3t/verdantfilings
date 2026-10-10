"""Daily clean-up so the free database stays small, and the run log for the Admin health panel."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, insert, select, update

from core.db import (api_usage, enrich_queue, events, filings, get_engine, keyword_hits, login_attempts, metadata,
                     run_log, sessions, stars, utcnow)
from core.usage import counts_today, get_state, set_state

log = logging.getLogger(__name__)
KEEP_FILINGS_DAYS = 730
KEEP_TEXT_DAYS = 180


def cleanup(force: bool = False) -> dict:
    """Once a day: remove old data that's no longer useful. Starred filings are always kept."""
    today = datetime.now(timezone.utc).date().isoformat()
    if not force and get_state("last_cleanup") == today:
        return {}
    now = utcnow()
    removed = {}
    with get_engine().begin() as conn:
        starred = select(stars.c.uid)
        removed["old filings"] = conn.execute(delete(filings).where(
            filings.c.filed_date < date.today() - timedelta(days=KEEP_FILINGS_DAYS), filings.c.uid.not_in(starred))).rowcount
        removed["long texts trimmed"] = conn.execute(update(filings).where(
            filings.c.filed_date < date.today() - timedelta(days=KEEP_TEXT_DAYS), filings.c.body_en.is_not(None))
            .values(body_en=None)).rowcount
        removed["keyword matches"] = conn.execute(delete(keyword_hits).where(keyword_hits.c.created_at < now - timedelta(days=90))).rowcount
        removed["stale requests"] = conn.execute(delete(enrich_queue).where(enrich_queue.c.requested_at < now - timedelta(days=7))).rowcount
        removed["past events"] = conn.execute(delete(events).where(events.c.event_date < date.today() - timedelta(days=30))).rowcount
        removed["run log"] = conn.execute(delete(run_log).where(run_log.c.started_at < now - timedelta(days=30))).rowcount
        removed["expired sign-ins"] = conn.execute(delete(sessions).where(sessions.c.expires_at < now)).rowcount
        from core.db import password_resets, security_events, share_links
        removed["old security log"] = conn.execute(delete(security_events).where(security_events.c.ts < now - timedelta(days=365))).rowcount
        removed["used reset links"] = conn.execute(delete(password_resets).where(password_resets.c.expires_at < now - timedelta(days=1))).rowcount
        removed["expired share links"] = conn.execute(delete(share_links).where(share_links.c.expires_at < now)).rowcount
        removed["old sign-in attempts"] = conn.execute(delete(login_attempts).where(login_attempts.c.ts < now - timedelta(days=1))).rowcount
        removed["old usage counters"] = conn.execute(delete(api_usage).where(
            api_usage.c.day < (date.today() - timedelta(days=90)).isoformat())).rowcount
    set_state("last_cleanup", today)
    return {k: v for k, v in removed.items() if v}


def log_run(seconds: float, stats: dict, warnings: list[str]) -> None:
    with get_engine().begin() as conn:
        conn.execute(insert(run_log).values(started_at=utcnow() - timedelta(seconds=seconds), seconds=round(seconds, 1),
                                            stats=json.dumps(stats, default=str), warnings="\n".join(warnings[-30:])))


def health() -> dict:
    with get_engine().connect() as conn:
        runs = [dict(r) for r in conn.execute(select(run_log).order_by(run_log.c.id.desc()).limit(50)).mappings()]
        sizes = {}
        for name, table in metadata.tables.items():
            try:
                sizes[name] = conn.execute(select(func.count()).select_from(table)).scalar_one()
            except Exception:
                sizes[name] = None
    for r in runs:
        try:
            r["stats"] = json.loads(r["stats"] or "{}")
        except ValueError:
            r["stats"] = {}
    return {"runs": runs, "sizes": sizes, "usage": counts_today(), "last_cleanup": get_state("last_cleanup")}
