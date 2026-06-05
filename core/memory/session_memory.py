"""In‑memory conversation history per session (lost on restart)."""

from typing import Optional


class SessionMemory:
    """
    Stores chat history keyed by ``session_id``.

    This is intentionally in‑memory — for an MVP there's no need to persist
    conversations across restarts.  Each session stores up to *max_turns*
    user/assistant pairs.
    """

    def __init__(self, max_turns: int = 10):
        self._store: dict[str, list[dict]] = {}
        self.max_turns = max_turns

    def add(self, session_id: str, role: str, content: str) -> None:
        """
        Append a message to the session history.

        *role* should be ``"user"`` or ``"assistant"``.
        """
        if session_id not in self._store:
            self._store[session_id] = []
        self._store[session_id].append({"role": role, "content": content})

        # Enforce turn limit (keep the most recent)
        max_messages = self.max_turns * 2  # user + assistant per turn
        if len(self._store[session_id]) > max_messages:
            self._store[session_id] = self._store[session_id][-max_messages:]

    def get(self, session_id: str, max_turns: Optional[int] = None) -> list[dict]:
        """
        Return recent messages formatted for the LLM.

        Returns a list of ``{"role": …, "content": …}`` dicts.
        """
        max_turns = max_turns or self.max_turns
        history = self._store.get(session_id, [])
        return history[-(max_turns * 2):]

    def clear(self, session_id: str) -> None:
        """Remove all history for a session."""
        self._store.pop(session_id, None)
