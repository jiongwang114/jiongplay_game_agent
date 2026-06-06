"""Tests for data_layer/auth.py — password hashing, tokens, and AuthManager."""

import pytest
from unittest.mock import MagicMock

from data_layer.auth import AuthManager, hash_password, verify_password, generate_token
from data_layer.schema import User


# =========================================================================
#  Pure functions — no DB needed
# =========================================================================

class TestHashPassword:

    def test_returns_salt_colon_hash_format(self):
        result = hash_password("test1234")
        parts = result.split(":")
        assert len(parts) == 2
        assert len(parts[0]) == 32  # salt is 32 hex chars (16 random bytes via secrets.token_hex(16))
        assert len(parts[1]) == 64  # SHA-256 hex digest

    def test_different_salts_each_call(self):
        a = hash_password("same_password")
        b = hash_password("same_password")
        assert a != b  # different salts → different outputs


class TestVerifyPassword:

    def test_correct_password_returns_true(self):
        stored = hash_password("mypassword")
        assert verify_password("mypassword", stored) is True

    def test_wrong_password_returns_false(self):
        stored = hash_password("mypassword")
        assert verify_password("wrongpassword", stored) is False

    def test_malformed_stored_string_returns_false(self):
        assert verify_password("anything", "not-a-valid-format") is False

    def test_empty_stored_string_returns_false(self):
        assert verify_password("anything", "") is False


class TestGenerateToken:

    def test_returns_32_char_hex(self):
        token = generate_token()
        assert len(token) == 32
        # Must be valid hex
        int(token, 16)

    def test_unique_tokens(self):
        tokens = {generate_token() for _ in range(100)}
        assert len(tokens) == 100  # all unique


# =========================================================================
#  AuthManager — with mock GameDB
# =========================================================================

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.create_user.return_value = None
    db.get_user_by_username.return_value = None  # no existing user by default
    db.get_user_by_token.return_value = None
    return db


@pytest.fixture
def auth(mock_db):
    return AuthManager(mock_db)


def _make_user(username="testuser", token=None):
    """Helper: create a real User object for testing."""
    u = User()
    u.id = 1
    u.username = username
    u.password_hash = hash_password("pass1234")
    u.token = token or generate_token()
    u.avatar_seed = "seed"
    u.created_at = "2026-01-01"
    return u


class TestAuthManagerRegister:

    def test_username_too_short(self, auth):
        _, error = auth.register("a", "pass1234")
        assert error is not None
        assert "用户名" in error

    def test_password_too_short(self, auth):
        _, error = auth.register("validuser", "ab")
        assert error is not None
        assert "密码" in error

    def test_duplicate_username(self, auth, mock_db):
        mock_db.get_user_by_username.return_value = _make_user("taken")
        _, error = auth.register("taken", "pass1234")
        assert error is not None
        assert "已被注册" in error

    def test_successful_registration(self, auth, mock_db):
        mock_db.get_user_by_username.return_value = None
        # AuthManager.register() calls create_user(username, pw_hash) then set_user_token(user, token)
        def _create(username, pw_hash):
            u = User()
            u.id = 1
            u.username = username
            u.password_hash = pw_hash
            u.token = ""
            u.avatar_seed = "seed"
            u.created_at = "2026-01-01"
            return u
        mock_db.create_user.side_effect = _create
        mock_db.set_user_token = MagicMock()

        user, error = auth.register("newuser", "pass1234")
        assert error is None
        assert user is not None
        assert user.username == "newuser"


class TestAuthManagerLogin:

    def test_user_not_found(self, auth, mock_db):
        mock_db.get_user_by_username.return_value = None
        _, error = auth.login("nobody", "pass1234")
        assert error is not None

    def test_wrong_password(self, auth, mock_db):
        user = _make_user("testuser")
        user.password_hash = hash_password("correct")
        mock_db.get_user_by_username.return_value = user
        _, error = auth.login("testuser", "wrongpass")
        assert error is not None
        assert "密码" in error

    def test_successful_login(self, auth, mock_db):
        user = _make_user("testuser")
        user.password_hash = hash_password("pass1234")
        mock_db.get_user_by_username.return_value = user
        result, error = auth.login("testuser", "pass1234")
        assert error is None
        assert result is not None
        assert result.token is not None


class TestAuthManagerToken:

    def test_get_user_by_token_valid(self, auth, mock_db):
        user = _make_user("testuser")
        mock_db.get_user_by_token.return_value = user
        result = auth.get_user_by_token("valid-token")
        assert result is not None
        assert result.username == "testuser"

    def test_get_user_by_token_invalid(self, auth, mock_db):
        mock_db.get_user_by_token.return_value = None
        result = auth.get_user_by_token("invalid-token")
        assert result is None


class TestAuthManagerLogout:

    def test_logout_clears_token(self, auth, mock_db):
        # logout looks up user by token first; need a valid user to succeed
        mock_db.get_user_by_token.return_value = _make_user("testuser")
        mock_db.set_user_token = MagicMock()
        ok = auth.logout("some-token")
        assert ok is True
        mock_db.set_user_token.assert_called_once()
