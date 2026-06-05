"""Exact‑match game filter tool — SQL conditions against the local DB."""

from typing import Optional

from data_layer.sqlite_db import GameDB


class GameFilterTool:
    """
    Query games by structured filters: price, review score, tags, multiplayer.

    The Agent calls this tool when the user gives concrete constraints like
    "RPG under ¥60" or "multiplayer with 90%+ rating".
    """

    def __init__(self, db: GameDB):
        self._db = db

    def run(self, params: dict) -> list[dict]:
        """
        Execute a filter query.

        *params* may contain:
            max_price      — float, upper price bound (CNY)
            min_review     — float, minimum review score (0.0–1.0)
            tags           — list[str], tags that must ALL be present
            is_multiplayer — bool
            limit          — int (default 10)
        """
        games = self._db.filter_games(
            max_price=params.get("max_price"),
            min_review=params.get("min_review"),
            tags=params.get("tags"),
            is_multiplayer=params.get("is_multiplayer"),
            limit=params.get("limit", 10),
        )
        return [g.to_dict() for g in games]
