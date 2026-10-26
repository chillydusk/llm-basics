"""模型客户端：把"调 DeepSeek"这一件事收在一个地方。

    LLMClient.stream(...)  → 逐块吐 chunk（Agent 循环自己负责拼）
    LLMClient.invoke(...)  → 一次拿完整结果（测试 / 脚本用）

三件企业级的小事在这里落地：

    ① **重试**  只在「还没吐任何内容」时重试（网络抖动最常发生在这里）。
       已经开始流式输出再重试 = 用户看到重复的答案。
    ② **超时**  统一由 Settings.request_timeout 控制。
    ③ **异常翻译**  把 openai 的异常包成 LLMError，上层不用认识 SDK。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from study_assistant.config import Settings
from study_assistant.errors import LLMError

logger = logging.getLogger(__name__)

_TRANSIENT = (APIConnectionError, APITimeoutError, RateLimitError)

Message = dict[str, Any]


class LLMClient:
    def __init__(self, settings: Settings, *, max_retries: int = 3) -> None:
        self.settings = settings
        self.max_retries = max_retries
        self._client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.request_timeout,
        )

    # ── 流式 ──────────────────────────────────────────────
    def stream(self, messages: list[Message], tools: list[dict] | None = None) -> Iterator[Any]:
        """逐块返回 SDK 的 chunk。第一次取块失败会自动重试。"""
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools

        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                stream = self._client.chat.completions.create(**kwargs)
                it = iter(stream)
                first = next(it, None)  # ← 在这里暴露连接问题，此时还没打印任何东西
            except _TRANSIENT as e:
                if attempt == self.max_retries:
                    raise LLMError(f"调用模型失败（已重试 {attempt} 次）：{e}") from e
                logger.warning("第 %d 次调用失败，%.1fs 后重试：%s", attempt, delay, e)
                time.sleep(delay)
                delay *= 2
                continue
            except Exception as e:
                raise LLMError(f"调用模型失败：{e}") from e

            if first is not None:
                yield first
            yield from it  # 中途出错不再重试（避免重复输出）
            return

    # ── 一次性 ────────────────────────────────────────────
    def invoke(self, messages: list[Message], tools: list[dict] | None = None):
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
        }
        if tools:
            kwargs["tools"] = tools
        try:
            return self._client.chat.completions.create(**kwargs)
        except Exception as e:
            raise LLMError(f"调用模型失败：{e}") from e
