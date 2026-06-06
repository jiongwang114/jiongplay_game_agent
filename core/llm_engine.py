"""LLM API wrapper — supports OpenAI / DeepSeek / any OpenAI‑compatible API.

Includes exponential‑backoff retry (via tenacity) for transient API errors.
"""

import logging
import os
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

_logger = logging.getLogger("steam_agent")


def _is_retryable(exception: Exception) -> bool:
    """Return True for transient errors worth retrying."""
    # Import errors lazily to avoid coupling
    try:
        from openai import (
            APIConnectionError,
            APIError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    except ImportError:  # pragma: no cover — older openai versions
        return True  # be safe and retry on unknown errors

    if isinstance(exception, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    if isinstance(exception, InternalServerError):
        return True
    # APIError with status >= 500 is also retryable
    if isinstance(exception, APIError):
        status = getattr(exception, "status_code", None)
        if status is not None and status >= 500:
            return True
    return False


class LLMEngine:
    """
    Thin wrapper around OpenAI‑compatible chat completions APIs.

    Defaults to DeepSeek.  Set these env vars in ``.env``:

    - ``DEEPSEEK_API_KEY``  (or ``OPENAI_API_KEY`` as fallback)
    - ``DEEPSEEK_BASE_URL`` (default: ``https://api.deepseek.com``)
    - ``MODEL_NAME``        (default: ``deepseek-chat``)
    - ``LLM_MAX_RETRIES``   (default: 3)
    - ``LLM_RETRY_MIN_WAIT``(default: 1.0, seconds)
    - ``LLM_RETRY_MAX_WAIT``(default: 30.0, seconds)
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

        # Retry config — read from env so users can tune without code changes
        self._max_retries = int(os.getenv("LLM_MAX_RETRIES", "3"))
        self._retry_min_wait = float(os.getenv("LLM_RETRY_MIN_WAIT", "1.0"))
        self._retry_max_wait = float(os.getenv("LLM_RETRY_MAX_WAIT", "30.0"))

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

        stream = None
        last_error = None
        try:
            # [新增] 指数退避重试 — 解决 API 抖动导致的用户体验崩溃
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential(
                    multiplier=1, min=self._retry_min_wait, max=self._retry_max_wait
                ),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    try:
                        stream = await self._client.chat.completions.create(
                            model=self.model,
                            messages=full_messages,
                            stream=True,
                            temperature=0.7,
                            max_tokens=1024,
                        )
                    except Exception as e:
                        last_error = e
                        if attempt.retry_state and attempt.retry_state.attempt_number < self._max_retries:
                            _logger.warning(
                                "LLM stream_chat 调用失败（第 %d 次尝试），正在重试：%s",
                                attempt.retry_state.attempt_number, e
                            )
                        raise
        except Exception as e:
            _logger.error("LLM stream_chat 所有重试均失败：%s", e)
            yield f"\n[❌ LLM 连接失败（已重试 {self._max_retries} 次）：{e}]"
            return

        if stream is None:
            yield f"\n[❌ LLM 连接失败：未能建立流式连接]"
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

        last_error = None
        try:
            # [新增] 指数退避重试
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential(
                    multiplier=1, min=self._retry_min_wait, max=self._retry_max_wait
                ),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    try:
                        response = await self._client.chat.completions.create(
                            model=self.model,
                            messages=messages,
                            temperature=0.3,
                            max_tokens=512,
                        )
                    except Exception as e:
                        last_error = e
                        if attempt.retry_state and attempt.retry_state.attempt_number < self._max_retries:
                            _logger.warning(
                                "LLM chat 调用失败（第 %d 次尝试），正在重试：%s",
                                attempt.retry_state.attempt_number, e
                            )
                        raise
        except Exception as e:
            _logger.error("LLM chat 所有重试均失败：%s", e)
            return f"[❌ LLM 调用失败（已重试 {self._max_retries} 次）：{e}]"

        msg = response.choices[0].message
        # Support reasoning models: fall back to reasoning_content
        return msg.content or getattr(msg, "reasoning_content", None) or ""
