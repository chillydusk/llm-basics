"""会话历史：构造 + 截断。

截断要守住三条底线（Part F 用一次 400 换来的）：

    ① 第一条 system（人设）永远留着
    ② 返回**新列表**，不能是传进来的那个 —— 否则 run 里的 append 会污染会话状态
    ③ 开头不能是孤儿 tool 消息（它的配对 assistant 被裁掉了 → API 400）

`tests/test_history.py` 里有 8 条不变量断言，改这个文件先跑测试。
"""

from __future__ import annotations

from typing import Any

Message = dict[str, Any]


def new_history(system_prompt: str) -> list[Message]:
    return [{"role": "system", "content": system_prompt}]


def trim(history: list[Message], keep: int = 20) -> list[Message]:
    """超过 keep 条就丢最早的，保住 system，清掉孤儿 tool。"""
    if len(history) <= keep:
        return history.copy()

    tail = history[-(keep - 1) :]
    while tail and tail[0].get("role") == "tool":
        tail.pop(0)

    # 切完之后可能只剩 tool 了（极端情况）——那样连 system 都补不回来
    return history[:1] + tail


def append_user(history: list[Message], text: str) -> None:
    history.append({"role": "user", "content": text})


def append_assistant(history: list[Message], text: str | None) -> None:
    history.append({"role": "assistant", "content": text or ""})
