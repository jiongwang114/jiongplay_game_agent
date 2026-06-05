"""SQLAlchemy data models for the Steam game recommendation system."""

from sqlalchemy import Column, Integer, String, Float, Boolean, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class Game(Base):
    """Steam game record stored in SQLite."""

    __tablename__ = "games"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    steam_appid: int = Column(Integer, unique=True, index=True, nullable=False)
    name: str = Column(String(256), nullable=False)
    description: str = Column(String(2048), nullable=False, default="")
    price_cny: float = Column(Float, default=0.0)          # 人民币定价，0 表示免费
    review_score: float = Column(Float, default=0.0)       # 好评率 0.0~1.0
    review_count: int = Column(Integer, default=0)          # 评价数量
    release_date: str = Column(String(32), default="")      # "YYYY-MM-DD"
    tags: str = Column(String(1024), default="")            # 逗号分隔 "RPG,开放世界,单机"
    is_multiplayer: bool = Column(Boolean, default=False)
    header_image: str = Column(String(512), default="")     # Steam 封面图 URL
    store_url: str = Column(String(512), default="")        # Steam 商店页面 URL


class User(Base):
    """Registered user account for authentication."""

    __tablename__ = "users"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    username: str = Column(String(64), unique=True, index=True, nullable=False)
    password_hash: str = Column(String(256), nullable=False)
    token: str = Column(String(128), unique=True, index=True, default="")   # auth token
    avatar_seed: str = Column(String(64), default="")                       # DiceBear seed
    created_at: str = Column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "avatar_seed": self.avatar_seed,
            "created_at": self.created_at,
        }


class Conversation(Base):
    """Persistent chat history per session."""

    __tablename__ = "conversations"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    session_id: str = Column(String(64), index=True, nullable=False)
    role: str = Column(String(16), nullable=False)         # "user" or "assistant"
    content: str = Column(String(8192), nullable=False)
    created_at: str = Column(String(32), default="")

    def __repr__(self) -> str:
        return f"<Game(id={self.id}, name='{self.name}', price={self.price_cny})>"

    def to_dict(self) -> dict:
        """Convert ORM object to a plain dict for tool responses."""
        return {
            "name": self.name,
            "steam_appid": self.steam_appid,
            "price": self.price_cny,
            "review": self.review_score,
            "review_count": self.review_count,
            "release_date": self.release_date,
            "tags": [t.strip() for t in self.tags.split(",") if t.strip()],
            "is_multiplayer": self.is_multiplayer,
            "description": self.description,
            "header_image": self.header_image,
            "store_url": self.store_url,
        }
