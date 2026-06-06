"""Tests for tools/game_filter_tool.py."""

import pytest
from unittest.mock import MagicMock

from tools.game_filter_tool import GameFilterTool


class TestGameFilterTool:

    @pytest.fixture
    def mock_db(self):
        """A MagicMock GameDB whose filter_games returns mock ORM objects."""
        db = MagicMock()

        def make_game(name, price, review, tags):
            g = MagicMock()
            g.name = name
            g.price_cny = price
            g.review_score = review
            g.tags = ", ".join(tags)
            g.to_dict.return_value = {
                "name": name, "price": price,
                "review": review, "tags": tags,
            }
            return g

        db.filter_games.return_value = [
            make_game("Elden Ring", 298.0, 0.94, ["RPG", "开放世界", "魂类"]),
            make_game("Hades II", 108.0, 0.96, ["动作", "肉鸽"]),
        ]
        return db

    def test_empty_params_passes_through(self, mock_db):
        tool = GameFilterTool(mock_db)
        results = tool.run({})
        assert len(results) == 2
        mock_db.filter_games.assert_called_once()

    def test_max_price_forwarded(self, mock_db):
        tool = GameFilterTool(mock_db)
        tool.run({"max_price": 100})
        call_kwargs = mock_db.filter_games.call_args.kwargs
        assert call_kwargs["max_price"] == 100

    def test_min_review_forwarded(self, mock_db):
        tool = GameFilterTool(mock_db)
        tool.run({"min_review": 0.8})
        call_kwargs = mock_db.filter_games.call_args.kwargs
        assert call_kwargs["min_review"] == 0.8

    def test_tags_forwarded(self, mock_db):
        tool = GameFilterTool(mock_db)
        tool.run({"tags": ["RPG", "开放世界"]})
        call_kwargs = mock_db.filter_games.call_args.kwargs
        assert call_kwargs["tags"] == ["RPG", "开放世界"]

    def test_exclude_tags_forwarded(self, mock_db):
        tool = GameFilterTool(mock_db)
        tool.run({"exclude_tags": ["像素"]})
        call_kwargs = mock_db.filter_games.call_args.kwargs
        assert call_kwargs["exclude_tags"] == ["像素"]

    def test_is_multiplayer_forwarded(self, mock_db):
        tool = GameFilterTool(mock_db)
        tool.run({"is_multiplayer": True})
        call_kwargs = mock_db.filter_games.call_args.kwargs
        assert call_kwargs["is_multiplayer"] is True

    def test_limit_forwarded(self, mock_db):
        tool = GameFilterTool(mock_db)
        tool.run({"limit": 5})
        call_kwargs = mock_db.filter_games.call_args.kwargs
        assert call_kwargs["limit"] == 5

    def test_default_limit(self, mock_db):
        tool = GameFilterTool(mock_db)
        tool.run({})
        call_kwargs = mock_db.filter_games.call_args.kwargs
        assert call_kwargs["limit"] == 10

    def test_results_use_to_dict(self, mock_db):
        tool = GameFilterTool(mock_db)
        results = tool.run({})
        assert isinstance(results, list)
        assert "name" in results[0]
        assert "price" in results[0]

    def test_empty_db_results(self, mock_db):
        mock_db.filter_games.return_value = []
        tool = GameFilterTool(mock_db)
        results = tool.run({"max_price": 1})
        assert results == []
