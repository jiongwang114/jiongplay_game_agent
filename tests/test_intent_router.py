"""Tests for core/intent_router.py — keyword + LLM intent detection."""

import pytest
from unittest.mock import AsyncMock

from core.intent_router import IntentRouter, format_user_settings


# =========================================================================
#  format_user_settings
# =========================================================================

class TestFormatUserSettings:

    def test_empty_settings(self):
        assert format_user_settings({}) == ""

    def test_budget_only(self):
        result = format_user_settings({"budget": 100})
        assert "预算上限 100 元" in result

    def test_genres_only(self):
        result = format_user_settings({"genres": ["RPG", "FPS"]})
        assert "RPG、FPS" in result

    def test_platforms_only(self):
        result = format_user_settings({"platforms": ["单机"]})
        assert "单机" in result

    def test_all_fields(self):
        result = format_user_settings({
            "budget": 200, "genres": ["RPG"], "platforms": ["多人"]
        })
        assert "200 元" in result
        assert "RPG" in result
        assert "多人" in result

    def test_empty_lists_return_empty_string(self):
        result = format_user_settings({"genres": [], "platforms": []})
        assert result == ""


# =========================================================================
#  Keyword Intent Detection
# =========================================================================

@pytest.fixture
def router():
    """An IntentRouter with a mock LLM (not used in keyword tests)."""
    llm = AsyncMock()
    return IntentRouter(llm)


class TestKeywordIntentDetect:

    # --- Genre keywords ---
    def test_genre_triggers_search(self, router):
        intent = router._keyword_intent_detect("推荐几个RPG游戏", {})
        assert intent is not None
        assert intent["action"] in ("search", "mixed")
        assert intent["source"] == "keyword"

    def test_chinese_genre_triggers_search(self, router):
        intent = router._keyword_intent_detect("有没有策略类游戏", {})
        assert intent is not None

    # --- Price keywords ---
    def test_price_extraction_yuan(self, router):
        intent = router._keyword_intent_detect("100块以内的游戏", {})
        assert intent is not None
        assert intent["filters"]["max_price"] == 100.0

    def test_price_extraction_free(self, router):
        intent = router._keyword_intent_detect("免费游戏", {})
        assert intent is not None
        assert intent["filters"]["max_price"] == 0.0

    def test_cheap_defaults_to_50(self, router):
        intent = router._keyword_intent_detect("推荐点便宜的游戏", {})
        assert intent is not None
        assert intent["filters"]["max_price"] == 50.0

    # --- Similar keywords ---
    def test_similar_triggers_search(self, router):
        intent = router._keyword_intent_detect("类似黑帝斯的游戏", {})
        assert intent is not None
        assert intent["action"] in ("search", "mixed")

    # --- Multiplayer ---
    def test_multiplayer_detection(self, router):
        intent = router._keyword_intent_detect("和朋友一起玩的联机游戏", {})
        assert intent is not None
        assert intent["filters"]["is_multiplayer"] is True

    def test_single_player_detection(self, router):
        intent = router._keyword_intent_detect("好玩的单机游戏", {})
        assert intent is not None
        assert intent["filters"]["is_multiplayer"] is False

    # --- Review ---
    def test_high_rating_sets_review_threshold(self, router):
        intent = router._keyword_intent_detect("好评如潮的RPG", {})
        assert intent is not None
        assert intent["filters"]["min_review"] == 0.8

    # --- Exclusion keywords ---
    def test_exclusion_returns_none(self, router):
        """Exclusion keywords like '不要' mean we escalate to LLM."""
        intent = router._keyword_intent_detect("不要像素画风的游戏", {})
        assert intent is None

    def test_exclusion_bie_returns_none(self, router):
        intent = router._keyword_intent_detect("别给我推荐魂类", {})
        assert intent is None

    # --- Vague messages ---
    def test_no_keywords_returns_none(self, router):
        intent = router._keyword_intent_detect("推荐个好玩的", {})
        assert intent is None

    def test_too_short_returns_none(self, router):
        intent = router._keyword_intent_detect("推荐", {})
        assert intent is None

    # --- Price check ---
    def test_price_check_action(self, router):
        intent = router._keyword_intent_detect("艾尔登法环多少钱", {})
        assert intent is not None
        assert intent["action"] == "price_check"

    # --- Settings fallback ---
    def test_settings_genres_used_as_tags(self, router):
        intent = router._keyword_intent_detect("便宜的RPG", {"genres": ["策略"]})
        assert intent is not None
        assert "策略" in intent["filters"]["tags"]

    # --- Combined signals: genre + price → filter (not mixed, since no "similar") ---
    def test_genre_and_price_is_filter(self, router):
        intent = router._keyword_intent_detect("50元以内的RPG", {})
        assert intent is not None
        assert intent["action"] == "filter"

    # --- Filler words stripped ---
    def test_filler_words_removed_from_query(self, router):
        intent = router._keyword_intent_detect("有没有推荐的好玩的开放世界游戏", {})
        assert intent is not None
        query = intent["search_query"]
        for filler in ["有没有", "推荐"]:
            assert filler not in query


# =========================================================================
#  LLM Intent Detection
# =========================================================================

class TestLLMIntentDetect:

    @pytest.fixture
    def llm(self):
        return AsyncMock()

    @pytest.fixture
    def router(self, llm):
        return IntentRouter(llm)

    @pytest.mark.asyncio
    async def test_valid_json_parsed(self, router, llm):
        llm.chat.return_value = (
            '{"action": "search", "search_query": "开放世界 RPG", '
            '"filters": {"max_price": null, "min_review": null, "tags": [], '
            '"exclude_tags": [], "is_multiplayer": null, "similar_to": "", "limit": 10}, '
            '"clarify_question": "", "reasoning": "user wants open world"}'
        )
        intent = await router._llm_intent_detect("开放世界", {}, "")
        assert intent["action"] == "search"
        assert intent["source"] == "llm"
        assert intent["search_query"] == "开放世界 RPG"

    @pytest.mark.asyncio
    async def test_clarify_action(self, router, llm):
        llm.chat.return_value = (
            '{"action": "clarify", "search_query": "", '
            '"filters": {"max_price": null, "min_review": null, "tags": [], '
            '"exclude_tags": [], "is_multiplayer": null, "similar_to": "", "limit": 10}, '
            '"clarify_question": "你喜欢什么类型的游戏？", "reasoning": "too vague"}'
        )
        intent = await router._llm_intent_detect("推荐", {}, "")
        assert intent["action"] == "clarify"
        assert "什么类型" in intent["clarify_question"]

    @pytest.mark.asyncio
    async def test_malformed_json_falls_back(self, router, llm):
        llm.chat.return_value = "not valid json at all {{{"
        # With no keywords in the message, should fall back to clarify
        intent = await router._llm_intent_detect("推荐个好玩的游戏吧", {}, "")
        assert intent["action"] == "clarify"
        assert intent["source"] in ("keyword_fallback",)

    @pytest.mark.asyncio
    async def test_markdown_json_stripped(self, router, llm):
        llm.chat.return_value = (
            '```json\n'
            '{"action": "filter", "search_query": "便宜RPG", '
            '"filters": {"max_price": 50, "min_review": 0.8, "tags": ["RPG"], '
            '"exclude_tags": [], "is_multiplayer": null, "similar_to": "", "limit": 5}, '
            '"clarify_question": "", "reasoning": "budget conscious"}\n'
            '```'
        )
        intent = await router._llm_intent_detect("便宜的RPG", {}, "")
        assert intent["action"] == "filter"
        assert intent["filters"]["max_price"] == 50
        assert intent["filters"]["min_review"] == 0.8

    @pytest.mark.asyncio
    async def test_invalid_action_forced_to_clarify(self, router, llm):
        llm.chat.return_value = (
            '{"action": "invalid_action", "search_query": "", '
            '"filters": {}, "clarify_question": "", "reasoning": ""}'
        )
        intent = await router._llm_intent_detect("test", {}, "")
        assert intent["action"] == "clarify"

    @pytest.mark.asyncio
    async def test_missing_filters_defaults(self, router, llm):
        llm.chat.return_value = '{"action": "search", "search_query": "test"}'
        intent = await router._llm_intent_detect("test", {}, "")
        assert intent["action"] == "search"
        assert intent["filters"]["tags"] == []
        assert intent["filters"]["exclude_tags"] == []


# =========================================================================
#  Detect (integrated progressive detection)
# =========================================================================

class TestDetect:

    @pytest.fixture
    def llm(self):
        return AsyncMock()

    @pytest.fixture
    def router(self, llm):
        return IntentRouter(llm)

    @pytest.mark.asyncio
    async def test_keyword_path_skips_llm(self, router, llm):
        """When keywords are clear, should NOT call LLM."""
        intent = await router.detect("100块以内的RPG游戏", {}, "")
        assert intent["source"] == "keyword"
        llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_ambiguous_escalates_to_llm(self, router, llm):
        """When no keywords match, should call LLM."""
        llm.chat.return_value = (
            '{"action": "clarify", "search_query": "", '
            '"filters": {"max_price": null, "min_review": null, "tags": [], '
            '"exclude_tags": [], "is_multiplayer": null, "similar_to": "", "limit": 10}, '
            '"clarify_question": "你想要什么类型的？", "reasoning": "vague"}'
        )
        intent = await router.detect("推荐个好玩的游戏吧", {}, "")
        assert intent["source"] == "llm"
        llm.chat.assert_called_once()
