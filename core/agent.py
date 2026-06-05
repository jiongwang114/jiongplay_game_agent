"""Recommendation wizard Agent — intent routing, tool orchestration, streaming."""

import json
import os
import re
from pathlib import Path
from typing import AsyncGenerator, Optional

from core.llm_engine import LLMEngine
from core.memory.session_memory import SessionMemory
from core.memory.user_profile import UserProfile
from tools.game_filter_tool import GameFilterTool
from tools.semantic_search_tool import SemanticSearchTool
from tools.steam_store_tool import SteamStoreTool
from data_layer.sqlite_db import GameDB
from data_layer.vector_store import VectorStore


# ---------------------------------------------------------------------------
#  Intent‑detection keywords (lightweight, no LLM call needed for routing)
# ---------------------------------------------------------------------------

_GENRE_KEYWORDS = [
    "rpg", "fps", "策略", "模拟", "动作", "冒险", "休闲", "独立",
    "恐怖", "解谜", "竞速", "体育", "音乐", "角色扮演", "射击",
    "开放世界", "肉鸽", "rogue", "魂类", "类魂", "银河城",
    "种田", "建造", "生存", "潜行", "战棋", "卡牌",
]

_PRICE_KEYWORDS = ["便宜", "贵", "免费", "块", "元", "¥", "￥", "预算", "打折", "折扣"]
_SIMILAR_KEYWORDS = ["类似", "像", "类", "风格的", "一样的", "like", "相似"]


def _load_system_prompt() -> str:
    """Load the game expert prompt from file."""
    prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "game_expert_prompt.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "你是一位资深 Steam 游戏品鉴师。"  # fallback


# ---------------------------------------------------------------------------
#  AgentRunner
# ---------------------------------------------------------------------------

class AgentRunner:
    """
    Main entry point for the recommendation agent.

    On each user message it:
        1. Silently updates the user profile via LLM
        2. Determines intent (clarify vs. tool chain)
        3. Runs the appropriate tools
        4. Streams the LLM response back
    """

    def __init__(self):
        # Shared infrastructure
        self.llm = LLMEngine()
        self.memory = SessionMemory()
        self.profile = UserProfile(self.llm)
        self.db = GameDB()
        self.vector_store = VectorStore()

        # Tools
        self.filter_tool = GameFilterTool(self.db)
        self.semantic_tool = SemanticSearchTool(self.vector_store, self.db)
        self.steam_tool = SteamStoreTool(self.db)

        # Cached system prompt template
        self._system_prompt_template = _load_system_prompt()

    # ------------------------------------------------------------------
    #  Streaming entry point
    # ------------------------------------------------------------------

    async def run_stream(
        self,
        session_id: str,
        message: str,
    ) -> AsyncGenerator[str, None]:
        """
        Process a user message and yield LLM response tokens.

        Called by ``server.py``'s ``POST /chat`` handler.
        """
        # 1. Save user message to history
        self.memory.add(session_id, "user", message)

        # 2. Silently extract preferences (fire‑and‑forget, non‑blocking in spirit)
        await self.profile.extract_and_update(message)

        # 3. Decide: clarify or run tools
        if self._should_ask_clarification(message):
            # Not enough info — ask one follow‑up question
            tool_results = ""
        else:
            # Run the tool chain
            tool_context = await self._run_tool_chain(message)
            tool_results = self._format_tool_results(tool_context)

        # 4. Build the system prompt
        user_summary = self.profile.get_summary()
        system_prompt = self._system_prompt_template.replace(
            "{user_profile_summary}", user_summary or "暂无偏好数据"
        ).replace(
            "{tool_results}", tool_results or "（等待用户提供更多信息）"
        )

        # 5. Build context for the LLM
        history = self.memory.get(session_id)
        context = message

        # 6. Stream response
        response_chunks: list[str] = []
        async for token in self.llm.stream_chat(system_prompt, history, context):
            response_chunks.append(token)
            yield token

        # 7. Save assistant response to history
        full_response = "".join(response_chunks)
        self.memory.add(session_id, "assistant", full_response)

    # ------------------------------------------------------------------
    #  Intent detection
    # ------------------------------------------------------------------

    def _should_ask_clarification(self, message: str) -> bool:
        """
        Return True if the user message is too vague — we should ask a
        follow‑up question instead of running tools.

        Heuristic:
            - Very short messages (< 10 chars) with no genre / price keywords
            - Generic requests with no specific conditions
        """
        msg_lower = message.lower().strip()

        # Extremely short and no keywords → clarify
        if len(msg_lower) < 10:
            has_keyword = any(kw in msg_lower for kw in _GENRE_KEYWORDS + _PRICE_KEYWORDS)
            if not has_keyword:
                return True

        return False

    # ------------------------------------------------------------------
    #  Tool chain
    # ------------------------------------------------------------------

    async def _run_tool_chain(self, message: str) -> list[dict]:
        """
        Dispatch the message to the appropriate tool(s) and return results.

        Intent routing logic:
            - "类似XX" / semantic description → semantic_search_tool
            - concrete filters (price, tags, review) → game_filter_tool
            - "XX游戏多少钱" / price check → steam_store_tool
            - Combo: filter + semantic can both run
        """
        results: list[dict] = []
        msg_lower = message.lower()

        # --- Detect semantic search intent ---------------------------------
        has_similar = any(kw in msg_lower for kw in _SIMILAR_KEYWORDS)
        has_genre_desc = any(kw in msg_lower for kw in _GENRE_KEYWORDS) and not any(
            kw in msg_lower for kw in _PRICE_KEYWORDS
        )

        if has_similar or has_genre_desc:
            semantic_results = self.semantic_tool.run(message)
            results.extend(semantic_results)

        # --- Detect filter intent ------------------------------------------
        has_price = any(kw in msg_lower for kw in _PRICE_KEYWORDS)
        has_multi = any(kw in msg_lower for kw in ["多人", "联机", "和朋友", "在线", "单机"])
        has_review = any(kw in msg_lower for kw in ["好评", "评分", "口碑"])

        if has_price or has_multi or has_review:
            params: dict = {"limit": 10}

            # Price extraction
            price_match = re.search(r"(\d+)\s*(?:块|元|¥|￥)", message)
            if price_match:
                params["max_price"] = float(price_match.group(1))
            elif "免费" in msg_lower:
                params["max_price"] = 0.0
            elif "便宜" in msg_lower:
                params["max_price"] = 50.0

            # Multiplayer detection
            if any(kw in msg_lower for kw in ["多人", "联机", "和朋友", "在线"]):
                params["is_multiplayer"] = True
            elif "单机" in msg_lower:
                params["is_multiplayer"] = False

            # Review threshold
            if any(kw in msg_lower for kw in ["好评", "口碑"]):
                params["min_review"] = 0.8

            filter_results = self.filter_tool.run(params)
            results.extend(filter_results)

        # --- Detect price‑check intent -------------------------------------
        price_check_match = re.search(r"(.+?)(?:多少钱|价格|什么价|贵不贵|打折)", message)
        if price_check_match:
            game_name = price_check_match.group(1).strip()
            if game_name and len(game_name) > 1:
                steam_result = await self.steam_tool.run(game_name)
                if "error" not in steam_result:
                    results.append(steam_result)

        # --- Fallback: if no tool matched, run semantic search anyway -------
        if not results:
            semantic_results = self.semantic_tool.run(message)
            results.extend(semantic_results)

        # Deduplicate by name
        seen: set[str] = set()
        unique: list[dict] = []
        for r in results:
            name = r.get("name", "")
            if name and name not in seen:
                seen.add(name)
                unique.append(r)

        return unique[:5]

    # ------------------------------------------------------------------
    #  Formatting
    # ------------------------------------------------------------------

    def _format_tool_results(self, results: list[dict]) -> str:
        """Convert tool results into a compact text block for the LLM."""
        if not results:
            return ""

        lines = ["以下是为用户检索到的游戏："]
        for i, g in enumerate(results, 1):
            tags_str = ", ".join(g.get("tags", [])) if g.get("tags") else "无标签"
            price_str = f"¥{g['price']}" if g.get("price") else "免费"
            review_str = f"好评率 {int(g.get('review', 0) * 100)}%" if g.get("review") else "暂无评价"
            lines.append(
                f"{i}. **{g['name']}** — {price_str} — {review_str} — 标签：{tags_str}\n"
                f"   简介：{g.get('description', '暂无简介')[:120]}\n"
                f"   链接：{g.get('store_url', '')}"
            )

        return "\n".join(lines)
