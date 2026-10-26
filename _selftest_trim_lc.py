"""trim_lc() 自检 —— 🟢 我写好的，你只管把 langchain_agent.trim_lc 写对。

跑法（Git Bash，项目根目录 D:/桌面/Agent开发/agents/study-assistant）：
    uv run python _selftest_trim_lc.py

它检查的是【不变量】，跟 _selftest_trim.py 同构 —— 只把 dict 换成了消息对象。
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

try:
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )
except ImportError:
    print("❌ 还没装 langchain —— 先跑 `uv add langchain-openai langchain`")
    raise SystemExit(1)

try:
    from llm_basics.langchain_agent import trim_lc
except ImportError as e:
    print(f"❌ 导不进来：{e}\n   先确认 langchain_agent.py 里有 trim_lc 函数")
    raise SystemExit(1)

SYS = SystemMessage("人设")
USR = HumanMessage("问")
AST = AIMessage("答")
TC = AIMessage(
    content="",
    tool_calls=[{"name": "read_notes", "args": {"filename": "demo.md"},
                 "id": "c1", "type": "tool_call"}],
)
TL = ToolMessage(content="内容", tool_call_id="c1")


def paint(m):
    return m.type + ("(tc)" if getattr(m, "tool_calls", None) else "")


def orphans(out):
    """type == "tool" 但前一条不是带 tool_calls 的 ai → 孤儿。"""
    bad = []
    for i, m in enumerate(out):
        if getattr(m, "type", None) != "tool":
            continue
        prev = out[i - 1] if i > 0 else None
        ok = prev is not None and getattr(prev, "type", None) == "ai" and getattr(
            prev, "tool_calls", None
        )
        if not ok:
            bad.append(i)
    return bad


FAILS = []


def check(title, history, keep, expect_len):
    before = len(history)
    out = trim_lc(history, keep)
    bad = []

    if len(history) != before:
        bad.append(f"你改动了传进来的 messages（{before} → {len(history)} 条）")
    if not isinstance(out, list):
        bad.append(f"返回值不是 list，是 {type(out).__name__}")
        out = []
    elif out is history:
        bad.append("返回的就是 messages 本身 —— 后面 append 会污染会话状态")
    else:
        if not out or getattr(out[0], "type", None) != "system":
            bad.append("结果第一条不是 system")
        elif out[0] is not history[0]:
            bad.append("结果第一条不是原来那条 system（你新建了一条）")
        n_sys = sum(1 for m in out if getattr(m, "type", None) == "system")
        if n_sys != 1:
            bad.append(f"system 出现了 {n_sys} 次，应该正好 1 次")
        bad += [f"第 {i} 条是孤儿 tool" for i in orphans(out)]
        if len(out) != expect_len:
            bad.append(f"长度应为 {expect_len}，实际 {len(out)}")
        if len(out) > keep:
            bad.append(f"长度 {len(out)} 超过 keep={keep}")

    mark = "✅" if not bad else "❌"
    print(f"\n{mark} {title}   (keep={keep})")
    print(f"   输入 {[paint(m) for m in history]}")
    print(f"   输出 {[paint(m) for m in out]}")
    for b in bad:
        print(f"   ⚠️  {b}")
    if bad:
        FAILS.append(title)


def chain(n):
    """system + n 组 (human, ai)"""
    h = [SYS]
    for i in range(n):
        h += [HumanMessage(f"q{i}"), AIMessage(f"a{i}")]
    return h


check("A 短历史（没超 keep）不动", [SYS, USR], 20, 2)
check("B 只有 system", [SYS], 20, 1)
check("C 19 条，没到 20", chain(9), 20, 19)
check("D 21 条，超了 1 条", chain(10), 20, 20)
check("E 边界正好切在 ai(tool_calls) 上（不能丢配对）", [SYS, USR, AST, USR, TC, TL, AST], 4, 4)
check("F 切完开头是孤儿 tool（得丢掉）", [SYS, USR, AST, TC, TL, TL, AST], 4, 2)
check("G 超长历史", chain(30), 20, 20)

print("\n" + "─" * 56)
if FAILS:
    print(f"❌ {len(FAILS)} / 7 项没通过：{'、'.join(FAILS)}")
    raise SystemExit(1)
print("✅ 7 / 7 全过，trim_lc 可以用了")
