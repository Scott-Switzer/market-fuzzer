from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.app import (
    _arena_session_secret,
    _cached_execution_store,
    _execution_store,
    app,
)
from app.execution_arena import CHALLENGE_ID
from app.execution_store import ArenaStore


@pytest.fixture(autouse=True)
def clear_execution_store_cache(monkeypatch):
    _cached_execution_store.cache_clear()
    monkeypatch.delenv("ARENA_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("ARENA_SESSION_SECRET", raising=False)
    yield
    _cached_execution_store.cache_clear()


def test_test_auth_bypass_requires_starlette_test_client_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("ARENA_TEST_AUTH", "1")

    trusted_test_client = TestClient(app)
    trusted = trusted_test_client.get(
        f"/api/arena/execution/challenges/{CHALLENGE_ID}/evidence",
        headers={"X-Test-Role": "instructor", "X-Test-User": "test-instructor"},
    )
    assert trusted.status_code == 409

    remote_like_client = TestClient(
        app,
        base_url="https://arena.example",
        client=("203.0.113.10", 41_000),
    )
    rejected = remote_like_client.get(
        f"/api/arena/execution/challenges/{CHALLENGE_ID}/evidence",
        headers={"X-Test-Role": "instructor", "X-Test-User": "remote-attacker"},
    )
    assert rejected.status_code == 401


def test_demo_cookies_are_insecure_only_for_verified_or_explicit_local_demo(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "cookies.sqlite3"))
    monkeypatch.setenv("ARENA_DEMO_AUTH", "1")

    local_response = TestClient(app).post("/api/arena/demo-session", json={"role": "student"})
    assert local_response.status_code == 200
    local_cookies = local_response.headers.get_list("set-cookie")
    assert len(local_cookies) == 2
    assert all("httponly" in cookie.lower() for cookie in local_cookies)
    assert all("samesite=lax" in cookie.lower() for cookie in local_cookies)
    assert all("secure" not in cookie.lower() for cookie in local_cookies)

    remote_response = TestClient(
        app,
        base_url="http://arena.example",
        client=("203.0.113.11", 41_001),
    ).post("/api/arena/demo-session", json={"role": "student"})
    assert remote_response.status_code == 200
    assert all("secure" in cookie.lower() for cookie in remote_response.headers.get_list("set-cookie"))

    monkeypatch.setenv("ARENA_COOKIE_SECURE", "1")
    forced_secure = TestClient(app).post("/api/arena/demo-session", json={"role": "student"})
    assert forced_secure.status_code == 200
    assert all("secure" in cookie.lower() for cookie in forced_secure.headers.get_list("set-cookie"))

    monkeypatch.setenv("ARENA_COOKIE_SECURE", "0")
    remote_override = TestClient(
        app,
        base_url="http://arena.example",
        client=("203.0.113.12", 41_002),
    ).post("/api/arena/demo-session", json={"role": "student"})
    assert remote_override.status_code == 200
    assert all("secure" in cookie.lower() for cookie in remote_override.headers.get_list("set-cookie"))

    monkeypatch.setenv("ARENA_COOKIE_SECURE", "invalid")
    with pytest.raises(RuntimeError, match="must be either 0 or 1"):
        TestClient(app).post("/api/arena/demo-session", json={"role": "student"})


def test_session_secret_is_random_for_demo_and_required_outside_demo(monkeypatch) -> None:
    monkeypatch.setenv("ARENA_DEMO_AUTH", "1")
    first = _arena_session_secret()
    assert first == _arena_session_secret()
    assert len(first) == 32
    assert first != b"local-demo-not-for-production"

    monkeypatch.setenv("ARENA_DEMO_AUTH", "0")
    with pytest.raises(RuntimeError, match="required outside local demo mode"):
        _arena_session_secret()

    monkeypatch.setenv("ARENA_SESSION_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        _arena_session_secret()


def test_execution_store_cache_uses_resolved_path_and_is_bounded(tmp_path, monkeypatch) -> None:
    calls: list[Path] = []
    original = ArenaStore.ensure_default_challenge

    def count_default_challenge(self: ArenaStore, challenge_id: str, hidden_worlds: list[str]) -> None:
        calls.append(self.path)
        original(self, challenge_id, hidden_worlds)

    monkeypatch.setattr(ArenaStore, "ensure_default_challenge", count_default_challenge)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARENA_DB_PATH", "state/../first.sqlite3")
    first = _execution_store()

    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "first.sqlite3"))
    same = _execution_store()
    assert same is first
    assert same.challenge(CHALLENGE_ID)["challenge_id"] == CHALLENGE_ID

    monkeypatch.setenv("ARENA_DB_PATH", str(tmp_path / "second.sqlite3"))
    second = _execution_store()
    assert second is not first
    assert second.challenge(CHALLENGE_ID)["challenge_id"] == CHALLENGE_ID

    assert calls == [tmp_path / "first.sqlite3", tmp_path / "second.sqlite3"]
    cache_info = _cached_execution_store.cache_info()
    assert cache_info.currsize == 2
    assert cache_info.maxsize == 8
