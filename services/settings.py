"""Flexible per-member settings stored as key/value pairs."""
from __future__ import annotations

import json

from sqlalchemy import delete, insert, select

from core.db import get_engine, user_settings

DEFAULTS = {"email": "", "email_alerts": False, "email_briefs": True, "morning_brief": False, "morning_hour": 7,
            "weekly_ai": True, "monthly_pdf": True, "insider_alerts": True, "new_listing_markets": [],
            "importance_sort": False, "ipo_alert_countries": [], "ipo_alert_sectors": [], "report_theme": "light"}


def get(user_id: int) -> dict:
    with get_engine().connect() as conn:
        rows = conn.execute(select(user_settings.c.key, user_settings.c.value).where(user_settings.c.user_id == user_id)).all()
    out = dict(DEFAULTS)
    for key, value in rows:
        try:
            out[key] = json.loads(value)
        except (TypeError, ValueError):
            out[key] = value
    return out


def save(user_id: int, **values) -> None:
    with get_engine().begin() as conn:
        for key, value in values.items():
            conn.execute(delete(user_settings).where(user_settings.c.user_id == user_id, user_settings.c.key == key))
            conn.execute(insert(user_settings).values(user_id=user_id, key=key, value=json.dumps(value)))
