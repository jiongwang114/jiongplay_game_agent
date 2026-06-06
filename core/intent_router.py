"""Intent detection and routing — keyword fast‑path + LLM deep understanding.

Extracted from ``core/agent.py`` for testability.  The ``IntentRouter`` class
handles progressive intent analysis: lightweight keyword matching first, then
escalating to the LLM only when needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.llm_engine import LLMEngine


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
# [新增] 排除语义关键词 — 用户说"不要XX"、"别XX"时，关键词系统无法处理，需升级到 LLM
_EXCLUDE_KEYWORDS = ["不要", "别", "除了", "排除", "非", "不是", "不含", "别给我", "不想"]


# ---------------------------------------------------------------------------
#  Prompt loader
# ---------------------------------------------------------------------------

# [职责] 从文件加载 LLM 意图检测 prompt 模板 → 返回字符串
def _load_intent_prompt() -> str:
    """Load the intent detection prompt from file."""
    prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "intent_detection_prompt.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return ""  # 调用方会检查空值并降级到关键词路由


# ---------------------------------------------------------------------------
#  Utility — exported so agent.py can use it without duplicating
# ---------------------------------------------------------------------------

# [职责] 将用户设置 dict → 格式化文本，供系统提示词和意图检测使用
def format_user_settings(settings: dict) -> str:
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


# ---------------------------------------------------------------------------
#  IntentRouter
# ---------------------------------------------------------------------------

class IntentRouter:
    """
    Progressive intent detection: keyword fast‑path first, LLM escalation second.

    Usage::

        router = IntentRouter(llm)
        intent = await router.detect(message, settings, user_summary)
    """

    # [职责] 初始化意图路由器 → 保存 LLM 引擎引用
    def __init__(self, llm: "LLMEngine"):
        self.llm = llm

    # [职责] 输入用户消息 + 设置 → 关键词快速路径 → 返回意图 dict 或 None（需升级 LLM）
    def _keyword_intent_detect(
        self, message: str, settings: dict
    ) -> dict | None:
        """
        Fast keyword‑based intent detection.

        Returns an intent dict if keywords give a **clear** signal,
        or None if the message is ambiguous → caller should escalate to LLM.
        """
        msg_lower = message.lower().strip()

        # --- Low confidence: exclusion keywords → escalate to LLM ---
        if any(kw in msg_lower for kw in _EXCLUDE_KEYWORDS):
            return None

        # --- Low confidence: no relevant keywords at all ---
        has_genre = any(kw in msg_lower for kw in _GENRE_KEYWORDS)
        has_price = any(kw in msg_lower for kw in _PRICE_KEYWORDS)
        has_similar = any(kw in msg_lower for kw in _SIMILAR_KEYWORDS)
        has_multi = any(kw in msg_lower for kw in ["多人", "联机", "和朋友", "在线", "单机"])
        has_review = any(kw in msg_lower for kw in ["好评", "评分", "口碑"])
        has_price_check = bool(re.search(r"(.+?)(?:多少钱|价格|什么价|贵不贵|打折)", message))

        if not (has_genre or has_price or has_similar or has_multi or has_review or has_price_check):
            return None  # Too vague, needs LLM

        # --- Build intent from keywords ---
        action = "filter"
        search_query = message
        filters: dict = {"max_price": None, "min_review": None, "tags": [],
                         "exclude_tags": [], "is_multiplayer": None,
                         "similar_to": "", "limit": 10}

        # Price extraction
        price_match = re.search(r"(\d+)\s*(?:块|元|¥|￥)", message)
        if price_match:
            filters["max_price"] = float(price_match.group(1))
        elif "免费" in msg_lower:
            filters["max_price"] = 0.0
        elif "便宜" in msg_lower:
            filters["max_price"] = 50.0

        # Multiplayer
        if any(kw in msg_lower for kw in ["多人", "联机", "和朋友", "在线"]):
            filters["is_multiplayer"] = True
        elif "单机" in msg_lower:
            filters["is_multiplayer"] = False

        # Review
        if has_review:
            filters["min_review"] = 0.8

        # Tags from settings as fallback
        if settings.get("genres"):
            filters["tags"] = list(settings["genres"])

        # Determine action
        if has_price_check:
            action = "price_check"
        if has_similar:
            action = "search" if not (has_price or has_multi or has_review) else "mixed"
        elif has_genre and not (has_price or has_multi or has_review):
            action = "search"

        # Optimize search query
        if action in ("search", "mixed"):
            for filler in ["有没有", "推荐", "我想找", "帮我", "一个", "一款", "一些"]:
                search_query = search_query.replace(filler, "")
            search_query = search_query.strip()

        return {
            "action": action,
            "source": "keyword",
            "clarify_question": "",
            "search_query": search_query,
            "filters": filters,
            "reasoning": f"keyword: genre={has_genre}, price={has_price}, similar={has_similar}",
        }

    # [职责] 调用 LLM 分析用户消息 → 返回结构化意图 dict（含 exclude_tags 等）
    async def _llm_intent_detect(
        self, message: str, settings: dict, user_summary: str
    ) -> dict:
        """Call the LLM to deeply understand the user's intent."""
        prompt_template = _load_intent_prompt()
        if not prompt_template:
            return {
                "action": "clarify", "source": "llm",
                "clarify_question": "能多说一点你的偏好吗？比如喜欢的游戏类型、预算范围？",
                "search_query": "",
                "filters": {"max_price": None, "min_review": None, "tags": [],
                            "exclude_tags": [], "is_multiplayer": None,
                            "similar_to": "", "limit": 10},
                "reasoning": "意图检测 prompt 文件缺失，降级为追问",
            }

        settings_text = format_user_settings(settings) if settings else ""
        prompt = prompt_template.replace(
            "{user_profile}", user_summary or "暂无用户画像数据"
        ).replace(
            "{user_settings}", settings_text or "用户未设置偏好"
        ).replace(
            "{user_message}", message
        )

        try:
            raw = await self.llm.chat(prompt)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            parsed = json.loads(raw)

            valid_actions = {"clarify", "search", "filter", "mixed", "price_check"}
            action = parsed.get("action", "clarify")
            if action not in valid_actions:
                action = "clarify"

            filters = parsed.get("filters", {})
            if not isinstance(filters, dict):
                filters = {}

            return {
                "action": action, "source": "llm",
                "clarify_question": parsed.get("clarify_question", ""),
                "search_query": parsed.get("search_query", message),
                "filters": {
                    "max_price": filters.get("max_price"),
                    "min_review": filters.get("min_review"),
                    "tags": filters.get("tags") or [],
                    "exclude_tags": filters.get("exclude_tags") or [],
                    "is_multiplayer": filters.get("is_multiplayer"),
                    "similar_to": filters.get("similar_to", ""),
                    "limit": filters.get("limit", 10),
                },
                "reasoning": parsed.get("reasoning", ""),
            }
        except (json.JSONDecodeError, Exception):
            fallback = self._keyword_intent_detect(message, settings)
            if fallback:
                fallback["source"] = "keyword_fallback"
                return fallback
            return {
                "action": "clarify", "source": "keyword_fallback",
                "clarify_question": "能多说一点你的偏好吗？",
                "search_query": "",
                "filters": {"max_price": None, "min_review": None, "tags": [],
                            "exclude_tags": [], "is_multiplayer": None,
                            "similar_to": "", "limit": 10},
                "reasoning": "LLM JSON 解析失败，降级为追问",
            }

    # [职责] 渐进式意图检测：先试快速关键词，模糊时升级 LLM → 返回统一意图 dict
    async def detect(
        self, message: str, settings: dict, user_summary: str
    ) -> dict:
        """Main entry: try keyword fast-path first, escalate to LLM if ambiguous."""
        kw_intent = self._keyword_intent_detect(message, settings)
        if kw_intent is not None:
            return kw_intent
        return await self._llm_intent_detect(message, settings, user_summary)
