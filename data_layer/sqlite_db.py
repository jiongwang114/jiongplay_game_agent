"""SQLite database operations for Steam games."""

import os
from typing import Optional

from sqlalchemy import create_engine, text
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
        #  Migration: ensure users.token has a NON-UNIQUE index.
        #
        #  Earlier versions had ``unique=True`` on the token column, but
        #  multiple users must be allowed to have an empty token (logged-out
        #  state).  SQLAlchemy's ``create_all`` does NOT alter existing
        #  tables, so we drop the old unique index and recreate it as plain.
        # ------------------------------------------------------------------
        try:
            with self.engine.connect() as conn:
                conn.execute(text("DROP INDEX IF EXISTS ix_users_token"))
                conn.commit()
            with self.engine.connect() as conn:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_token ON users (token)"))
                conn.commit()
        except Exception:
            pass  # Table may not exist yet — harmless

        # ------------------------------------------------------------------
        #  Migration: add persistent user data columns to users table
        #  (steam_id, steam_profile_json, preferences_json, settings_json)
        # ------------------------------------------------------------------
        user_new_cols = [
            ("steam_id", "TEXT DEFAULT ''"),
            ("steam_profile_json", "TEXT DEFAULT ''"),
            ("preferences_json", "TEXT DEFAULT ''"),
            ("settings_json", "TEXT DEFAULT ''"),
        ]
        for col_name, col_def in user_new_cols:
            try:
                with self.engine.connect() as conn:
                    conn.execute(text(
                        f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
                    ))
                    conn.commit()
            except Exception:
                pass  # Column already exists — harmless

        # ------------------------------------------------------------------
        #  Migration: add user_id column to conversations table
        # ------------------------------------------------------------------
        try:
            with self.engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE conversations ADD COLUMN user_id INTEGER DEFAULT NULL"
                ))
                conn.commit()
        except Exception:
            pass  # Column already exists — harmless

        # Index on conversations.user_id for fast user-history queries
        try:
            with self.engine.connect() as conn:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_conversations_user_id ON conversations (user_id)"
                ))
                conn.commit()
        except Exception:
            pass

        # ------------------------------------------------------------------
        #  Migration: unique constraint on steam_id (one Steam ID per user)
        #  Partial index — only enforces uniqueness for non-empty steam_ids.
        # ------------------------------------------------------------------
        try:
            with self.engine.connect() as conn:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_steam_id_unique "
                    "ON users (steam_id) WHERE steam_id != ''"
                ))
                conn.commit()
        except Exception:
            pass  # May fail if duplicate steam_ids exist — app-level check is authoritative

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

    # [职责] 输入多维度筛选条件 → 动态 SQL 查询 → 返回匹配的游戏列表
    def filter_games(
        self,
        max_price: Optional[float] = None,
        min_review: Optional[float] = None,
        tags: Optional[list[str]] = None,
        exclude_tags: Optional[list[str]] = None,
        is_multiplayer: Optional[bool] = None,
        limit: int = 10,
    ) -> list[Game]:
        """
        Dynamic SQL filter.

        - *max_price*      — upper price bound (CNY)
        - *min_review*     — minimum review score (0.0–1.0)
        - *tags*           — list of tags that must ALL be present (AND logic)
        - *exclude_tags*   — [新增] list of tags that must NOT be present (排除条件)
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
        # [新增] 排除标签 — 用于处理"不要像素画风"等用户否定条件
        if exclude_tags:
            for tag in exclude_tags:
                query = query.filter(~Game.tags.like(f"%{tag}%"))

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

    # [职责] 输入 steam_id → 查询该 Steam ID 被哪个用户绑定 → 返回 User 或 None
    def get_user_by_steam_id(self, steam_id: str) -> Optional[User]:
        """Return the user who owns this Steam ID, or None if unclaimed."""
        if not steam_id:
            return None
        return self.session.query(User).filter(User.steam_id == steam_id).first()

    # ------------------------------------------------------------------
    #  User profile persistence (Steam / preferences / settings)
    # ------------------------------------------------------------------

    def save_user_steam_profile(self, user_id: int, steam_id: str, profile_json: str):
        """Persist Steam profile data to the user record."""
        user = self.session.query(User).filter(User.id == user_id).first()
        if user:
            user.steam_id = steam_id
            user.steam_profile_json = profile_json
            self.session.commit()

    def save_user_preferences(self, user_id: int, preferences_json: str):
        """Persist LLM-extracted preferences to the user record."""
        user = self.session.query(User).filter(User.id == user_id).first()
        if user:
            user.preferences_json = preferences_json
            self.session.commit()

    def save_user_settings(self, user_id: int, settings_json: str):
        """Persist user settings (budget, genres, platforms) to the user record."""
        user = self.session.query(User).filter(User.id == user_id).first()
        if user:
            user.settings_json = settings_json
            self.session.commit()

    # ------------------------------------------------------------------
    #  Conversation helpers
    # ------------------------------------------------------------------

    def add_conversation(self, session_id: str, role: str, content: str,
                         user_id: int = None):
        """Persist a single chat message, optionally tied to a user account."""
        import time
        msg = Conversation(
            session_id=session_id,
            user_id=user_id,
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

    def get_conversations_by_user(self, user_id: int, limit: int = 50) -> list[Conversation]:
        """
        Return recent messages for a user account, across all sessions.
        Used to restore chat history when user re-logs in.
        """
        rows = (
            self.session.query(Conversation)
            .filter(Conversation.user_id == user_id)
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
