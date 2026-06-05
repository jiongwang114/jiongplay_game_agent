"""Semantic (vector) search tool — natural‑language → game list."""

from data_layer.sqlite_db import GameDB
from data_layer.vector_store import VectorStore


class SemanticSearchTool:
    """
    Search games by meaning, not keywords.

    The Agent calls this when the user describes a *vibe* or concept:
    "类似黑帝斯的游戏", "开放世界探索", "治愈系种田游戏".
    """

    def __init__(self, vector_store: VectorStore, db: GameDB):
        self._vs = vector_store
        self._db = db

    def run(self, query: str, top_k: int = 5) -> list[dict]:
        """
        *query*  — natural‑language search string
        *top_k*  — max results

        Returns a list of game dicts (same format as GameFilterTool).
        """
        appids = self._vs.search(query, top_k=top_k)
        results = []
        for appid in appids:
            game = self._db.get_by_appid(appid)
            if game:
                results.append(game.to_dict())
        return results
