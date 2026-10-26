"""会话截断的不变量。

这五条任意一条破了，线上表现分别是：人设丢失、会话被污染、API 400。
所以每一条都单独一个测试 —— 失败时能立刻知道破的是哪条。
"""

from __future__ import annotations

from typing import Any

import pytest

from study_assistant.history import Message, new_history, trim

SYS: Message = {"role": "system", "content": "人设"}
USR: Message = {"role": "user", "content": "问"}
AST: Message = {"role": "assistant", "content": "答"}
TC: Message = {
    "role": "assistant",
    "content": "",
    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
}
TL: Message = {"role": "tool", "tool_call_id": "c1", "content": "结果"}


def chain(n: int) -> list[Message]:
    """system + n 组 (human, assistant)"""
    h: list[Message] = [SYS]
    for i in range(n):
        h += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    return h


def orphans(msgs: list[Message]) -> list[int]:
    """返回孤儿 tool 的下标：tool 消息前面不是带 tool_calls 的 assistant。"""
    bad = []
    for i, m in enumerate(msgs):
        if m.get("role") != "tool":
            continue
        prev: Any = msgs[i - 1] if i else None
        if not (prev and prev.get("role") == "assistant" and prev.get("tool_calls")):
            bad.append(i)
    return bad


def test_short_history_untouched():
    h = [SYS, USR]
    out = trim(h, keep=20)
    assert out == h
    assert out is not h, "必须返回新列表，否则后续 append 会污染会话"


def test_system_only():
    out = trim([SYS], keep=20)
    assert out == [SYS]


def test_exactly_keep():
    h = chain(9)  # 19 条
    assert len(h) == 19
    assert trim(h, keep=20) == h


def test_over_keep_drops_oldest():
    h = chain(10)  # 21 条
    out = trim(h, keep=20)
    assert len(out) == 20
    assert out[0] is SYS


def test_system_appears_once():
    h = chain(10)
    out = trim(h, keep=20)
    assert sum(1 for m in out if m["role"] == "system") == 1


def test_boundary_on_tool_calls_pair():
    """切点正好落在 ai(tool_calls) 上时，不能把它后面的 tool 弄成孤儿。"""
    h = [SYS, USR, AST, USR, TC, TL, AST]
    out = trim(h, keep=4)
    assert len(out) <= 4
    assert orphans(out) == []


def test_orphan_tool_at_head_dropped():
    h = [SYS, USR, AST, TC, TL, TL, AST]
    out = trim(h, keep=4)
    assert orphans(out) == []
    assert out[0] is SYS


def test_input_not_mutated():
    h = chain(30)
    before = len(h)
    trim(h, keep=20)
    assert len(h) == before


@pytest.mark.parametrize("n", [1, 5, 19, 20, 21, 100])
def test_never_exceeds_keep(n: int):
    out = trim(chain(n), keep=20)
    assert len(out) <= 20
    assert out[0] is SYS
    assert orphans(out) == []


def test_new_history_has_system_first():
    h = new_history("人设")
    assert h[0]["role"] == "system"
