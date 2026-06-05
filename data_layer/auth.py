"""Simple authentication module — password hashing, token generation, user CRUD.

Uses SHA-256 + random salt for password hashing (sufficient for a local demo;
swap to bcrypt for production).  Tokens are random hex strings.
"""

import hashlib
import os
import secrets
import time
from typing import Optional

from data_layer.schema import User
from data_layer.sqlite_db import GameDB


# ---------------------------------------------------------------------------
#  Password helpers
# ---------------------------------------------------------------------------

def _salt() -> str:
    return secrets.token_hex(16)


def hash_password(password: str) -> str:
    """Return ``salt:hash_hex`` for storage."""
    salt = _salt()
    h = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}:{h}"


def verify_password(password: str, stored: str) -> bool:
    """Check *password* against a ``salt:hash`` string."""
    try:
        salt, expected = stored.split(":", 1)
    except ValueError:
        return False
    actual = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return secrets.compare_digest(actual, expected)


# ---------------------------------------------------------------------------
#  Token helpers
# ---------------------------------------------------------------------------

def generate_token() -> str:
    """Return a random 32‑char hex token."""
    return secrets.token_hex(16)


# ---------------------------------------------------------------------------
#  Auth operations (require a GameDB instance)
# ---------------------------------------------------------------------------

class AuthManager:
    """Thin wrapper around GameDB for authentication operations."""

    def __init__(self, db: GameDB):
        self._db = db

    def register(self, username: str, password: str) -> tuple[Optional[User], Optional[str]]:
        """
        Create a new user account.

        Returns ``(user, error_message)`` — exactly one will be non‑None.
        """
        username = username.strip()
        if not username or len(username) < 2:
            return None, "用户名至少需要 2 个字符"
        if not password or len(password) < 4:
            return None, "密码至少需要 4 个字符"

        existing = self._db.get_user_by_username(username)
        if existing:
            return None, f"用户名「{username}」已被注册"

        try:
            pw_hash = hash_password(password)
            token = generate_token()
            user = self._db.create_user(username, pw_hash)
            self._db.set_user_token(user, token)
            return user, None
        except Exception as e:
            return None, f"注册失败：{e}"

    def login(self, username: str, password: str) -> tuple[Optional[User], Optional[str]]:
        """
        Authenticate and return a fresh token.

        Returns ``(user, error_message)``.
        """
        user = self._db.get_user_by_username(username.strip())
        if not user:
            return None, "用户名或密码错误"

        if not verify_password(password, user.password_hash):
            return None, "用户名或密码错误"

        # Issue a new token on each login (invalidates previous tokens)
        token = generate_token()
        self._db.set_user_token(user, token)
        return user, None

    def get_user_by_token(self, token: str) -> Optional[User]:
        """Validate a token and return the user, or None."""
        if not token:
            return None
        return self._db.get_user_by_token(token)

    def logout(self, token: str) -> bool:
        """Clear the token for the associated user."""
        user = self.get_user_by_token(token)
        if user:
            self._db.set_user_token(user, "")
            return True
        return False
