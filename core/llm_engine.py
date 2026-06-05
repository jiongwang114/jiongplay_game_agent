"""LLM API wrapper — supports OpenAI / DeepSeek / any OpenAI‑compatible API."""

import os
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI


class LLMEngine:
    """
    Thin wrapper around OpenAI‑compatible chat completions APIs.

    Defaults to DeepSeek.  Set these env vars in ``.env``:

    - ``DEEPSEEK_API_KEY``  (or ``OPENAI_API_KEY`` as fallback)
    - ``DEEPSEEK_BASE_URL`` (default: ``https://api.deepseek.com``)
    - ``MODEL_NAME``        (default: ``deepseek-chat``)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        # API key — DEEPSEEK_API_KEY first, then OPENAI_API_KEY as fallback
        api_key = (
            api_key
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        # Base URL — DEEPSEEK_BASE_URL first, then default to DeepSeek
        base_url = (
            base_url
            or os.getenv("DEEPSEEK_BASE_URL")
            or "https://api.deepseek.com"
        )
        model = model or os.getenv("MODEL_NAME", "deepseek-chat")

        if not api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY (or OPENAI_API_KEY) is required. "
                "Set it in .env or pass it directly."
            )

        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    # ------------------------------------------------------------------
    #  Streaming (used by the chat endpoint)
    # ------------------------------------------------------------------

    async def stream_chat(
        self,
        system_prompt: str,
        messages: list[dict],
        context: str = "",
    ) -> AsyncGenerator[str, None]:
        """
        Yield tokens from the LLM as they arrive.

        *system_prompt*  — injected as the system message
        *messages*       — list of {"role": …, "content": …} history
        *context*        — appended as the last user message (tool results, etc.)
        """
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(messages)

        if context:
            full_messages.append({"role": "user", "content": context})

        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                stream=True,
                temperature=0.7,
                max_tokens=1024,
            )
        except Exception as e:
            yield f"\n[❌ LLM 连接失败：{e}]"
            return

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta:
                # Support reasoning models (deepseek-v4-flash / v4-pro)
                # whose output is in `reasoning_content` instead of `content`
                token = delta.content or getattr(delta, "reasoning_content", None)
                if token:
                    yield token

    # ------------------------------------------------------------------
    #  Non‑streaming (used for preference extraction)
    # ------------------------------------------------------------------

    async def chat(self, prompt: str, system_prompt: str = "") -> str:
        """
        Simple non‑streaming call — returns the full response text.

        Used for background tasks like user profile extraction that don't
        need streaming.
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                max_tokens=512,
            )
        except Exception as e:
            return f"[❌ LLM 调用失败：{e}]"

        msg = response.choices[0].message
        # Support reasoning models: fall back to reasoning_content
        return msg.content or getattr(msg, "reasoning_content", None) or ""
