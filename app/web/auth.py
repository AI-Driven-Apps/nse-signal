"""Single-user auth (§2.1). Signed session cookie (itsdangerous), bcrypt
password hashing (the `bcrypt` library directly — not passlib, whose
CryptContext self-test breaks under bcrypt>=4.1: it's a known, unresolved
compat bug and passlib is effectively unmaintained), first-boot bootstrap
from ADMIN_USER/ADMIN_PASSWORD, simple in-memory login rate limiter.
No JWT/OAuth — Phase 3 swaps this behind an AUTH_MODE flag (CLAUDE.md §Phase 3)."""
import logging
import time

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from starlette.requests import Request

from ..config import ADMIN_PASSWORD, ADMIN_USER, COOKIE_MAX_AGE_DAYS, SESSION_SECRET
from ..models import User

log = logging.getLogger("auth")

COOKIE_NAME = "nse_signal_session"

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
_login_attempts: dict[str, list[float]] = {}


def _serializer() -> URLSafeTimedSerializer:
    if not SESSION_SECRET:
        raise RuntimeError(
            "SESSION_SECRET is not set — required to sign session cookies. "
            "Set it in .env (any long random string)."
        )
    return URLSafeTimedSerializer(SESSION_SECRET, salt="nse-signal-session")


def hash_password(plain: str) -> str:
    pw_bytes = plain.encode("utf-8")
    if len(pw_bytes) > 72:
        raise ValueError("Password too long (bcrypt supports at most 72 bytes).")
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def bootstrap_admin(session) -> None:
    """Create the single admin user from env on first boot. Safe to call
    every startup — no-ops once a user exists."""
    if session.execute(select(User)).first() is not None:
        return
    if not ADMIN_PASSWORD:
        log.warning("No users exist and ADMIN_PASSWORD is unset — "
                    "login is impossible until you set it and restart.")
        return
    try:
        pw_hash = hash_password(ADMIN_PASSWORD)
    except ValueError as e:
        log.error("Cannot bootstrap admin user: %s", e)
        return
    session.add(User(username=ADMIN_USER, password_hash=pw_hash))
    log.info("Bootstrapped admin user '%s'.", ADMIN_USER)


def make_session_cookie(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session_cookie(token: str) -> int | None:
    try:
        data = _serializer().loads(token, max_age=COOKIE_MAX_AGE_DAYS * 86400)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("uid")


def is_rate_limited(client_ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _login_attempts.get(client_ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    _login_attempts[client_ip] = attempts
    return len(attempts) >= LOGIN_MAX_ATTEMPTS


def record_login_attempt(client_ip: str) -> None:
    _login_attempts.setdefault(client_ip, []).append(time.time())


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
