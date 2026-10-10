"""Accounts: bcrypt hashes, login throttling, sign-up modes."""
from __future__ import annotations

import re
import secrets
from datetime import timedelta

import bcrypt
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .config import get_secret
from .db import as_utc, get_engine, login_attempts, password_resets, users, utcnow

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


def password_problem(password: str, check_breach: bool = True) -> str | None:
    if len(password) < 10:
        return "Use at least 10 characters."
    if len(password.encode("utf-8")) > 72:
        return "Use at most 72 characters."
    if password.isdigit() or password.isalpha():
        return "Mix letters with numbers or symbols."
    if check_breach and str(get_secret("CHECK_BREACHED_PASSWORDS", "true")).lower() not in {"0", "false", "no"}:
        from .security import breached
        if breached(password):
            return ("That password has appeared in a known data breach, so attackers try it first. "
                    "Please choose a different one.")
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


def _too_many(conn, key: str) -> bool:
    conn.execute(delete(login_attempts).where(login_attempts.c.ts < utcnow() - timedelta(days=1)))
    return conn.execute(select(func.count()).select_from(login_attempts).where(
        login_attempts.c.username == key, login_attempts.c.ts >= utcnow() - WINDOW)).scalar_one() >= MAX_ATTEMPTS


def authenticate(username: str, password: str) -> tuple[dict | None, str | None]:
    """Checks the password only. If the member has two-factor sign-in on, call verify_second_factor next."""
    from .security import record
    uname = username.strip().lower()
    if not uname or not password:
        return None, "Enter your username and password."
    with get_engine().begin() as conn:
        locked = _too_many(conn, uname)
    if locked:
        record("login_locked", None, uname)
        return None, "Too many failed attempts. Try again in 15 minutes."
    with get_engine().begin() as conn:
        row = conn.execute(select(users).where(users.c.username == uname)).mappings().first()
        # Always run bcrypt so response time doesn't reveal whether the username exists.
        password_ok = verify_password(password, row["pw_hash"] if row else _DUMMY_HASH)
        if not (row and password_ok and row["is_active"]):
            conn.execute(insert(login_attempts).values(username=uname, ts=utcnow()))
            failed = True
        else:
            conn.execute(delete(login_attempts).where(login_attempts.c.username == uname))
            failed = False
    if failed:
        record("login_failed", row["id"] if row else None, uname)
        return None, "Username or password is incorrect."
    return dict(row), None


def needs_second_factor(user: dict) -> bool:
    return bool(user.get("totp_secret"))


def verify_second_factor(user_id: int, code: str) -> tuple[bool, str | None]:
    """An authenticator code, or one of the member's backup codes (each works once)."""
    import json
    from . import totp
    from .security import record
    key = f"2fa:{user_id}"
    user = get_user(user_id)
    if not user or not user.get("totp_secret"):
        return False, "Two-factor sign-in isn't set up for this account."
    with get_engine().begin() as conn:
        locked = _too_many(conn, key)
    if locked:
        record("login_locked", user_id, user["username"], "two-factor")
        return False, "Too many wrong codes. Try again in 15 minutes."
    with get_engine().begin() as conn:
        step = totp.verify(user["totp_secret"], code, user.get("totp_last_step"))
        if step is not None:
            conn.execute(update(users).where(users.c.id == user_id).values(totp_last_step=step))
            conn.execute(delete(login_attempts).where(login_attempts.c.username == key))
            return True, None
        codes = json.loads(user.get("backup_codes") or "[]")
        h = totp.hash_code(code or "")
        if (code or "").strip() and h in codes:
            codes.remove(h)
            conn.execute(update(users).where(users.c.id == user_id).values(backup_codes=json.dumps(codes)))
            conn.execute(delete(login_attempts).where(login_attempts.c.username == key))
            used = True
        else:
            conn.execute(insert(login_attempts).values(username=key, ts=utcnow()))
            used = False
    if used:
        record("backup_code_used", user_id, user["username"], f"{len(codes)} backup codes left")
        return True, None
    record("2fa_failed", user_id, user["username"])
    return False, "That code isn't right. Codes change every 30 seconds; use the newest one."


def enable_second_factor(user_id: int, secret: str, code: str) -> tuple[list[str] | None, str | None]:
    """Turn on two-factor sign-in after the member proves their app works. Returns new backup codes to show once."""
    import json
    from . import totp
    from .security import record
    step = totp.verify(secret, code)
    if step is None:
        return None, "That code doesn't match. Check the time on your phone is set automatically, then try the newest code."
    codes = totp.new_backup_codes()
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(
            totp_secret=secret, totp_last_step=step, backup_codes=json.dumps([totp.hash_code(c) for c in codes])))
    user = get_user(user_id)
    record("2fa_on", user_id, user["username"] if user else None)
    return codes, None


def new_backup_codes(user_id: int) -> list[str]:
    import json
    from . import totp
    from .security import record
    codes = totp.new_backup_codes()
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(backup_codes=json.dumps([totp.hash_code(c) for c in codes])))
    record("backup_codes_new", user_id, (get_user(user_id) or {}).get("username"))
    return codes


def backup_codes_left(user: dict) -> int:
    import json
    return len(json.loads(user.get("backup_codes") or "[]"))


def disable_second_factor(user_id: int, password: str | None, by_admin: dict | None = None) -> str | None:
    from .security import record
    user = get_user(user_id)
    if not user:
        return "No such member."
    if by_admin is None and not verify_password(password or "", user["pw_hash"]):
        return "Your password is incorrect."
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(totp_secret=None, totp_last_step=None, backup_codes=None))
    if by_admin:
        record("admin_2fa_reset", user_id, user["username"], f"by {by_admin['username']}")
    else:
        record("2fa_off", user_id, user["username"])
    return None


def admin_requires_2fa() -> bool:
    return str(get_secret("ADMIN_REQUIRE_2FA", "true")).lower() not in {"0", "false", "no", "off"}


def create_user(username: str, password: str, email: str | None = None,
                role: str = "user", plan: str = "free", check_breach: bool = True) -> tuple[dict | None, str | None]:
    uname = username.strip().lower()
    if not USERNAME_RE.match(uname):
        return None, "Usernames are 3 to 24 letters, numbers or underscores."
    if email and not EMAIL_RE.match(email.strip()):
        return None, "That email address doesn't look right."
    problem = password_problem(password, check_breach=check_breach)
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


def change_password(user_id: int, old: str, new: str, keep_session: str | None = None) -> str | None:
    """Also signs out every other device."""
    from .security import record
    user = get_user(user_id)
    if not user or not verify_password(old, user["pw_hash"]):
        return "Your current password is incorrect."
    problem = password_problem(new)
    if problem:
        return problem
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(pw_hash=hash_password(new)))
    from services.personal import end_all_sessions
    end_all_sessions(user_id, keep=keep_session)
    record("password_changed", user_id, user["username"])
    return None


# ---- password reset by email -------------------------------------------------------------------------------------
RESET_MINUTES = 30


def recovery_email(user: dict) -> str | None:
    try:
        from services import settings
        return (settings.get(user["id"]).get("email") or user.get("email") or "").strip() or None
    except Exception:
        return (user.get("email") or "").strip() or None


def _reset_hash(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def request_reset(identifier: str) -> None:
    """Email a one-time reset link if the username or email matches a member with an email address. Says nothing
    about whether it matched, so the form can't be used to discover accounts."""
    from .security import record
    ident = (identifier or "").strip().lower()
    if not ident:
        return
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(select(users).where(users.c.is_active == True)).mappings()]  # noqa: E712
    user = next((u for u in rows if u["username"] == ident), None) or next(
        (u for u in rows if (recovery_email(u) or "").lower() == ident), None)
    if not user:
        record("reset_requested", None, ident[:64], "no matching member")
        return
    address = recovery_email(user)
    with get_engine().begin() as conn:
        recent = conn.execute(select(func.count()).select_from(password_resets).where(
            password_resets.c.user_id == user["id"],
            password_resets.c.expires_at >= utcnow() + timedelta(minutes=RESET_MINUTES) - timedelta(hours=1))).scalar_one()
        blocked = recent >= 3 or not address
        if not blocked:
            conn.execute(delete(password_resets).where(password_resets.c.expires_at < utcnow() - timedelta(days=1)))
            token = secrets.token_urlsafe(32)
            conn.execute(insert(password_resets).values(token_hash=_reset_hash(token), user_id=user["id"],
                                                        expires_at=utcnow() + timedelta(minutes=RESET_MINUTES)))
    if blocked:
        record("reset_requested", user["id"], user["username"], "too many requests" if address else "no email on file")
        return
    app = (get_secret("APP_URL") or "").rstrip("/")
    link = f"{app}/?reset={token}"
    from services import deliver, emailer
    body = (f"<b>Reset your Verdant Filings password</b>\n\nSomeone (hopefully you) asked to reset the password for "
            f"<b>{user['username']}</b>.\n\n<a href=\"{link}\">Choose a new password</a>\n\nThe link works once and "
            f"expires in {RESET_MINUTES} minutes. If you didn't ask for this, ignore this email; your password stays the same.")
    emailer.send(address, "Reset your Verdant Filings password", deliver.telegram_to_html(body))
    record("reset_requested", user["id"], user["username"], "link emailed")


def reset_user(token: str | None) -> int | None:
    if not token or len(token) > 100:
        return None
    with get_engine().connect() as conn:
        row = conn.execute(select(password_resets).where(password_resets.c.token_hash == _reset_hash(token))).mappings().first()
    if not row or row["used_at"] or as_utc(row["expires_at"]) < utcnow():
        return None
    return row["user_id"]


def complete_reset(token: str, new: str) -> str | None:
    from .security import record
    user_id = reset_user(token)
    if not user_id:
        return "This reset link has expired or was already used. Ask for a new one."
    problem = password_problem(new)
    if problem:
        return problem
    username = get_user(user_id)["username"]
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(pw_hash=hash_password(new)))
        conn.execute(update(password_resets).where(password_resets.c.user_id == user_id, password_resets.c.used_at.is_(None))
                     .values(used_at=utcnow()))
        conn.execute(delete(login_attempts).where(login_attempts.c.username.in_([username, f"2fa:{user_id}"])))
    from services.personal import end_all_sessions
    end_all_sessions(user_id)
    user = get_user(user_id)
    record("password_reset", user_id, user["username"])
    try:
        from services import deliver
        deliver.send(user_id, "Your password was changed", "<b>Your Verdant Filings password was just reset</b> using an "
                     "email link. All devices were signed out. If this wasn't you, contact the admin now.", kind="security")
    except Exception:
        pass
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
        create_user(username, password, role="admin", plan="pro", check_breach=False)
