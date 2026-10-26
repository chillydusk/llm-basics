"""Agent 循环 —— 这个项目的核心，也是唯一"框架不会替你做"的那一层。

一句话：**反复问模型，直到它不再要工具为止。**

    截断历史 → 请求（流式）→ 攒工具调用分片 → 判 finish_reason
                                              ├─ tool_calls → 执行 → 回填 → 转圈
                                              └─ stop       → 返回答案

流式累积三定律（Part E 用三次失败换来的，别改）：

    ① 按 tc.index 分槽（setdefault）
    ② id / name 只有第一片有值 → 先到先得，**不覆盖**
    ③ arguments 每片都有 → **+= 拼接**，攒完才能 json.loads

工具执行交给 ToolRegistry：**它永不抛异常**，错误会作为结果喂回模型，
所以一句"这个文件不存在"不会把整个会话搞崩。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from study_assistant.config import Settings
from study_assistant.history import trim
from study_assistant.llm import LLMClient
from study_assistant.tools import ToolRegistry
from study_assistant.usage import CostMeter, Usage, estimate_cost

logger = logging.getLogger(__name__)

DIM = "\033[2m"
RESET = "\033[0m"

Message = dict[str, Any]


@dataclass
class AgentResult:
    text: str
    turns: int
    finish_reason: str | None
    hit_max_turns: bool = False


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        settings: Settings,
        meter: CostMeter | None = None,
        *,
        stream: bool = True,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.settings = settings
        self.meter = meter or CostMeter()
        self.stream = stream

    # ── 对外 ──────────────────────────────────────────────
    def run(self, history: list[Message]) -> AgentResult:
        messages = trim(history, keep=self.settings.keep_messages)
        tools = self.registry.schemas()

        for turn in range(1, self.settings.max_turns + 1):
            text, finish_reason, tool_acc, usage = self._one_turn(messages, tools, turn)
            if usage is not None:
                self.meter.add(usage)

            if finish_reason != "tool_calls":
                return AgentResult(text=text, turns=turn, finish_reason=finish_reason)

            # ① 先回填 assistant（含它说要调的工具），② 再回填每条工具结果。
            #    顺序反了 → 400：tool 消息必须紧跟在带 tool_calls 的 assistant 后面。
            messages.append(
                {
                    "role": "assistant",
                    "content": text,  # 保留模型说过的话（不能填 ""，否则下一轮会失忆）
                    "tool_calls": [
                        {
                            "id": slot["id"],
                            "type": "function",
                            "function": {
                                "name": slot["name"],
                                "arguments": slot["arguments"],
                            },
                        }
                        for slot in tool_acc.values()
                    ],
                }
            )

            for slot in tool_acc.values():
                self.trace_tool(slot["name"], slot["arguments"])
                result = self.registry.call(slot["name"], slot["arguments"])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": slot["id"],
                        "content": result,
                    }
                )

        logger.warning("达到最大轮数 %d，强行停止", self.settings.max_turns)
        return AgentResult(
            text=f"（超过最大轮数 {self.settings.max_turns}，停手）",
            turns=self.settings.max_turns,
            finish_reason=None,
            hit_max_turns=True,
        )

    # ── 一轮 ──────────────────────────────────────────────
    def _one_turn(
        self, messages: list[Message], tools: list[dict], turn: int
    ) -> tuple[str, str | None, dict[int, dict[str, Any]], Usage | None]:
        if not self.stream:
            return self._one_turn_blocking(messages, tools, turn)

        text, finish_reason = "", None
        tool_acc: dict[int, dict[str, Any]] = {}
        usage: Usage | None = None

        for chunk in self.llm.stream(messages, tools):
            if getattr(chunk, "usage", None):
                # ⚠️ usage 和 finish_reason 在**同一块**上，这里不能 continue
                usage = Usage.from_sdk(chunk.usage)
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                print(f"{DIM}{reasoning}", end="", flush=True)
            if delta.content:
                print(f"{RESET}{delta.content}", end="", flush=True)
                text += delta.content

            for tc in delta.tool_calls or []:
                slot = tool_acc.setdefault(tc.index, {"id": None, "name": None, "arguments": ""})
                if slot["id"] is None:
                    slot["id"] = tc.id
                if slot["name"] is None:
                    slot["name"] = tc.function.name
                slot["arguments"] += tc.function.arguments

        print(RESET, end="")
        self._report(turn, finish_reason, usage)
        return text, finish_reason, tool_acc, usage

    def _one_turn_blocking(
        self, messages: list[Message], tools: list[dict], turn: int
    ) -> tuple[str, str | None, dict[int, dict[str, Any]], Usage | None]:
        resp = self.llm.invoke(messages, tools)
        choice = resp.choices[0]
        text = choice.message.content or ""
        usage = Usage.from_sdk(resp.usage) if resp.usage else None
        tool_acc = {
            i: {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }
            for i, tc in enumerate(choice.message.tool_calls or [])
        }
        print(text)
        self._report(turn, choice.finish_reason, usage)
        return text, choice.finish_reason, tool_acc, usage

    # ── 展示 ──────────────────────────────────────────────
    def _report(self, turn: int, finish_reason: str | None, usage: Usage | None) -> None:
        if usage is None:
            print(f"\n[轮 {turn}] finish={finish_reason} | ⚠️ 没拿到 usage")
            return
        print(
            f"\n[轮 {turn}] finish={finish_reason}"
            f" | 命中 {usage.prompt_cache_hit_tokens} / 输入 {usage.prompt_tokens}"
            f" | 输出 {usage.completion_tokens}"
            f" | 花费 ≈ {estimate_cost(usage):.6f} 元"
        )

    def trace_tool(self, name: str, arguments: str) -> None:
        """工具调用的可见性 —— 没有这一行，用户根本不知道模型干了什么。"""
        if not self.settings.trace_tools:
            return
        try:
            args = json.loads(arguments) if arguments else {}
            pretty = json.dumps(args, ensure_ascii=False)
        except json.JSONDecodeError:
            pretty = arguments
        print(f"\n  ⚙️  {name}({pretty})", flush=True)
