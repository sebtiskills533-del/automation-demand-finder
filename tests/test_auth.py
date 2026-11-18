from datetime import datetime, timedelta, timezone

import pytest

from app import auth
from app.auth import LoginResult, attempt_login, hash_password, password_problems, verify_password
from app.config import load_settings
from app.set_password import ENV_KEY, set_password, write_env_value

FAST_N = 2**10  # cheap hashing so tests stay fast
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def stored():
    return hash_password("correct horse battery", n=FAST_N)


def test_hash_format_and_verify(stored):
    assert stored.startswith(f"scrypt:{FAST_N}:8:1:")
    assert "correct horse" not in stored
    assert verify_password("correct horse battery", stored)
    assert not verify_password("wrong password!!", stored)


def test_same_password_gets_different_salts():
    assert hash_password("same password 1", n=FAST_N) != hash_password("same password 1", n=FAST_N)


def test_default_cost_is_strong():
    assert auth.DEFAULT_N >= 2**17


@pytest.mark.parametrize(
    "bad_hash",
    ["", "plain", "md5:1:2:3:4:5", "scrypt:abc:8:1:x:y", "scrypt:2:8:1:AAAA:AAAA", "scrypt:1048577000:8:1:AA:AA"],
)
def test_malformed_or_tampered_hashes_never_match(bad_hash):
    assert verify_password("anything at all", bad_hash) is False


def test_empty_and_huge_passwords_rejected(stored):
    assert not verify_password("", stored)
    assert not verify_password("x" * 5000, stored)


@pytest.mark.parametrize(
    ("password", "ok"),
    [("short", False), ("aaaaaaaaaaaa", False), (" leading space1", False), ("a good passphrase", True)],
)
def test_password_strength(password, ok):
    assert (password_problems(password) == []) is ok


def test_login_success_and_failure(conn, stored):
    assert attempt_login(conn, "correct horse battery", stored, NOW) is LoginResult.OK
    assert attempt_login(conn, "nope nope nope", stored, NOW) is LoginResult.WRONG_PASSWORD


def test_not_configured(conn):
    assert attempt_login(conn, "whatever pass", "", NOW) is LoginResult.NOT_CONFIGURED


def test_lockout_after_five_failures_even_with_right_password(conn, stored):
    for i in range(auth.LOCKOUT_THRESHOLD):
        assert attempt_login(conn, "bad guess", stored, NOW + timedelta(seconds=i)) is LoginResult.WRONG_PASSWORD
    later = NOW + timedelta(minutes=1)
    assert attempt_login(conn, "correct horse battery", stored, later) is LoginResult.LOCKED_OUT


def test_lockout_ends_after_window(conn, stored):
    for i in range(auth.LOCKOUT_THRESHOLD):
        attempt_login(conn, "bad guess", stored, NOW + timedelta(seconds=i))
    after = NOW + auth.LOCKOUT_WINDOW + timedelta(minutes=1)
    assert attempt_login(conn, "correct horse battery", stored, after) is LoginResult.OK


def test_session_expiry():
    assert not auth.session_expired(NOW, NOW + timedelta(hours=1))
    assert auth.session_expired(NOW, NOW + auth.SESSION_LIFETIME + timedelta(seconds=1))


# ------------------------------------------------------------ .env writer


def test_write_env_value_replaces_only_that_line(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-123\nDASHBOARD_PASSWORD_HASH=old\nLOG_LEVEL=INFO\n", encoding="utf-8")
    write_env_value(env, "DASHBOARD_PASSWORD_HASH", "new:value")
    assert env.read_text(encoding="utf-8") == (
        "ANTHROPIC_API_KEY=sk-123\nDASHBOARD_PASSWORD_HASH=new:value\nLOG_LEVEL=INFO\n"
    )


def test_write_env_value_appends_when_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    write_env_value(env, "B_KEY", "2")
    assert env.read_text(encoding="utf-8") == "A=1\nB_KEY=2\n"


def test_write_env_value_creates_from_example(tmp_path):
    (tmp_path / ".env.example").write_text("ANTHROPIC_API_KEY=\nDASHBOARD_PASSWORD_HASH=\n", encoding="utf-8")
    write_env_value(tmp_path / ".env", "DASHBOARD_PASSWORD_HASH", "h")
    assert "DASHBOARD_PASSWORD_HASH=h" in (tmp_path / ".env").read_text(encoding="utf-8")


@pytest.mark.parametrize(("key", "value"), [("bad key", "x"), ("OK", "line\nINJECTED=1"), ("OK", "a b")])
def test_write_env_value_rejects_unsafe_input(tmp_path, key, value):
    with pytest.raises(ValueError):
        write_env_value(tmp_path / ".env", key, value)


def test_set_password_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr("app.set_password.hash_password", lambda p: hash_password(p, n=FAST_N))
    env = tmp_path / ".env"
    set_password(env, "a long good password")
    settings = load_settings(env={}, env_file=env)
    assert verify_password("a long good password", settings.dashboard_password_hash)


def test_set_password_rejects_weak(tmp_path):
    with pytest.raises(ValueError, match="at least"):
        set_password(tmp_path / ".env", "short")
    assert not (tmp_path / ".env").exists() or ENV_KEY not in (tmp_path / ".env").read_text()
