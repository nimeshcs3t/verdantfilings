"""Database size and freeing space: deleting filings members marked 'Read once'.

A filing is deleted only if someone marked it Read once, nobody marked it Important or Keep, and nobody wrote a
journal note on it. Deleted filings are
remembered (deleted_filings) so the background job never downloads them again or alerts about them twice."""
from __future__ import annotations

from sqlalchemy import delete, func, insert, select, text

from core.db import (deleted_filings, enrich_queue, events, events_scanned, filing_flags, filing_marks, filing_notes,
                     filing_texts, filings, get_engine, keyword_hits, metadata, stars, utcnow)

FREE_PLAN_MB = 500          # Supabase free plan database limit
RELATED = [filing_notes, filing_texts, filing_flags, enrich_queue, events, events_scanned, keyword_hits, filing_marks]


def _deletable(conn) -> list[str]:
    read_once = select(filing_marks.c.uid).where(filing_marks.c.mark == "read_once")
    from core.db import journal
    from sqlalchemy import union
    protected = union(select(filing_marks.c.uid).where(filing_marks.c.mark == "keep"), select(stars.c.uid),
                      select(journal.c.filing_uid).where(journal.c.filing_uid.is_not(None)))
    return list(conn.execute(select(filings.c.uid).where(filings.c.uid.in_(read_once), filings.c.uid.not_in(protected))).scalars())


def read_once_count(user_id: int | None = None) -> int:
    """Filings that the bulk delete would remove (optionally only those this member marked)."""
    with get_engine().connect() as conn:
        uids = _deletable(conn)
        if user_id is not None and uids:
            mine = set(conn.execute(select(filing_marks.c.uid).where(filing_marks.c.user_id == user_id,
                                                                      filing_marks.c.mark == "read_once")).scalars())
            uids = [u for u in uids if u in mine]
    return len(uids)


def delete_read_once() -> int:
    """Delete every 'Read once' filing that nobody marked Important or Keep, with its overview, notes and dates."""
    with get_engine().connect() as conn:
        uids = _deletable(conn)
    done = 0
    for i in range(0, len(uids), 400):
        chunk = uids[i:i + 400]
        with get_engine().begin() as conn:
            for table in RELATED:
                conn.execute(delete(table).where(table.c.uid.in_(chunk)))
            done += conn.execute(delete(filings).where(filings.c.uid.in_(chunk))).rowcount or 0
            known = set(conn.execute(select(deleted_filings.c.uid).where(deleted_filings.c.uid.in_(chunk))).scalars())
            rows = [{"uid": u, "deleted_at": utcnow()} for u in chunk if u not in known]
            if rows:
                conn.execute(insert(deleted_filings), rows)
    return done


def sizes() -> dict:
    """{'total_mb': database size or None, 'tables': [(table, MB or None, rows)]}, largest first."""
    engine = get_engine()
    tables = []
    total = None
    with engine.connect() as conn:
        if engine.dialect.name == "postgresql":
            try:
                total = conn.execute(text("select pg_database_size(current_database())")).scalar() / 1e6
                rows = conn.execute(text(
                    "select relname, pg_total_relation_size(relid), n_live_tup from pg_catalog.pg_stat_user_tables "
                    "where schemaname = 'public' order by 2 desc")).all()
                tables = [(r[0], r[1] / 1e6, int(r[2] or 0)) for r in rows]
            except Exception:
                tables = []
        if not tables:
            for t in metadata.sorted_tables:
                try:
                    tables.append((t.name, None, conn.execute(select(func.count()).select_from(t)).scalar_one()))
                except Exception:
                    continue
            tables.sort(key=lambda r: -r[2])
    return {"total_mb": total, "tables": tables}
