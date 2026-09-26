"""Accounts: bcrypt hashes, login throttling, sign-up modes."""
from __future__ import annotations

import re
import secrets
from datetime import timedelta

import bcrypt
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .config import get_secret
from .db import get_engine, login_attempts, users, utcnow

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,24}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_ATTEMPTS = 5
WINDOW = timedelta(minutes=15)
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(rounds=12)).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), pw_hash.encode("utf-8"))
    except ValueError:
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 10:
        return "Use at least 10 characters."
    if len(password.encode("utf-8")) > 72:
        return "Use at most 72 characters."
    if password.isdigit() or password.isalpha():
        return "Mix letters with numbers or symbols."
    return None


def signup_mode() -> str:
    mode = str(get_secret("SIGNUP_MODE", "invite")).lower()
    return mode if mode in {"open", "invite", "closed"} else "invite"


def invite_ok(code: str) -> bool:
    expected = get_secret("INVITE_CODE")
    return bool(expected) and secrets.compare_digest(code.strip(), str(expected))


def get_user(user_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(users).where(users.c.id == user_id)).mappings().first()
    return dict(row) if row else None


def authenticate(username: str, password: str) -> tuple[dict | None, str | None]:
    uname = username.strip().lower()
    if not uname or not password:
        return None, "Enter your username and password."
    with get_engine().begin() as conn:
        conn.execute(delete(login_attempts).where(login_attempts.c.ts < utcnow() - timedelta(days=1)))
        failures = conn.execute(
            select(func.count()).select_from(login_attempts).where(
                login_attempts.c.username == uname, login_attempts.c.ts >= utcnow() - WINDOW)
        ).scalar_one()
        if failures >= MAX_ATTEMPTS:
            return None, "Too many failed attempts. Try again in 15 minutes."
        row = conn.execute(select(users).where(users.c.username == uname)).mappings().first()
        # Always run bcrypt so response time doesn't reveal whether the username exists.
        password_ok = verify_password(password, row["pw_hash"] if row else _DUMMY_HASH)
        if not (row and password_ok and row["is_active"]):
            conn.execute(insert(login_attempts).values(username=uname, ts=utcnow()))
            return None, "Username or password is incorrect."
        conn.execute(delete(login_attempts).where(login_attempts.c.username == uname))
        return dict(row), None


def create_user(username: str, password: str, email: str | None = None,
                role: str = "user", plan: str = "free") -> tuple[dict | None, str | None]:
    uname = username.strip().lower()
    if not USERNAME_RE.match(uname):
        return None, "Usernames are 3 to 24 letters, numbers or underscores."
    if email and not EMAIL_RE.match(email.strip()):
        return None, "That email address doesn't look right."
    problem = password_problem(password)
    if problem:
        return None, problem
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(users).values(
                username=uname, email=(email or "").strip() or None, pw_hash=hash_password(password),
                role=role, plan=plan, is_active=True, created_at=utcnow()))
            row = conn.execute(select(users).where(users.c.username == uname)).mappings().first()
        return dict(row), None
    except IntegrityError:
        return None, "That username is taken."


def change_password(user_id: int, old: str, new: str) -> str | None:
    user = get_user(user_id)
    if not user or not verify_password(old, user["pw_hash"]):
        return "Your current password is incorrect."
    problem = password_problem(new)
    if problem:
        return problem
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(pw_hash=hash_password(new)))
    return None


def update_user(user_id: int, **values) -> None:
    allowed = {k: v for k, v in values.items()
               if k in {"role", "plan", "is_active", "telegram_chat_id", "tg_link_code", "email"}}
    if allowed:
        with get_engine().begin() as conn:
            conn.execute(update(users).where(users.c.id == user_id).values(**allowed))


def ensure_admin() -> None:
    username, password = get_secret("ADMIN_USERNAME"), get_secret("ADMIN_PASSWORD")
    if not username or not password:
        return
    with get_engine().connect() as conn:
        exists = conn.execute(select(users.c.id).where(users.c.username == username.lower())).first()
    if not exists:
        create_user(username, password, role="admin", plan="pro")
