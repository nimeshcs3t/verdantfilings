"""Download all of a member's data as one zip: JSON for everything, CSV for the main lists."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date, datetime

from sqlalchemy import select

from core import db

TABLES = ["watchlist", "watch_prefs", "transactions", "cash_moves", "journal", "notes", "stars", "keywords", "price_alerts",
          "fair_values", "targets", "goals", "user_prefs", "user_settings", "holdings"]
CSV_TABLES = ["transactions", "cash_moves", "journal", "watchlist"]


def _plain(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def export(user_id: int) -> bytes:
    data = {}
    with db.get_engine().connect() as conn:
        for name in TABLES:
            table = getattr(db, name, None)
            if table is None or "user_id" not in table.c:
                continue
            rows = [{k: _plain(v) for k, v in dict(r).items()} for r in conn.execute(
                select(table).where(table.c.user_id == user_id)).mappings()]
            data[name] = rows
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("verdant-backup.json", json.dumps({"exported_at": datetime.utcnow().isoformat() + "Z",
                                                       "tables": data}, ensure_ascii=False, indent=1))
        for name in CSV_TABLES:
            rows = data.get(name) or []
            if rows:
                out = io.StringIO()
                w = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
                zf.writestr(f"{name}.csv", out.getvalue())
        zf.writestr("README.txt", "Your Verdant Filings data. verdant-backup.json has everything; the CSV files open in "
                                  "Excel or Google Sheets.\n")
    return buf.getvalue()
