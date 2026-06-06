"""Recommendation wizard Agent — intent routing, tool orchestration, streaming."""

import asyncio
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
        self.db = GameDB()
        self.memory = SessionMemory(db=self.db)
        self.profile = UserProfile(self.llm)
        self.profile._db = self.db  # Enable DB persistence for preferences
        self.vector_store = VectorStore()

        # Tools
        self.filter_tool = GameFilterTool(self.db)
        self.semantic_tool = SemanticSearchTool(self.vector_store, self.db)
        self.steam_tool = SteamStoreTool(self.db)

        # Cached system prompt template
        self._system_prompt_template = _load_system_prompt()

        # Cache structured tool results per session for frontend card rendering
        self._tool_result_cache: dict[str, list[dict]] = {}

        # Per‑session agent status for the sidebar status panel
        # Tracks phase transitions: idle → analyzing → searching → generating → done
        self._status: dict[str, dict] = {}

        # Per‑session Steam profile data (populated by /api/sync-steam)
        self._steam_profiles: dict[str, dict] = {}

        # Per‑session linked user_id — enables persistence across re-logins
        self._session_users: dict[str, int] = {}

        # SSE status push — one asyncio.Queue per session
        self._status_queues: dict[str, "asyncio.Queue"] = {}

    # ------------------------------------------------------------------
    #  Status helpers (for the sidebar AI status panel)
    # ------------------------------------------------------------------

    def _init_status(self, session_id: str) -> dict:
        """Create or return the status dict for a session."""
        if session_id not in self._status:
            # Check if this session has Steam data synced
            steam = self._steam_profiles.get(session_id, {})
            data_src = "Steam 在线同步" if steam else "本地数据库"
            initial_thought = ("已分析你的 Steam 游戏库，等待你的提问..."
                               if steam else "等待了解你的游戏喜好...")

            self._status[session_id] = {
                "data_source": data_src,
                "preference_bias": "",
                "confidence": 50,
                "agent_thought": initial_thought,
                "phase": "idle",
            }
        return self._status[session_id]

    def _update_status(self, session_id: str, **kwargs):
        """Update one or more fields in the session's status dict and push via SSE."""
        st = self._init_status(session_id)
        st.update(kwargs)

        # Push to SSE queue if anyone is listening
        q = self._status_queues.get(session_id)
        if q:
            try:
                q.put_nowait(dict(st))
            except asyncio.QueueFull:
                pass  # drop if consumer is too slow (shouldn't happen)

    def get_status(self, session_id: str) -> dict:
        """Public getter for /api/agent-status (polling fallback)."""
        return self._init_status(session_id)

    def subscribe_status(self, session_id: str) -> "asyncio.Queue":
        """
        Return an asyncio.Queue that receives status updates for *session_id*.
        Used by ``GET /api/agent-stream/{session_id}`` for real‑time SSE push.

        The queue is created lazily and cleaned up when the consumer disconnects.
        """
        if session_id not in self._status_queues:
            self._status_queues[session_id] = asyncio.Queue(maxsize=32)
            # Push initial state immediately
            self._status_queues[session_id].put_nowait(self.get_status(session_id))
        return self._status_queues[session_id]

    def unsubscribe_status(self, session_id: str):
        """Called when the SSE consumer disconnects."""
        self._status_queues.pop(session_id, None)

    def set_steam_profile(self, session_id: str, profile: dict):
        """Called by /api/sync-steam to persist Steam profile data per session."""
        self._steam_profiles[session_id] = profile
        # Reflect in status
        self._update_status(
            session_id,
            data_source="Steam 在线同步",
            agent_thought="已分析你的 Steam 游戏库，等待你的提问...",
            preference_bias=profile.get("top_genre", ""),
            confidence=78,
        )
        # Persist to DB if this session is linked to a user account
        self._persist_steam_to_db(session_id, profile)

    # ------------------------------------------------------------------
    #  User account linking (enables data persistence across re-logins)
    # ------------------------------------------------------------------

    def link_user(self, session_id: str, user_id: int) -> dict:
        """
        Associate *session_id* with a user account.

        Returns a dict with the restored user state so the frontend can
        update its UI: past conversations, Steam profile, preferences,
        and settings.
        """
        self._session_users[session_id] = user_id
        self.profile.link_user(session_id, user_id)

        result: dict = {
            "success": True,
            "user_id": user_id,
            "conversations": [],
            "steam_profile": None,
            "preferences": None,
            "settings": None,
        }

        # 1. Restore past conversations (across all sessions)
        try:
            result["conversations"] = self.memory.load_user_history(
                user_id, limit=50
            )
        except Exception:
            pass

        # 2. Restore Steam profile from DB
        try:
            from data_layer.schema import User
            user = self.db.session.query(User).filter(User.id == user_id).first()
            if user and user.steam_profile_json:
                import json
                steam = json.loads(user.steam_profile_json)
                if steam.get("steam_id"):
                    self._steam_profiles[session_id] = steam
                    result["steam_profile"] = steam
                    self._update_status(
                        session_id,
                        data_source="Steam 在线同步",
                        agent_thought="已分析你的 Steam 游戏库，等待你的提问...",
                        preference_bias=steam.get("top_genre", ""),
                        confidence=78,
                    )
        except Exception:
            pass

        # 3. Restore LLM-extracted preferences
        try:
            self.profile.load_from_db(session_id, user_id)
            result["preferences"] = self.profile.get_profile(session_id)
        except Exception:
            pass

        # 4. Restore user settings
        try:
            from data_layer.schema import User
            user = self.db.session.query(User).filter(User.id == user_id).first()
            if user and user.settings_json:
                import json
                result["settings"] = json.loads(user.settings_json)
        except Exception:
            pass

        return result

    def unlink_user(self, session_id: str) -> None:
        """Remove the user association for a session (on logout)."""
        self._session_users.pop(session_id, None)
        self.profile.unlink_user(session_id)
        self._steam_profiles.pop(session_id, None)

    def get_session_user_id(self, session_id: str) -> int | None:
        """Return the user_id linked to *session_id*, or None."""
        return self._session_users.get(session_id)

    def _persist_steam_to_db(self, session_id: str, profile: dict) -> None:
        """Save Steam profile to the linked user's DB record."""
        user_id = self._session_users.get(session_id)
        if not user_id or not profile.get("success"):
            return
        import json
        try:
            steam_id = profile.get("steam_id", "")
            self.db.save_user_steam_profile(
                user_id, steam_id, json.dumps(profile, ensure_ascii=False)
            )
        except Exception:
            pass

    def save_user_settings_to_db(self, user_id: int, settings: dict) -> None:
        """Persist user settings (budget, genres, platforms) to the DB."""
        import json
        try:
            self.db.save_user_settings(
                user_id, json.dumps(settings, ensure_ascii=False)
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    #  Streaming entry point
    # ------------------------------------------------------------------

    async def run_stream(
        self,
        session_id: str,
        message: str,
        settings: dict = None,
    ) -> AsyncGenerator[str, None]:
        """
        Process a user message and yield LLM response tokens.

        Called by ``server.py``'s ``POST /chat`` handler.

        *settings* — optional dict with user preferences from the settings
                    modal: {budget, genres, platforms}.  Injected into the
                    system prompt and used to bias tool‑chain parameters.
        """
        if settings is None:
            settings = {}
        self._init_status(session_id)

        # Determine linked user_id for persistence
        user_id = self._session_users.get(session_id)

        # 1. Save user message to history
        self.memory.add(session_id, "user", message, user_id=user_id)

        # Yield progress immediately so the SSE stream has data flowing
        # while we do pre‑processing.  The frontend uses __PROGRESS__ to
        # update the typing indicator instead of showing a blank bubble.
        yield "__PROGRESS__正在分析你的需求..."

        # Phase: analyzing
        self._update_status(session_id, phase="analyzing",
                            agent_thought="正在分析你的需求...", confidence=60)

        # 2. Silently extract preferences (fire‑and‑forget, non‑blocking in spirit)
        await self.profile.extract_and_update(session_id, message)

        # 3. Decide: clarify or run tools
        if self._should_ask_clarification(message):
            tool_results = ""
            yield "__PROGRESS__需要了解更多细节..."
            self._update_status(session_id, phase="clarifying",
                                agent_thought="需要更多信息，准备追问...")
        else:
            # Yield progress before the (potentially slow) tool chain
            yield "__PROGRESS__正在检索游戏库..."

            # Phase: searching
            self._update_status(session_id, phase="searching",
                                agent_thought="正在检索游戏库和 Steam 实时数据...")
            tool_context = await self._run_tool_chain(message, settings)
            self._tool_result_cache[session_id] = tool_context
            tool_results = self._format_tool_results(tool_context)

            found_count = len(tool_context)
            yield f"__PROGRESS__已检索到 {found_count} 款匹配游戏，正在生成推荐..."

            self._update_status(
                session_id, phase="generating",
                agent_thought=f"已检索到 {found_count} 款匹配游戏，正在生成推荐...",
                confidence=min(85, 55 + found_count * 5),
            )

        # 4. Build user-settings text for the prompt
        user_settings_text = self._format_user_settings(settings)

        # 5. Build the system prompt
        user_summary = self.profile.get_summary(session_id)
        system_prompt = self._system_prompt_template.replace(
            "{user_profile_summary}", user_summary or "暂无偏好数据"
        ).replace(
            "{user_settings}", user_settings_text or ""
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
        self.memory.add(session_id, "assistant", full_response, user_id=user_id)

        # Phase: done
        self._update_status(session_id, phase="idle",
                            agent_thought="回复完成，等待你的下一个问题 👋")

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

    async def _run_tool_chain(self, message: str, settings: dict = None) -> list[dict]:
        """
        Dispatch the message to the appropriate tool(s) and return results.

        Intent routing logic:
            - "类似XX" / semantic description → semantic_search_tool
            - concrete filters (price, tags, review) → game_filter_tool
            - "XX游戏多少钱" / price check → steam_store_tool
            - Combo: filter + semantic can both run

        *settings* — user preferences from the settings modal, used to
                     bias tool‑chain parameters when the message itself
                     doesn't specify concrete constraints.
        """
        if settings is None:
            settings = {}
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

            # Price extraction (from message)
            price_match = re.search(r"(\d+)\s*(?:块|元|¥|￥)", message)
            if price_match:
                params["max_price"] = float(price_match.group(1))
            elif "免费" in msg_lower:
                params["max_price"] = 0.0
            elif "便宜" in msg_lower:
                params["max_price"] = 50.0
            # Fall back to settings budget
            elif settings.get("budget"):
                params["max_price"] = float(settings["budget"])

            # Multiplayer detection
            if any(kw in msg_lower for kw in ["多人", "联机", "和朋友", "在线"]):
                params["is_multiplayer"] = True
            elif "单机" in msg_lower:
                params["is_multiplayer"] = False
            # Fall back to settings platform
            elif settings.get("platforms"):
                if "多人" in settings["platforms"] and "单机" not in settings["platforms"]:
                    params["is_multiplayer"] = True
                elif "单机" in settings["platforms"] and "多人" not in settings["platforms"]:
                    params["is_multiplayer"] = False

            # Tags from settings genres
            if settings.get("genres"):
                params["tags"] = settings["genres"]

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

        # --- Apply settings bias when no explicit filter matched ------------
        # If the user didn't say "price"/"multiplayer" etc. but HAS settings,
        # apply the filter tool with settings-derived parameters.
        if not has_price and not has_multi and not has_review and settings:
            filter_params: dict = {"limit": 5}
            applied = False
            if settings.get("budget"):
                filter_params["max_price"] = float(settings["budget"])
                applied = True
            if settings.get("genres"):
                filter_params["tags"] = settings["genres"]
                applied = True
            if settings.get("platforms"):
                if "多人" in settings["platforms"] and "单机" not in settings["platforms"]:
                    filter_params["is_multiplayer"] = True
                    applied = True
                elif "单机" in settings["platforms"] and "多人" not in settings["platforms"]:
                    filter_params["is_multiplayer"] = False
                    applied = True
            if applied:
                extra = self.filter_tool.run(filter_params)
                for g in extra:
                    if g["name"] not in {r.get("name") for r in results}:
                        results.append(g)

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

    @staticmethod
    def _format_user_settings(settings: dict) -> str:
        """Build a human‑readable settings summary for the system prompt."""
        if not settings:
            return ""
        parts = []
        budget = settings.get("budget")
        if budget:
            parts.append(f"预算上限 {budget} 元")
        genres = settings.get("genres", [])
        if genres:
            parts.append(f"偏好类型：{'、'.join(genres)}")
        platforms = settings.get("platforms", [])
        if platforms:
            parts.append(f"平台偏好：{'、'.join(platforms)}")
        if not parts:
            return ""
        return "## 用户在设置面板中的偏好\n" + "\n".join(f"- {p}" for p in parts) + \
               "\n\n请优先根据以上偏好进行推荐。如果用户当前提问与偏好冲突，以当前提问为准。"

    def _format_tool_results(self, results: list[dict]) -> str:
        """Convert tool results into a compact text block for the LLM.

        Formats the list so the LLM can easily reference each game and its
        exact name — this is critical for the frontend card‑matching logic.
        """
        if not results:
            return "（无匹配结果 — 请告诉用户换个条件试试，不要编造游戏）"

        lines = ["以下是为用户检索到的游戏（**你只能推荐这个列表里的游戏**）："]
        for i, g in enumerate(results, 1):
            name = g.get("name", "未知游戏")
            tags_str = ", ".join(g.get("tags", [])) if g.get("tags") else "无标签"
            price_str = f"¥{g['price']}" if g.get("price") else "免费"
            review_str = f"好评率 {int(g.get('review', 0) * 100)}%" if g.get("review") else "暂无评价"
            desc = g.get("description", "暂无简介")[:150]
            store = g.get("store_url", "")
            lines.append(
                f"{i}. **{name}**\n"
                f"   价格：{price_str}  |  {review_str}\n"
                f"   标签：{tags_str}\n"
                f"   简介：{desc}\n"
                f"   商店：{store}"
            )

        return "\n\n".join(lines)

    def get_tool_results(self, session_id: str) -> list[dict]:
        """Return the structured tool results from the last query for this session.

        Called by the GET /api/tool-results/{session_id} endpoint so the
        frontend can render rich game cards after the SSE stream ends.
        """
        return self._tool_result_cache.get(session_id, [])
