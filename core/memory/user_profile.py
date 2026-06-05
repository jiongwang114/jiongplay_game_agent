"""User preference profile — silently extracted from chat messages via LLM."""

import json
import os
from pathlib import Path
from typing import Optional


# Default preference‑extraction prompt (also loaded from file if available)
_DEFAULT_EXTRACT_PROMPT = """从下面这句用户消息中提取游戏偏好信息。

输出格式：严格 JSON，无多余文字，结构为：
{
  "genres": [],            // 提到的游戏类型（RPG, FPS, 策略, 模拟 等），没有则 []
  "budget": null,          // 提到的预算上限（数字，人民币元），没有则 null
  "owned_games": [],       // 提到已拥有或玩过的游戏名称，没有则 []
  "platforms": [],         // 平台偏好（单机, 多人, 网游, 手游 等），没有则 []
  "mood": null             // 心情/氛围标签（放松, 挑战, 沉浸, 社交 等），没有则 null
}

如果没有可提取的信息，所有字段返回空值/空数组。

用户消息：{user_message}"""


def _load_prompt() -> str:
    """Try to load the preference extraction prompt from disk, fall back to default."""
    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / "preference_extract_prompt.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return _DEFAULT_EXTRACT_PROMPT


class UserProfile:
    """
    Tracks game preferences **per session** across messages.

    Every time ``extract_and_update`` is called, the LLM silently extracts
    structured preferences from the user's message and merges them into
    the session-specific profile.

    Keyed by *user_key* (typically ``session_id``) so that concurrent
    conversations don't pollute each other's preferences.
    """

    _EMPTY_PROFILE: dict = {
        "genres": [],
        "budget": None,
        "owned_games": [],
        "platforms": [],
        "mood": None,
    }

    def __init__(self, llm_engine):
        """*llm_engine* — an ``LLMEngine`` instance for extraction calls."""
        self._llm = llm_engine
        self._profiles: dict[str, dict] = {}  # user_key → profile dict

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def _get_profile(self, user_key: str) -> dict:
        """Return (or initialise) the profile for *user_key*."""
        if user_key not in self._profiles:
            self._profiles[user_key] = {
                "genres": [],
                "budget": None,
                "owned_games": [],
                "platforms": [],
                "mood": None,
            }
        return self._profiles[user_key]

    async def extract_and_update(self, user_key: str, message: str) -> None:
        """
        Call the LLM to extract preferences from *message*, then merge
        into the profile for *user_key*.
        """
        profile = self._get_profile(user_key)
        prompt_template = _load_prompt()
        prompt = prompt_template.replace("{user_message}", message)

        try:
            raw = await self._llm.chat(prompt)
            parsed = json.loads(raw)
        except (json.JSONDecodeError, Exception):
            return  # Silently ignore extraction failures

        if not isinstance(parsed, dict):
            return

        for key in profile:
            if key not in parsed:
                continue
            new_val = parsed[key]
            if key in ("genres", "owned_games", "platforms"):
                if isinstance(new_val, list) and new_val:
                    existing = set(profile[key])
                    for item in new_val:
                        if isinstance(item, str) and item not in existing:
                            profile[key].append(item)
                            existing.add(item)
            elif key in ("budget", "mood"):
                if new_val is not None:
                    profile[key] = new_val

    def get_summary(self, user_key: str) -> str:
        """
        Return a natural‑language summary of the profile for *user_key*.
        Returns empty string if no preferences have been collected yet.
        """
        profile = self._get_profile(user_key)
        parts = []

        genres = profile.get("genres", [])
        if genres:
            parts.append(f"偏好类型：{'、'.join(genres)}")

        budget = profile.get("budget")
        if budget is not None:
            parts.append(f"预算上限：{budget} 元")

        owned = profile.get("owned_games", [])
        if owned:
            parts.append(f"已拥有游戏：{'、'.join(owned)}")

        platforms = profile.get("platforms", [])
        if platforms:
            parts.append(f"平台偏好：{'、'.join(platforms)}")

        mood = profile.get("mood")
        if mood:
            parts.append(f"当前心情：{mood}")

        if not parts:
            return ""

        return "用户偏好：" + "；".join(parts)

    def clear(self, user_key: str) -> None:
        """Remove all collected preferences for *user_key*."""
        self._profiles.pop(user_key, None)

    @property
    def raw(self) -> dict:
        """Return a copy of all raw profiles (for debugging)."""
        return {k: dict(v) for k, v in self._profiles.items()}
