"""Dashboard password: hashing, checking and lockout after repeated failures.

Passwords are never stored. Only a salted scrypt hash goes in .env, in the
form  scrypt:N:r:p:salt:hash  (salt and hash are base64).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from enum import Enum

from app import store

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024  # stops someone making the server hash megabytes of text

# OWASP-recommended scrypt cost (2^17, 8, 1): about 128 MB and ~0.3 s per check.
DEFAULT_N = 2**17
_R = 8
_P = 1
_KEY_LENGTH = 32
_SALT_BYTES = 16

LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW = timedelta(minutes=15)
SESSION_LIFETIME = timedelta(hours=8)


class LoginResult(Enum):
    OK = "ok"
    WRONG_PASSWORD = "wrong_password"
    LOCKED_OUT = "locked_out"
    NOT_CONFIGURED = "not_configured"


def password_problems(password: str) -> list[str]:
    """Reasons a new password is too weak (empty list = fine)."""
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Use at most {MAX_PASSWORD_LENGTH} characters.")
    if password.strip() != password:
        problems.append("Don't start or end with a space.")
    if password and len(set(password)) < 4:
        problems.append("Use more different characters.")
    return problems


def hash_password(password: str, *, n: int = DEFAULT_N) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt, n, _R, _P)
    return f"scrypt:{n}:{_R}:{_P}:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, stored_hash: str) -> bool:
    """True if `password` matches. Malformed hashes simply don't match."""
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        scheme, n, r, p, salt, expected = stored_hash.split(":")
        if scheme != "scrypt":
            return False
        n_i, r_i, p_i = int(n), int(r), int(p)
        if not (2**10 <= n_i <= 2**20 and 1 <= r_i <= 32 and 1 <= p_i <= 16):
            return False  # refuse silly cost values from a tampered .env
        actual = _scrypt(password, _unb64(salt), n_i, r_i, p_i)
        return hmac.compare_digest(actual, _unb64(expected))
    except (ValueError, TypeError):
        return False


def is_locked_out(conn: sqlite3.Connection, now: datetime) -> bool:
    return store.failed_logins_since(conn, now - LOCKOUT_WINDOW) >= LOCKOUT_THRESHOLD


def attempt_login(
    conn: sqlite3.Connection, password: str, stored_hash: str, now: datetime
) -> LoginResult:
    """Check a login attempt, recording it for the lockout rule."""
    if not stored_hash:
        return LoginResult.NOT_CONFIGURED
    if is_locked_out(conn, now):
        return LoginResult.LOCKED_OUT  # not even checked, so guessing is pointless
    ok = verify_password(password, stored_hash)
    store.record_login_attempt(conn, ok, now)
    return LoginResult.OK if ok else LoginResult.WRONG_PASSWORD


def session_expired(logged_in_at: datetime, now: datetime) -> bool:
    return now - logged_in_at > SESSION_LIFETIME


# ---------------------------------------------------------- remember me
# A random token lives in a browser cookie; the database keeps only its SHA-256,
# tied to the current password, so changing the password logs every browser out.

REMEMBER_FOR = timedelta(days=14)
COOKIE_NAME = "adf_session"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")  # secrets.token_urlsafe(32)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _password_tag(stored_hash: str) -> str:
    return hashlib.sha256(f"session:{stored_hash}".encode("utf-8")).hexdigest()[:32]


def create_remembered_session(conn: sqlite3.Connection, stored_hash: str, now: datetime) -> str:
    """Start a 'remember me' session; returns the token to put in the cookie."""
    token = secrets.token_urlsafe(32)
    store.save_login_session(conn, _token_hash(token), _password_tag(stored_hash), now, now + REMEMBER_FOR)
    return token


def check_remembered_session(conn: sqlite3.Connection, token: str | None, stored_hash: str, now: datetime) -> bool:
    """True if the cookie token is valid, unexpired, and from the current password."""
    if not isinstance(token, str) or not stored_hash or not _TOKEN_RE.match(token):
        return False
    session = store.find_login_session(conn, _token_hash(token), now)
    return bool(session) and hmac.compare_digest(session["password_tag"], _password_tag(stored_hash))


def end_remembered_session(conn: sqlite3.Connection, token: str | None) -> None:
    if isinstance(token, str) and _TOKEN_RE.match(token):
        store.delete_login_session(conn, _token_hash(token))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
        maxmem=256 * 1024 * 1024, dklen=_KEY_LENGTH,
    )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
