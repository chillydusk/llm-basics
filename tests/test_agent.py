"""Agent 循环：用假 LLM 把整条链路跑一遍。

核心价值：**不用花一分钱就能验证循环的正确性**。
真实的 DeepSeek 只在验收时用一次，回归测试全部靠这个假客户端。

这里锁死的行为：
    ① 流式分片要按 index 累积，arguments 是拼接出来的
    ② assistant 消息必须在 tool 消息**之前**
    ③ 工具结果要作为 tool 消息喂回去
    ④ 达到 max_turns 要能停下来，不能死循环
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from study_assistant.agent import Agent
from study_assistant.config import Settings
from study_assistant.tools import Tool, ToolRegistry

# ── 假 LLM ────────────────────────────────────────────


def chunk(content=None, tool_calls=None, finish=None, usage=None, choices=True):
    delta = SimpleNamespace(content=content, reasoning_content=None, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(
        choices=[choice] if choices else [],
        usage=usage,
    )


def tool_delta(index=0, id=None, name=None, arguments=""):
    return SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments)
    )


class FakeLLM:
    """按剧本吐 chunk，并记下每次收到的 messages。"""

    def __init__(self, script: list[list[Any]]):
        self.script = script
        self.calls: list[list[dict]] = []

    def stream(self, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        yield from self.script[len(self.calls) - 1]

    def invoke(self, messages, tools=None):  # pragma: no cover - 阻塞分支另一条路
        raise AssertionError("测试里不测阻塞分支")


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        api_key="sk-test",
        project_root=Settings.model_fields["project_root"].default,
        notes_dir=Settings.model_fields["notes_dir"].default,
        prompt_path=Settings.model_fields["prompt_path"].default,
    )


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="echo",
            description="回声",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=lambda x: f"echo:{x}",
        )
    )
    return reg


HISTORY = [{"role": "system", "content": "人设"}, {"role": "user", "content": "问题"}]


# ── 用例 ──────────────────────────────────────────────


def test_direct_answer(settings, registry):
    llm = FakeLLM([[chunk(content="你好"), chunk(content="呀", finish="stop")]])
    result = Agent(llm, registry, settings).run(HISTORY)
    assert result.text == "你好呀"
    assert result.turns == 1


def test_tool_roundtrip(settings, registry):
    """模型要先调工具，拿到结果后再回答 —— 并且消息顺序不能错。"""
    script = [
        [
            chunk(
                tool_calls=[tool_delta(0, "call_1", "echo", '{"x":')],
                finish="tool_calls",
            ),
            chunk(tool_calls=[tool_delta(0, None, None, '"hi"}')]),
        ],
        [chunk(content="答案是 echo:hi", finish="stop")],
    ]
    llm = FakeLLM(script)
    result = Agent(llm, registry, settings).run(HISTORY)

    assert result.text == "答案是 echo:hi"
    assert result.turns == 2

    # 第二轮请求里必须多出两条消息，且顺序是 assistant → tool
    second = llm.calls[1]
    assistant, tool = second[-2], second[-1]
    assert assistant["role"] == "assistant"
    assert tool["role"] == "tool"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"x": "hi"}
    assert tool["content"] == "echo:hi"
    assert tool["tool_call_id"] == "call_1"


def test_unknown_tool_becomes_result(settings, registry):
    """调到不存在的工具，不能抛异常 —— 错误要当结果喂回去。"""
    script = [
        [chunk(tool_calls=[tool_delta(0, "c1", "nope", "{}")], finish="tool_calls")],
        [chunk(content="好的我换个方式", finish="stop")],
    ]
    llm = FakeLLM(script)
    Agent(llm, registry, settings).run(HISTORY)
    tool_msg = llm.calls[1][-1]
    assert "没有这个工具" in tool_msg["content"]


def test_max_turns_stops(settings, registry):
    """模型一直要工具 → 必须停，不能死循环。"""
    loop = [chunk(tool_calls=[tool_delta(0, "c", "echo", '{"x":"1"}')], finish="tool_calls")]
    llm = FakeLLM([loop] * 10)
    result = Agent(llm, registry, settings).run(HISTORY)
    assert result.hit_max_turns is True
    assert result.turns == settings.max_turns


def test_history_is_not_mutated(settings, registry):
    """run() 内部的 append 不能写回调用方的 history。"""
    llm = FakeLLM([[chunk(content="答", finish="stop")]])
    history = list(HISTORY)
    Agent(llm, registry, settings).run(history)
    assert history == HISTORY


def test_usage_is_metered(settings, registry):
    usage = SimpleNamespace(
        prompt_tokens=100,
        prompt_cache_hit_tokens=40,
        prompt_cache_miss_tokens=60,
        completion_tokens=20,
    )
    llm = FakeLLM([[chunk(content="答", finish="stop"), chunk(usage=usage, choices=False)]])
    agent = Agent(llm, registry, settings)
    agent.run(HISTORY)
    assert agent.meter.calls == 1
    assert agent.meter.total > 0


def test_trim_applied_before_request(settings, registry):
    long_history = [{"role": "system", "content": "人设"}] + [
        {"role": "user", "content": f"q{i}"} for i in range(40)
    ]
    llm = FakeLLM([[chunk(content="答", finish="stop")]])
    Agent(llm, registry, settings).run(long_history)
    assert len(llm.calls[0]) <= settings.keep_messages
