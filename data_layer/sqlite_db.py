"""SQLite database operations for Steam games."""

import os
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data_layer.schema import Base, Conversation, Game, User


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

    # ------------------------------------------------------------------
    #  User helpers
    # ------------------------------------------------------------------

    def create_user(self, username: str, password_hash: str, avatar_seed: str = "") -> User:
        """Insert a new user. Raises on duplicate username."""
        import time
        user = User(
            username=username,
            password_hash=password_hash,
            avatar_seed=avatar_seed or username,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.session.add(user)
        self.session.commit()
        return user

    def get_user_by_username(self, username: str) -> Optional[User]:
        return self.session.query(User).filter(User.username == username).first()

    def get_user_by_token(self, token: str) -> Optional[User]:
        return self.session.query(User).filter(User.token == token).first()

    def set_user_token(self, user: User, token: str):
        user.token = token
        self.session.commit()

    # ------------------------------------------------------------------
    #  Conversation helpers
    # ------------------------------------------------------------------

    def add_conversation(self, session_id: str, role: str, content: str):
        """Persist a single chat message."""
        import time
        msg = Conversation(
            session_id=session_id,
            role=role,
            content=content,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.session.add(msg)
        self.session.commit()

    def get_conversations(self, session_id: str, limit: int = 20) -> list[Conversation]:
        """Return recent messages for a session, oldest first."""
        rows = (
            self.session.query(Conversation)
            .filter(Conversation.session_id == session_id)
            .order_by(Conversation.id.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))

    def clear_conversations(self, session_id: str):
        """Delete all messages for a session."""
        self.session.query(Conversation).filter(
            Conversation.session_id == session_id
        ).delete()
        self.session.commit()
