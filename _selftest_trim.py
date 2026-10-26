"""trim() 自检 —— 🟢 我写好的，你只管把 llm_basics.trim 写对。

跑法（Git Bash，项目根目录 D:/桌面/Agent开发/agents/study-assistant）：
    uv run python _selftest_trim.py

它检查的是【不变量】，不管你内部怎么实现。
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

try:
    from llm_basics import trim
except ImportError:
    print("❌ llm_basics 里还没有 trim 函数 —— 先写它，再跑这个")
    raise SystemExit(1)

SYS = {"role": "system", "content": "人设"}
USR = {"role": "user", "content": "问"}
AST = {"role": "assistant", "content": "答"}
TC = {
    "role": "assistant",
    "content": None,
    "tool_calls": [{"id": "c1", "type": "function",
                    "function": {"name": "read_notes", "arguments": "{}"}}],
}
TL = {"role": "tool", "tool_call_id": "c1", "content": "内容"}


def paint(m):
    return m["role"] + ("(tc)" if m.get("tool_calls") else "")


def orphans(out):
    """role=tool 但前一条不是带 tool_calls 的 assistant → 孤儿。"""
    bad = []
    for i, m in enumerate(out):
        if m.get("role") != "tool":
            continue
        prev = out[i - 1] if i > 0 else None
        ok = prev is not None and prev.get("role") == "assistant" and prev.get("tool_calls")
        if not ok:
            bad.append(i)
    return bad


FAILS = []


def check(title, history, keep, expect_len):
    before = len(history)
    out = trim(history, keep)
    bad = []

    if len(history) != before:
        bad.append(f"你改动了传进来的 history（{before} → {len(history)} 条）")
    if not isinstance(out, list):
        bad.append(f"返回值不是 list，是 {type(out).__name__}")
        out = []
    elif out is history:
        bad.append("返回的就是 history 本身 —— 后面 append 会污染会话状态")
    else:
        if not out or out[0].get("role") != "system":
            bad.append("结果第一条不是 system")
        elif out[0] is not history[0]:
            bad.append("结果第一条不是原来那条 system（你新建了一条）")
        n_sys = sum(1 for m in out if m.get("role") == "system")
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
    """system + n 组 (user, assistant)"""
    h = [SYS]
    for i in range(n):
        h += [{"role": "user", "content": f"q{i}"},
              {"role": "assistant", "content": f"a{i}"}]
    return h


check("A 短历史（没超 keep）不动", [SYS, USR], 20, 2)
check("B 只有 system", [SYS], 20, 1)
check("C 19 条，没到 20", chain(9), 20, 19)
check("D 21 条，超了 1 条", chain(10), 20, 20)
check("E 边界正好切在 assistant(tc) 上（不能丢配对）", [SYS, USR, AST, USR, TC, TL, AST], 4, 4)
check("F 切完开头是孤儿 tool（得丢掉）", [SYS, USR, AST, TC, TL, TL, AST], 4, 2)
check("G 超长历史", chain(30), 20, 20)

print("\n" + "─" * 56)
if FAILS:
    print(f"❌ {len(FAILS)} / 7 项没通过：{'、'.join(FAILS)}")
    raise SystemExit(1)
print("✅ 7 / 7 全过，trim 可以用了")
