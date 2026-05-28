"""
Tests for app.py routes.

Auth is tested by patching auth._verify_token so no real Firebase calls
are made. The autouse fixture bypasses auth on all non-auth tests
by patching require_auth to be a no-op.
"""

from unittest.mock import MagicMock, patch

import pytest

from app import app as flask_app


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def no_db_pool():
    """DB pool returns None for all tests by default."""
    with patch("app.get_pool", return_value=None):
        yield


# ---------------------------------------------------------------------------
# Health / infrastructure
# ---------------------------------------------------------------------------

def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert "timestamp" in data


def test_health_db_unconfigured(client):
    res = client.get("/health/db")
    assert res.status_code == 503
    assert res.get_json()["status"] == "unconfigured"


def test_health_db_ok(client):
    mock_conn = MagicMock()
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__enter__ = lambda s: mock_conn
    mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.get_pool", return_value=mock_pool):
        res = client.get("/health/db")

    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"
    mock_conn.execute.assert_called_once_with("SELECT 1")


def test_health_db_error(client):
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__enter__ = MagicMock(
        side_effect=Exception("connection refused")
    )
    mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.get_pool", return_value=mock_pool):
        res = client.get("/health/db")

    assert res.status_code == 503
    assert res.get_json()["status"] == "error"


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

def test_index(client):
    res = client.get("/")
    assert res.status_code == 200
    assert res.get_json()["message"] == "webapp-accounts-reconciller API"


def test_hello(client):
    res = client.get("/hello")
    assert res.status_code == 200
    assert res.get_json()["message"] == "Hello, world!"


def test_404(client):
    res = client.get("/nonexistent")
    assert res.status_code == 404
    assert res.get_json()["error"] == "Not found"


# ---------------------------------------------------------------------------
# Protected API — /api/me
# No token → 401
# ---------------------------------------------------------------------------

def test_api_me_no_token(client):
    res = client.get("/api/me")
    assert res.status_code == 401
    assert res.get_json()["error"] == "Authentication required"


def test_api_me_invalid_token(client):
    with patch("auth._verify_token", side_effect=Exception("bad token")):
        res = client.get("/api/me", headers={"Authorization": "Bearer bad.token.here"})
    assert res.status_code == 401


def test_api_me_valid_token(client):
    # Firebase decoded tokens use 'uid' not 'sub'
    fake_claims = {"uid": "user-uuid-123", "email": "user@example.com"}
    with patch("auth._verify_token", return_value=fake_claims):
        res = client.get("/api/me", headers={"Authorization": "Bearer valid.token.here"})

    assert res.status_code == 200
    data = res.get_json()
    assert data["user_id"] == "user-uuid-123"
    assert data["email"] == "user@example.com"


# ---------------------------------------------------------------------------
# Auth routes — /auth/me
# ---------------------------------------------------------------------------

def test_auth_me_no_token(client):
    res = client.get("/auth/me")
    assert res.status_code == 401


def test_auth_me_valid_token(client):
    fake_claims = {"uid": "user-uuid-456", "email": "web@example.com", "role": "branch"}
    with patch("auth._verify_token", return_value=fake_claims):
        res = client.get("/auth/me", headers={"Authorization": "Bearer valid.token.here"})

    assert res.status_code == 200
    data = res.get_json()
    assert data["user_id"] == "user-uuid-456"
    assert data["role"] == "branch"


# ---------------------------------------------------------------------------
# Auth routes — /auth/login serves the Firebase login page
# ---------------------------------------------------------------------------

def test_auth_login_renders_page(client):
    """GET /auth/login should return 200 with the Firebase login HTML."""
    with patch("auth.FIREBASE_PROJECT_ID", "gcashmatcher"):
        res = client.get("/auth/login")
    assert res.status_code == 200
    assert b"firebase" in res.data.lower()


# ---------------------------------------------------------------------------
# Auth routes — /auth/callback verifies Firebase token
# ---------------------------------------------------------------------------

def test_auth_callback_missing_token(client):
    res = client.post("/auth/callback", json={})
    assert res.status_code == 400
    assert res.get_json()["error"] == "Missing idToken"


def test_auth_callback_invalid_token(client):
    with patch("auth._verify_token", side_effect=Exception("token invalid")):
        res = client.post("/auth/callback", json={"idToken": "bad.token"})
    assert res.status_code == 401


def test_auth_callback_valid_token(client):
    fake_claims = {"uid": "uid-abc", "email": "user@example.com", "role": "branch"}
    with (
        patch("auth._verify_token", return_value=fake_claims),
        patch("auth._ensure_custom_claims"),
    ):
        res = client.post("/auth/callback", json={"idToken": "valid.token"})
    assert res.status_code == 200
    assert res.get_json()["ok"] is True


# ---------------------------------------------------------------------------
# Auth routes — /auth/logout clears session
# ---------------------------------------------------------------------------

def test_auth_logout_clears_session(client):
    with client.session_transaction() as sess:
        sess["firebase_id_token"] = "some-token"

    res = client.get("/auth/logout")

    # Should redirect to /auth/login
    assert res.status_code == 302
    assert "/auth/login" in res.headers["Location"]

    with client.session_transaction() as sess:
        assert "firebase_id_token" not in sess
