"""study-assistant · Part E：手写 Agent 循环。

从「单轮工具助手」升级成「会自己转圈的 Agent」：

    问一次 → 模型说要工具 → 执行 → 回填 → 再问 → …… → 模型说不用了（stop）→ 收工

🟢 已写好的：环境/定价/工具说明书/三个工具本体/分发表/流式显示
🔴 要你写的：run_agent() 里三处标了「填空」的地方
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

DIM = "\033[2m"  # 变暗（思考区开）
RESET = "\033[0m"  # 恢复默认（思考区关）

# 北京时间固定 UTC+8（中国不实行夏令时，所以用固定偏移就够，不必装 tzdata）
BEIJING = timezone(timedelta(hours=8))

# ── DeepSeek 定价：元 / 百万 token ────────────────────────────────
# 查证日期 2026-10-08，官方定价自 2026-09-10 12:00 起生效。
# 来源：https://api-docs.deepseek.com/zh-cn/quick_start/pricing
PRICE_CACHE_HIT = 0.02  # 输入 · 缓存命中（空闲时段）
PRICE_CACHE_MISS = 1.00  # 输入 · 缓存未命中（空闲时段）
PRICE_OUTPUT = 4.00  # 输出（空闲时段）
PEAK_MULTIPLIER = 2.0  # 高峰时段 = 空闲时段 × 2
TOKENS_PER_UNIT = 1_000_000  # 单价按「每百万 token」计

load_dotenv(".env")  # 显式路径（坑 #27：find_dotenv 在管道模式下会崩）

API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not API_KEY:
    raise SystemExit("没找到 DEEPSEEK_API_KEY，检查 .env 文件")

client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com")


def is_peak_now(now: datetime | None = None) -> bool:
    """当前是否处于高峰时段。

    高峰 = 周一至周五 9:00–12:00、14:00–18:00（北京时间），
    其余时段（含周末全天）都是空闲时段，价格是高峰的一半。

    已知偏差：官网把「中国法定节假日」也算作空闲时段，但本地代码拿不到
    节假日表，这里会把节假日误判成高峰 —— 结果是费用被**高估**。
    （保守方向：宁可估高，也别让你以为花得比实际少）
    """
    now = now or datetime.now(BEIJING)
    if now.weekday() >= 5:  # 5 = 周六, 6 = 周日
        return False
    hour = now.hour + now.minute / 60  # 折算成小数小时，好写区间
    return (9 <= hour < 12) or (14 <= hour < 18)


def estimate_cost(usage) -> float:
    """估算单次调用的费用，单位：元。

    计费口径（官网原话：费用 = token 数 × 单价）：
        输入分成两笔算 —— 缓存命中的部分便宜（0.02 元/百万），
        未命中的部分按正常输入价（1 元/百万）；输出单独一笔（4 元/百万）。
        三笔相加，再按峰谷时段决定要不要 ×2。
    """
    cost = (
        usage.prompt_cache_hit_tokens * PRICE_CACHE_HIT
        + usage.prompt_cache_miss_tokens * PRICE_CACHE_MISS
        + usage.completion_tokens * PRICE_OUTPUT
    ) / TOKENS_PER_UNIT

    return cost * PEAK_MULTIPLIER if is_peak_now() else cost


# ══════════════════════════════════════════════════════════════════
#  工具层（从 toolcall_demo.py 搬过来，🟢 已写好）
# ══════════════════════════════════════════════════════════════════

# —— ① 工具说明书：告诉模型"世界上有这三个工具" ——
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "获取当前日期和时间",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "做两个数的加法",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "第一个加数"},
                    "b": {"type": "number", "description": "第二个加数"},
                },
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_notes",
            "description": "读取我的学习笔记文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "笔记文件名"},
                },
                "required": ["filename"],
            },
        },
    },
]


# —— ② 工具本体：真正干活的函数 ——
def get_time() -> str:
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")


def calculate(a: float, b: float) -> str:
    return str(a + b)


def read_notes(filename: str) -> str:
    # ⚠️ 已知缺口：这里没做路径校验，传 "../../.env" 能读到秘密。
    #    W8 会专门修这个（路径穿越）。
    path = Path("notes") / filename
    if not path.exists():
        return f"找不到文件：{filename}"
    return path.read_text(encoding="utf-8")


# —— ③ 分发表：名字 → 函数。模型给名字，我们查表 ——
TOOL_MAP = {
    "get_time": get_time,
    "calculate": calculate,
    "read_notes": read_notes,
}


# ══════════════════════════════════════════════════════════════════
#  Agent 循环 —— 🔴 这一整块是你要写的
# ══════════════════════════════════════════════════════════════════


def run_agent(question: str, max_turns: int = 5) -> str:
    """反复问模型，直到它不再要工具为止。返回它最后说的那段话。"""
    messages = [{"role": "user", "content": question}]

    for turn in range(1, max_turns + 1):
        stream = client.chat.completions.create(
            model="deepseek-flash",
            messages=messages,
            tools=TOOLS,
            stream=True,
            stream_options={"include_usage": True},
        )

        text, finish_reason, tool_acc, usage = "", None, {}, None

        for chunk in stream:
            if chunk.usage is not None:
                # ⚠️ 实测：usage 和 finish_reason 挤在**同一块**上，
                #    且这一块 choices 不为空。所以这里不能 continue ——
                #    下面取 finish_reason 正要靠这一块。
                usage = chunk.usage
            if not chunk.choices:  # 防御性写法：万一有的块没 choices
                continue

            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta

            rc = getattr(delta, "reasoning_content", None)
            if rc:  # 思考链：暗色实时打
                print(f"{DIM}{rc}", end="", flush=True)
            if delta.content:  # 正文：正常色实时打
                print(f"{RESET}{delta.content}", end="", flush=True)
                text += delta.content

            for tc in delta.tool_calls or []:
                slot = tool_acc.setdefault(
                    tc.index, {"id": None, "name": None, "arguments": ""}
                )

                if slot["id"] is None:  # ③ 第一片才带 id
                    slot["id"] = tc.id

                if slot["name"] is None:  # ④ 第一片才带 name
                    slot["name"] = tc.function.name

                slot["arguments"] += tc.function.arguments  # ⑤ 永远拼接

        print(RESET, end="")  # 关掉暗色/收尾
        if usage is not None:
            print(
                f"\n[轮 {turn}] finish={finish_reason}"
                f" | 本轮花费 ≈ {estimate_cost(usage):.6f} 元"
            )
        else:
            print(f"\n[轮 {turn}] finish={finish_reason} | ⚠️ 没拿到 usage")

        if finish_reason != "tool_calls":
            return text

        # ─────────── 🔴 填空 2：把「模型这一程想干什么」记进历史 ───────────
        #
        # 形状必须是一条 assistant 消息（D.5 你手工拼过一次，这次要在循环里拼）：
        #   {"role": "assistant", "content": ?, "tool_calls": [ ... ]}
        #
        # 其中 tool_calls 里每一项要还原成：
        #   {"id": 槽位里的 id,
        #    "type": "function",
        #    "function": {"name": 槽位里的 name, "arguments": 槽位里的 arguments}}
        #
        # ⚠️ 必须是【攒完整】的那份 —— 把分片直接塞回去会被 API 拒绝。
        #
        # ⚠️ content 传什么？三个选择，探针实测 API 全都收：
        #      None  —— 卡上骨架写的，最省事，但会【抹掉】模型说过的话
        #      ""    —— 同上
        #      text  —— 本程累积到的原文
        #              （实测深色区里模型会先说"我来同时处理这两件事。"再调工具）
        #    想一想：F 部分要接多轮对话，历史里少了这半句，下一程的模型会怎么理解？
        #    （选哪个都跑得通，但你要能说出理由）
        # ───────────────────────────────────────────────────────────────
        assistant_message = {
            "role": "assistant",
            "content": text,  # ⚠️ 这里选 text，保留模型说过的话
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
        messages.append(assistant_message)

        # ─────────── 🔴 填空 3：逐个执行工具，把结果回填 ───────────
        #
        # 每一条 tool 消息的形状（D.5 你手工拼过一次）：
        #   {"role": "tool", "tool_call_id": 对应的 id, "content": 执行结果（字符串）}
        #
        # 执行三件套（D.4 你做过的，原样搬）：
        #   ① func = TOOL_MAP[槽位里的 name]        —— 用名字当钥匙，不能写死
        #   ② args = json.loads(槽位里的 arguments)  —— 字符串 → 字典
        #   ③ result = func(**args)                 —— 字典拆开喂进去
        #
        # ⚠️ 要遍历的是【槽位们】（tool_acc.values()），不是 delta.tool_calls ——
        #    那个变量早出作用域了。
        #
        # ⚠️ 探针实测：漏掉 tool 消息 → 400
        #    "An assistant message with 'tool_calls' must be followed by
        #     tool messages responding to each 'tool_call_id'."
        #    所以本轮有几个 tool_call，就必须补几条 tool 消息，一个都不能少。
        # ───────────────────────────────────────────────────────────
        for slot in tool_acc.values():
            func = TOOL_MAP[slot["name"]]
            args = json.loads(slot["arguments"])
            result = func(**args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": slot["id"],
                    "content": result,
                }
            )
    return f"（超过最大轮数 {max_turns}，停手）"


def main() -> None:
    # 这题必须真读文件才能答对 —— 模型不可能猜出 7319
    question = "读一下 notes/demo.md，告诉我实验编号是多少。"

    print(f"你 > {question}\n")
    answer = run_agent(question)
    print(f"\n{'─' * 56}\n🎯 run_agent 返回：{answer}")


if __name__ == "__main__":
    main()
