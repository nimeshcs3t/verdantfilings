"""Database schema and engine. Works with SQLite locally and Postgres (Supabase/Neon) in production."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, Date, DateTime, Index, Integer, MetaData, String, Table, Text,
                        create_engine)
from sqlalchemy.engine import Engine

from .config import get_secret


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(32), unique=True, nullable=False),
    Column("email", String(254)),
    Column("pw_hash", String(128), nullable=False),
    Column("role", String(16), nullable=False, default="user"),     # user | admin
    Column("plan", String(16), nullable=False, default="free"),     # free | pro
    Column("telegram_chat_id", String(32)),
    Column("tg_link_code", String(32)),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), default=utcnow),
)

watchlist = Table(
    "watchlist", metadata,
    Column("user_id", Integer, primary_key=True),
    Column("market", String(8), primary_key=True),
    Column("ticker", String(16), primary_key=True),
    Column("notify", Boolean, nullable=False, default=True),
    Column("added_at", DateTime(timezone=True), default=utcnow),
)

companies = Table(
    "companies", metadata,
    Column("market", String(8), primary_key=True),
    Column("ticker", String(16), primary_key=True),
    Column("source_id", String(32)),
    Column("name_local", String(200)),
    Column("name_en", String(200)),
    Column("last_synced", DateTime(timezone=True)),
)

filings = Table(
    "filings", metadata,
    Column("uid", String(64), primary_key=True),          # e.g. KR:20260925000123
    Column("market", String(8), nullable=False),
    Column("ticker", String(16), nullable=False),
    Column("company_name", String(200)),
    Column("filed_date", Date, nullable=False),
    Column("title_local", Text),
    Column("title_en", Text),
    Column("filer", String(200)),
    Column("url", Text),
    Column("summary_en", Text),
    Column("body_en", Text),
    Column("notified", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), default=utcnow),
    Index("ix_filings_company_date", "market", "ticker", "filed_date"),
    Index("ix_filings_date", "filed_date"),
    Index("ix_filings_notified", "notified"),
)

messages = Table(
    "messages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("market", String(8), nullable=False),
    Column("ticker", String(16), nullable=False),
    Column("user_id", Integer, nullable=False),
    Column("username", String(32), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow),
    Index("ix_messages_company", "market", "ticker", "created_at"),
)

translations = Table(
    "translations", metadata,
    Column("key", String(64), primary_key=True),
    Column("text_en", Text),
)

login_attempts = Table(
    "login_attempts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(64), nullable=False),
    Column("ts", DateTime(timezone=True), default=utcnow),
    Index("ix_login_attempts", "username", "ts"),
)

listed_companies = Table(
    "listed_companies", metadata,     # full exchange listing, refreshed daily by the worker
    Column("market", String(8), primary_key=True),
    Column("ticker", String(16), primary_key=True),
    Column("source_id", String(32)),
    Column("name_local", String(200)),
    Column("name_en", String(200)),
    Column("updated_at", DateTime(timezone=True), default=utcnow),
)

enrich_queue = Table(
    "enrich_queue", metadata,         # filings a member asked to translate; the worker processes them
    Column("uid", String(64), primary_key=True),
    Column("requested_at", DateTime(timezone=True), default=utcnow),
)

api_usage = Table(
    "api_usage", metadata,            # paid/limited API calls per market per day
    Column("market", String(8), primary_key=True),
    Column("day", String(10), primary_key=True),
    Column("calls", Integer, nullable=False, default=0),
    Column("last_call", DateTime(timezone=True)),
)

_engine: Engine | None = None
_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                url = str(get_secret("DATABASE_URL") or "sqlite:///filings.db").strip().strip('"').strip("'").strip()
                if url.startswith("postgres://"):
                    url = "postgresql://" + url[len("postgres://"):]
                kwargs = {"pool_pre_ping": True}
                if url.startswith("sqlite"):
                    kwargs["connect_args"] = {"check_same_thread": False}
                else:
                    kwargs.update(pool_size=5, max_overflow=5, pool_recycle=1800)
                engine = create_engine(url, **kwargs)
                metadata.create_all(engine)
                _engine = engine
    return _engine
