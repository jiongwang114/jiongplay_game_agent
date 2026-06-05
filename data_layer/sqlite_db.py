"""SQLite database operations for Steam games."""

import os
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data_layer.schema import Base, Game


class GameDB:
    """Relational database wrapper for game filtering and CRUD."""

    def __init__(self, db_path: Optional[str] = None):
        db_path = db_path or os.getenv("DB_PATH", "./data/sqlite/games.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        self._session_factory = None

    @property
    def session(self) -> Session:
        """Lazy session factory."""
        if self._session_factory is None:
            self._session_factory = Session(self.engine)
        return self._session_factory

    def init_tables(self) -> None:
        """Create all tables if they don't exist."""
        Base.metadata.create_all(self.engine)

    # ------------------------------------------------------------------
    #  CRUD
    # ------------------------------------------------------------------

    def upsert_game(self, game_dict: dict) -> Game:
        """Insert or update a game record (matched by steam_appid)."""
        existing = self.get_by_appid(game_dict["steam_appid"])
        if existing:
            for key, value in game_dict.items():
                setattr(existing, key, value)
            self.session.commit()
            return existing
        else:
            game = Game(**game_dict)
            self.session.add(game)
            self.session.commit()
            return game

    def get_by_appid(self, appid: int) -> Optional[Game]:
        """Return a single game by Steam App ID, or None."""
        return self.session.query(Game).filter(Game.steam_appid == appid).first()

    def get_all_for_vectorize(self) -> list[Game]:
        """Return all games — used to initialize the vector index."""
        return self.session.query(Game).all()

    # ------------------------------------------------------------------
    #  Filtering
    # ------------------------------------------------------------------

    def filter_games(
        self,
        max_price: Optional[float] = None,
        min_review: Optional[float] = None,
        tags: Optional[list[str]] = None,
        is_multiplayer: Optional[bool] = None,
        limit: int = 10,
    ) -> list[Game]:
        """
        Dynamic SQL filter.

        - *max_price*      — upper price bound (CNY)
        - *min_review*     — minimum review score (0.0–1.0)
        - *tags*           — list of tags that must ALL be present (AND logic)
        - *is_multiplayer* — if True, only multiplayer; if False, only single-player
        - *limit*          — max results
        """
        query = self.session.query(Game)

        if max_price is not None:
            query = query.filter(Game.price_cny <= max_price)
        if min_review is not None:
            query = query.filter(Game.review_score >= min_review)
        if is_multiplayer is not None:
            query = query.filter(Game.is_multiplayer == is_multiplayer)
        if tags:
            for tag in tags:
                query = query.filter(Game.tags.like(f"%{tag}%"))

        return query.order_by(Game.review_score.desc()).limit(limit).all()
