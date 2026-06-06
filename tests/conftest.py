"""Shared test fixtures for the wangjiong_game_agent test suite."""

import pytest
from unittest.mock import MagicMock, AsyncMock


# ---------------------------------------------------------------------------
#  Sample data
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_games() -> list[dict]:
    """A small set of realistic game dicts used across tests."""
    return [
        {
            "name": "Elden Ring",
            "steam_appid": 1245620,
            "price": 298.0,
            "review": 0.94,
            "tags": ["RPG", "开放世界", "魂类", "单机"],
            "description": "艾尔登法环 — 宫崎英高与乔治·R·R·马丁联手打造",
            "store_url": "https://store.steampowered.com/app/1245620",
            "header_image": "https://cdn.cloudflare.steamstatic.com/steam/apps/1245620/header.jpg",
        },
        {
            "name": "Hades II",
            "steam_appid": 1145350,
            "price": 108.0,
            "review": 0.96,
            "tags": ["动作", "肉鸽", "独立", "单机"],
            "description": "冥界公主的 rogue-like 地牢冒险",
            "store_url": "https://store.steampowered.com/app/1145350",
            "header_image": "https://cdn.cloudflare.steamstatic.com/steam/apps/1145350/header.jpg",
        },
        {
            "name": "Stardew Valley",
            "steam_appid": 413150,
            "price": 48.0,
            "review": 0.98,
            "tags": ["模拟", "种田", "独立", "多人"],
            "description": "像素风农场模拟经营",
            "store_url": "https://store.steampowered.com/app/413150",
            "header_image": "https://cdn.cloudflare.steamstatic.com/steam/apps/413150/header.jpg",
        },
    ]


@pytest.fixture
def sample_settings() -> dict:
    """Typical user settings from the settings modal."""
    return {
        "budget": 100,
        "genres": ["RPG", "独立"],
        "platforms": ["单机"],
    }


# ---------------------------------------------------------------------------
#  Mock GameDB
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db(sample_games):
    """A MagicMock GameDB with filter_games and get_by_appid stubbed."""
    db = MagicMock()
    db.filter_games.return_value = _make_mock_games(sample_games)
    db.get_by_appid.side_effect = lambda appid: _find_mock_game(sample_games, appid)
    return db


def _make_mock_games(games: list[dict]) -> list:
    """Convert sample game dicts to mock ORM objects with a .to_dict() method."""
    results = []
    for g in games:
        mock = MagicMock()
        mock.name = g["name"]
        mock.steam_appid = g["steam_appid"]
        mock.price_cny = g["price"]
        mock.review_score = g["review"]
        mock.tags = ", ".join(g["tags"])
        mock.description = g["description"]
        mock.store_url = g["store_url"]
        mock.header_image = g["header_image"]
        mock.is_multiplayer = "多人" in g["tags"]
        mock.to_dict.return_value = g
        results.append(mock)
    return results


def _find_mock_game(games: list[dict], appid: int):
    """Return a mock ORM game by steam_appid, or None."""
    for g in _make_mock_games(games):
        if g.steam_appid == appid:
            return g
    return None


# ---------------------------------------------------------------------------
#  Mock LLMEngine
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm():
    """An AsyncMock LLMEngine whose .chat() returns controlled JSON."""
    llm = AsyncMock()
    # Default: return a valid "search" intent
    llm.chat.return_value = (
        '{"action": "search", "search_query": "开放世界 RPG", '
        '"filters": {"max_price": null, "min_review": null, "tags": ["RPG"], '
        '"exclude_tags": [], "is_multiplayer": null, "similar_to": "", "limit": 10}, '
        '"clarify_question": "", "reasoning": "用户描述了类型偏好"}'
    )
    llm.model = "test-model"
    return llm
