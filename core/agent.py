"""Recommendation wizard Agent — intent routing, tool orchestration, streaming."""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import AsyncGenerator, Optional

from core.intent_router import IntentRouter, format_user_settings
from core.llm_engine import LLMEngine
from core.memory.session_memory import SessionMemory
from core.memory.user_profile import UserProfile
from core.trace import TraceContext, TraceStore
from tools.game_filter_tool import GameFilterTool
from tools.semantic_search_tool import SemanticSearchTool
from tools.steam_store_tool import SteamStoreTool
from data_layer.sqlite_db import GameDB
from data_layer.vector_store import VectorStore


# ---------------------------------------------------------------------------
#  Intent‑detection keywords (lightweight, no LLM call needed for routing)
# ---------------------------------------------------------------------------

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

    # [职责] 初始化 Agent 所有依赖 — 每个参数为 None 时自动创建真实实例（支持依赖注入测试）
    def __init__(
        self,
        llm=None,
        db=None,
        memory=None,
        profile=None,
        vector_store=None,
        filter_tool=None,
        semantic_tool=None,
        steam_tool=None,
    ):
        # Shared infrastructure — injectable for testing, defaults to real instances
        self.llm = llm if llm is not None else LLMEngine()
        self.db = db if db is not None else GameDB()
        self.memory = memory if memory is not None else SessionMemory(db=self.db)
        self.profile = profile if profile is not None else UserProfile(self.llm)
        self.profile._db = self.db  # Enable DB persistence for preferences
        self.vector_store = vector_store if vector_store is not None else VectorStore()

        # Tools
        self.filter_tool = filter_tool if filter_tool is not None else GameFilterTool(self.db)
        self.semantic_tool = semantic_tool if semantic_tool is not None else SemanticSearchTool(self.vector_store, self.db)
        self.steam_tool = steam_tool if steam_tool is not None else SteamStoreTool(self.db)

        # [新增] Intent router — extracted for testability (Phase 3)
        self.intent_router = IntentRouter(self.llm)

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

        # [新增] 创建请求级 Trace，用于可视化调试 Agent 执行流程
        trace = TraceContext(session_id, message)

        # Determine linked user_id for persistence
        user_id = self._session_users.get(session_id)

        # 1. Save user message to history
        with trace.span("save_message") as span:
            self.memory.add(session_id, "user", message, user_id=user_id)
            span.input = {"message": message[:120], "user_id": user_id}
            span.output = {"saved": True}

        # Yield progress immediately so the SSE stream has data flowing
        # while we do pre‑processing.  The frontend uses __PROGRESS__ to
        # update the typing indicator instead of showing a blank bubble.
        yield "__PROGRESS__正在分析你的需求..."

        # Phase: analyzing
        self._update_status(session_id, phase="analyzing",
                            agent_thought="正在分析你的需求...", confidence=60)

        # 2. Silently extract preferences (fire‑and‑forget, non‑blocking in spirit)
        # [新增] trace: 记录偏好提取
        with trace.span("profile_extraction") as prof_span:
            await self.profile.extract_and_update(session_id, message)
            user_summary = self.profile.get_summary(session_id)
            prof_span.input = {"message": message[:120]}
            prof_span.output = {"user_summary": user_summary or "(空)"}

        # Build user summary early — needed for intent detection
        # (user_summary already computed inside the span above)

        # 3. Detect intent (keyword fast‑path or LLM deep understanding)
        #    [修改] 原因：硬编码关键词无法理解"打起来很爽"、排除条件等自然语言
        # [新增] trace: 记录意图检测
        with trace.span("intent_detection") as intent_span:
            intent = await self.intent_router.detect(message, settings, user_summary)
            intent_span.input = {"message": message[:120], "user_summary": user_summary or ""}
            intent_span.output = {
                "action": intent.get("action"),
                "source": intent.get("source"),
                "clarify_question": intent.get("clarify_question", "")[:80],
                "search_query": intent.get("search_query", "")[:80],
                "reasoning": intent.get("reasoning", "")[:120],
            }

        if intent["action"] == "clarify":
            # [新增] trace: 记录追问决策
            with trace.span("clarify_decision") as clarify_span:
                # Pass LLM‑generated clarification question as hint to the prompt
                clarify_q = intent.get("clarify_question", "")
                if clarify_q:
                    tool_results = f"（用户表达比较模糊。建议追问方向：{clarify_q}。请结合用户画像，用轻松的语气问出这个问题。）"
                else:
                    tool_results = "（用户表达比较模糊，请根据追问规则向用户提 1 个最关键的问题。）"
                clarify_span.input = {"intent_source": intent.get("source")}
                clarify_span.output = {"clarify_question": clarify_q or "(默认追问)"}
            yield "__PROGRESS__需要了解更多细节..."
            self._update_status(session_id, phase="clarifying",
                                agent_thought="需要更多信息，准备追问...")
        else:
            # Yield progress before the (potentially slow) tool chain
            yield "__PROGRESS__正在检索游戏库..."

            # Phase: searching
            self._update_status(session_id, phase="searching",
                                agent_thought="正在检索游戏库和 Steam 实时数据...")
            # [修改] 原因：将 LLM 意图检测结果传入工具链，替代硬编码关键词路由
            # [新增] trace: 记录工具执行
            with trace.span("tool_execution") as tool_span:
                tool_context = await self._run_tool_chain(message, settings, intent)
                self._tool_result_cache[session_id] = tool_context
                tool_results = self._format_tool_results(tool_context)
                tool_span.input = {
                    "action": intent.get("action"),
                    "search_query": intent.get("search_query", "")[:80],
                    "filters": intent.get("filters", {}),
                }
                tool_span.output = {
                    "result_count": len(tool_context),
                    "game_names": [g.get("name", "") for g in tool_context[:5]],
                }

            found_count = len(tool_context)
            yield f"__PROGRESS__已检索到 {found_count} 款匹配游戏，正在生成推荐..."

            self._update_status(
                session_id, phase="generating",
                agent_thought=f"已检索到 {found_count} 款匹配游戏，正在生成推荐...",
                confidence=min(85, 55 + found_count * 5),
            )

        # 4. Build user-settings text for the prompt
        user_settings_text = format_user_settings(settings)

        # 5. Build the system prompt — merge Steam profile + LLM-extracted preferences
        #    user_summary already computed above (before intent detection)
        # [新增] trace: 记录 Prompt 组装
        with trace.span("prompt_assembly") as prompt_span:
            steam_summary = self._build_steam_summary(session_id)
            # [修改] 原因：Steam 同步数据之前仅显示在侧边栏，未进入推荐提示词，导致 Agent 不知道用户偏好
            if steam_summary and user_summary:
                combined_summary = steam_summary + "\n\n" + user_summary
            elif steam_summary:
                combined_summary = steam_summary
            else:
                combined_summary = user_summary
            system_prompt = self._system_prompt_template.replace(
                "{user_profile_summary}", combined_summary or "暂无偏好数据"
            ).replace(
                "{user_settings}", user_settings_text or ""
            ).replace(
                "{tool_results}", tool_results or "（等待用户提供更多信息）"
            )
            prompt_span.input = {
                "has_steam": bool(steam_summary),
                "has_user_summary": bool(user_summary),
                "has_tool_results": bool(tool_results and "无匹配结果" not in str(tool_results)),
            }
            prompt_span.output = {"prompt_chars": len(system_prompt)}

        # 5. Build context for the LLM
        history = self.memory.get(session_id)
        context = message

        # 6. Stream response
        # [新增] trace: 记录 LLM 生成
        response_chunks: list[str] = []
        with trace.span("llm_generation") as llm_span:
            llm_span.input = {
                "model": self.llm.model,
                "history_turns": len(history) // 2,
                "context": context[:120],
            }
            async for token in self.llm.stream_chat(system_prompt, history, context):
                response_chunks.append(token)
                yield token
            llm_span.output = {"response_chars": len("".join(response_chunks))}
            llm_span.metadata["model"] = self.llm.model

        # 7. Save assistant response to history
        full_response = "".join(response_chunks)
        self.memory.add(session_id, "assistant", full_response, user_id=user_id)

        # [新增] 完成 trace 并存入全局缓冲区
        trace.finish()
        TraceStore.add(trace.trace)

        # Phase: done
        self._update_status(session_id, phase="idle",
                            agent_thought="回复完成，等待你的下一个问题 👋")

    # ------------------------------------------------------------------
    #  Tool chain (intent‑driven)
    # ------------------------------------------------------------------

    # [职责] 输入消息 + 设置 + 意图 → 编排工具调用 → 返回合并去重后的游戏列表
    async def _run_tool_chain(
        self, message: str, settings: dict = None, intent: dict = None
    ) -> list[dict]:
        """
        Dispatch the message to the appropriate tool(s) and return results.

        All tool calls go through this method.  Sync tools (GameFilterTool,
        SemanticSearchTool) are run via ``asyncio.to_thread()`` to avoid
        blocking the event loop.  SteamStoreTool is natively async.
        """
        if settings is None:
            settings = {}
        results: list[dict] = []

        # intent is always provided by _detect_intent — no more fallback path
        action = intent.get("action", "search") if intent else "search"
        search_query = intent.get("search_query", message) if intent else message
        filters = intent.get("filters", {}) if intent else {}
        tags = filters.get("tags") or []
        exclude_tags = filters.get("exclude_tags") or []
        max_price = filters.get("max_price")
        min_review = filters.get("min_review")
        is_multiplayer = filters.get("is_multiplayer")
        limit = filters.get("limit", 10)

        # Merge settings defaults into filters (intent takes priority)
        if max_price is None and settings.get("budget"):
            max_price = float(settings["budget"])
        if not tags and settings.get("genres"):
            tags = list(settings["genres"])
        if is_multiplayer is None and settings.get("platforms"):
            if "多人" in settings["platforms"] and "单机" not in settings["platforms"]:
                is_multiplayer = True
            elif "单机" in settings["platforms"] and "多人" not in settings["platforms"]:
                is_multiplayer = False

        # [修改] 原因：同步工具调用改为 asyncio.to_thread()，避免阻塞事件循环
        # Semantic search
        if action in ("search", "mixed"):
            semantic_results = await asyncio.to_thread(
                self.semantic_tool.run, search_query, limit
            )
            results.extend(semantic_results)

        # Filter
        if action in ("filter", "mixed"):
            filter_params = {
                "max_price": max_price, "min_review": min_review,
                "tags": tags, "exclude_tags": exclude_tags,
                "is_multiplayer": is_multiplayer, "limit": limit,
            }
            has_conditions = any([
                max_price is not None, min_review is not None,
                tags, exclude_tags, is_multiplayer is not None,
            ])
            if has_conditions:
                filter_results = await asyncio.to_thread(
                    self.filter_tool.run, filter_params
                )
                results.extend(filter_results)

        # Price check (already async — no change needed)
        if action == "price_check":
            similar_to = filters.get("similar_to", "")
            game_name = similar_to or message
            game_name = re.sub(r"^(类似|像|类)\s*", "", game_name)
            steam_result = await self.steam_tool.run(game_name)
            if "error" not in steam_result:
                results.append(steam_result)

        # Fallback: if no results, try semantic search with raw message
        if not results and action != "clarify":
            semantic_results = await asyncio.to_thread(
                self.semantic_tool.run, message
            )
            results.extend(semantic_results)

        # ==================================================================
        #  Common post‑processing: deduplicate and limit
        # ==================================================================
        seen: set[str] = set()
        unique: list[dict] = []
        for r in results:
            name = r.get("name", "")
            if name and name not in seen:
                seen.add(name)
                unique.append(r)

        return unique[:5]

    def _build_steam_summary(self, session_id: str) -> str:
        """[职责] 读取 _steam_profiles → 生成 Steam 用户画像文本，供系统提示词使用。

        Returns empty string if no Steam data has been synced for this session.
        """
        steam = self._steam_profiles.get(session_id, {})
        if not steam or not steam.get("success"):
            return ""

        parts = []
        persona = steam.get("persona_name", "")
        game_count = steam.get("game_count", 0)
        top_genres = steam.get("top_genres", [])
        recent_games = steam.get("recent_games", [])
        total_playtime_min = steam.get("total_playtime_min", 0)
        account_age_days = steam.get("account_age_days", 0)
        loccountrycode = steam.get("loccountrycode", "")
        top_games = steam.get("top_games_by_playtime", [])

        if persona:
            parts.append(f"Steam 昵称：{persona}")
        # [新增] 账号年龄 — 判定玩家深度
        if account_age_days and account_age_days > 0:
            years = account_age_days // 365
            if years > 0:
                parts.append(f"Steam 账号注册约 {years} 年（老玩家）")
            else:
                parts.append(f"Steam 账号注册不到 1 年（新玩家）")
        if loccountrycode:
            parts.append(f"所在地区：{loccountrycode}")
        if game_count:
            parts.append(f"游戏库共 {game_count} 款游戏")
        if total_playtime_min and total_playtime_min > 0:
            total_hours = round(total_playtime_min / 60)
            parts.append(f"游戏总时长约 {total_hours} 小时")
        # [新增] 游玩时长排名 Top 5
        if top_games:
            top5_str = "、".join(
                f'{g["name"]}({g["playtime_hours"]}h)' for g in top_games[:5]
            )
            parts.append(f"游玩时长最多的游戏：{top5_str}")
        if top_genres:
            parts.append(f"根据游戏库分析，用户偏好类型：{'、'.join(top_genres[:5])}")
        if recent_games:
            recent_names = [g.get("name", "") for g in recent_games[:5] if g.get("name")]
            if recent_names:
                # [新增] 附带近两周时长
                recent_with_hours = []
                for g in recent_games[:5]:
                    name = g.get("name", "")
                    two_weeks_min = g.get("playtime_2weeks", 0)
                    if name:
                        recent_with_hours.append(
                            f'{name}({round(two_weeks_min/60, 1)}h)' if two_weeks_min > 0 else name
                        )
                parts.append(f"最近两周在玩：{'、'.join(recent_with_hours)}")

        if not parts:
            return ""

        return "## Steam 个人游戏库\n" + "\n".join(f"- {p}" for p in parts) + \
               "\n\n请优先结合用户的 Steam 游戏库偏好进行推荐。推荐时注意：\n" \
               "- 如果用户是多年老玩家，优先推荐深度作品或冷门佳作\n" \
               "- 参考用户游玩时长最长的游戏类型，推荐同类型或精神续作\n" \
               "- 结合最近两周在玩的游戏判断用户当前兴趣方向"

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
