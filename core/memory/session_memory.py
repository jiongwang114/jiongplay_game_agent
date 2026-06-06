"""Conversation history — in‑memory cache backed by SQLite for persistence."""

from typing import Optional


class SessionMemory:
    """
    Stores chat history keyed by ``session_id``.

    Uses an in‑memory cache for speed and writes through to SQLite so
    history survives server restarts.
    """

    def __init__(self, db=None, max_turns: int = 10):
        """
        *db* — a ``GameDB`` instance (optional).  When provided, messages
               are persisted to the ``conversations`` table.
        *max_turns* — how many turns to keep in context.
        """
        self._store: dict[str, list[dict]] = {}
        self._db = db
        self.max_turns = max_turns

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    def _load_from_db(self, session_id: str) -> list[dict]:
        """Restore recent history from SQLite (if db available)."""
        if not self._db:
            return []
        try:
            rows = self._db.get_conversations(session_id, limit=self.max_turns * 2)
            return [{"role": r.role, "content": r.content} for r in rows]
        except Exception:
            return []

    def _ensure_loaded(self, session_id: str) -> None:
        """Load history from DB if this session hasn't been initialised yet."""
        if session_id not in self._store:
            self._store[session_id] = self._load_from_db(session_id)

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def add(self, session_id: str, role: str, content: str, user_id: int = None) -> None:
        """
        Append a message to the session history.

        *role* should be ``"user"`` or ``"assistant"``.
        *user_id* — when provided, the message is linked to that user account
                    so it can be restored across sessions.
        """
        self._ensure_loaded(session_id)
        self._store[session_id].append({"role": role, "content": content})

        # Enforce turn limit (keep the most recent)
        max_messages = self.max_turns * 2  # user + assistant per turn
        if len(self._store[session_id]) > max_messages:
            self._store[session_id] = self._store[session_id][-max_messages:]

        # Persist to DB
        if self._db:
            try:
                self._db.add_conversation(session_id, role, content, user_id=user_id)
            except Exception:
                pass  # DB write failures are non‑fatal

    def get(self, session_id: str, max_turns: Optional[int] = None) -> list[dict]:
        """
        Return recent messages formatted for the LLM.

        Returns a list of ``{"role": …, "content": …}`` dicts.
        Loads from DB on first access if not already cached.
        """
        max_turns = max_turns or self.max_turns
        self._ensure_loaded(session_id)
        history = self._store.get(session_id, [])
        return history[-(max_turns * 2):]

    def load_user_history(self, user_id: int, limit: int = 50) -> list[dict]:
        """
        Load conversation history for a user from the database (across all sessions).

        Returns a list of ``{"role": …, "content": …}`` dicts for frontend display.
        Does NOT modify the in‑memory cache — callers should call ``add()`` to
        re-populate the current session if they want the LLM to use it.
        """
        if not self._db:
            return []
        try:
            rows = self._db.get_conversations_by_user(user_id, limit=limit)
            return [{"role": r.role, "content": r.content} for r in rows]
        except Exception:
            return []

    def clear(self, session_id: str) -> None:
        """Remove all history for a session (memory + DB)."""
        self._store.pop(session_id, None)
        if self._db:
            try:
                self._db.clear_conversations(session_id)
            except Exception:
                pass
