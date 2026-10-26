"""study-assistant · Part G：LangChain 复刻。

同一个人设、同一套工具、同一条链路 —— 用框架再实现一遍，然后回答一个问题：

    框架到底替你省了什么？又瞒着你扔了什么？

对照物就是你自己写的 src/llm_basics/__init__.py（Part E/F）。

🟢 已写好：入口 main()、费用换算 print_usage()
🔴 你要写：build_llm() / trim_lc() / get_response() / run_agent()

跑法（Git Bash，项目根目录 D:/桌面/Agent开发/agents/study-assistant）：

    uv add langchain-openai langchain          ← G.1，只做一次（约 1~3 分钟）
    uv run python -m llm_basics.langchain_agent          ← 交互
    uv run python -m llm_basics.langchain_agent "读一下 demo.md"

⚠️ 本文所有「实测」结论的版本：langchain 1.4.7 / langchain-openai / Python 3.13，
   实测日期 2026-10-26，模型 deepseek-flash。
"""

import os
import sys

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from openai import OpenAIError

from llm_basics import TOOL_MAP, TOOLS, estimate_cost, load_prompt

load_dotenv(".env")  # llm_basics 里已经 load 过一次，这里显式再来一次，单独跑本文件也不炸


# ══════════════════════════════════════════════════════════════════
#  G-2 · 造模型　🔴 你写（约 8 行）
# ══════════════════════════════════════════════════════════════════


def build_llm():
    """G-2 · 造一个「挂着工具说明书」的模型对象。

    三件套（实测能接 DeepSeek）：

        llm = ChatOpenAI(
            model="deepseek-flash",
            api_key=...,                       # 从 .env 读，别写死
            base_url="https://api.deepseek.com",
        )
        return llm.bind_tools(???)

    ⚠️ bind_tools 的 ??? 传什么，两条路，实测差别很大：

        路 A：bind_tools(TOOLS)      ← TOOLS 就是你 Part D 手写的那份 dict 列表
              实测：透传后 description、参数说明【一字不差】保留。

        路 B：bind_tools([get_time, calculate, read_notes])   ← 传函数对象
              实测：三个 description 全变成 ""（空字符串），
                    a / b 的「第一个加数」说明也没了。
              原因：框架是从【函数名 + 类型注解 + docstring】里抠说明书的，
                    你那三个函数没写 docstring → 抠出来就是空的。

    🎯 建议：主流程走 A —— 说明书写法两边一致，差异才只落在「循环 / 拼装」上，对比才公平。
        B 单独跑一次看 schema 变空，正好回答「框架自动生成 schema 省了什么、丢了什么」。

    ── 验证（Git Bash，项目根目录）──
        uv run python -c "from llm_basics.langchain_agent import build_llm; print(build_llm())"
        预期：打印出一个 ChatOpenAI(...) 对象，不报错
    """
    # 🔴 在这里写  ↓
    API_KEY = os.getenv("DEEPSEEK_API_KEY")
    if not API_KEY:
        raise SystemExit("没找到 DEEPSEEK_API_KEY，检查 .env 文件")

    llm = ChatOpenAI(
        model="deepseek-flash",
        api_key=API_KEY,  # 从 .env 读，别写死
        base_url="https://api.deepseek.com",
    )

    return llm.bind_tools(TOOLS)


# ══════════════════════════════════════════════════════════════════
#  G-3 · 截断的框架版　🔴 你写（约 10 行）
# ══════════════════════════════════════════════════════════════════


def trim_lc(messages: list, keep: int = 20) -> list:
    """G-3 · 把 Part F 的 trim() 翻译成 LangChain 版。

    为什么不能直接 from llm_basics import trim？
        因为 trim 里写的是 history[0]["role"]。
        LangChain 的消息是【对象】，取角色要用 .type，拿对象当下标 → TypeError。

    ⚠️ 角色名也变了（实测 langchain_core 1.7.0）：

        SystemMessage  → .type == "system"
        HumanMessage   → .type == "human"      ← 不是 "user"
        AIMessage      → .type == "ai"         ← 不是 "assistant"
        ToolMessage    → .type == "tool"

    其余四条底线跟 Part F 一模一样：
        · 长度 ≤ keep
        · 第一条是原来那条 system（不是新建的）
        · system 只出现一次
        · 没有孤儿 tool（开头若是 ToolMessage 就丢掉）
        · 不修改传进来的列表，且返回【新列表】（run_agent 要往后 append）

    🎯 写完你会得到一个反直觉的结论：**框架并没有让这段代码变短**。
       消息对象化带来的是类型安全，代价是你自己的工具函数全得改一遍。

    ── 自检（Git Bash，项目根目录）──
        uv run python _selftest_trim_lc.py
        7 项全过才算对。它跟 _selftest_trim.py 是同构的，只把 dict 换成了消息对象。
    """
    # 🔴 在这里写  ↓
    if len(messages) <= keep:
        return messages.copy()
    tail = messages[-(keep - 1) :]
    while tail and tail[0].type == "tool":  # ← 就这儿
        tail.pop(0)
    return messages[:1] + tail


# ══════════════════════════════════════════════════════════════════
#  G-4 · 问一次模型　🔴 你写（约 15 行）
# ══════════════════════════════════════════════════════════════════


def get_response(llm, messages: list, stream: bool = True) -> AIMessage:
    """G-4 · 问一次模型，返回一个【已经拼好】的 AIMessage。

    ── stream=False：一行就够 ──────────────────────────────────────
        return llm.invoke(messages)

        实测返回的 resp.tool_calls 长这样（注意 args 已经是 dict 了！）：

            [{'name': 'read_notes',
              'args': {'filename': 'demo.md'},
              'id': 'call_00_ET_cF1ESrbhW6K97Lm16Foq5514',
              'type': 'tool_call'}]

        对照 Part E 你干过的活：分槽 setdefault、拼接 arguments、json.loads
        —— 全没了。这是框架最实在的一处「省」。

    ── stream=True：边打边累加 ────────────────────────────────────
        实测（问「读一下 demo.md」）收到 11 个带 tool_call_chunks 的片：

            片 1  {'name': 'read_notes', 'args': '',  'id': 'call_...', 'index': 0}
            片 2  {'name': None,         'args': '{', 'id': None, ...}
            片 3  {'name': None,         'args': '"', ...}
            ...
            片 11 {'name': None,         'args': '}', ...}

        ⚠️ 划重点：**分片根本没有消失**。
           框架没有替你消灭分片，它替你做的是【累加】：

               full = chunk if full is None else full + chunk

           AIMessageChunk 的 __add__ 会把同 index 的 args 字符串接起来；
           累加完之后 full.tool_calls 自动就是上面那个解析好的 dict。

        所以你要写的只有三件事：
            ① for chunk in llm.stream(messages):
            ② chunk.content 有值就实时 print（记得 flush）
            ③ 把 chunk 往 full 上累加

        ⚠️ usage_metadata 只有【累加完之后】的 full 上才有值，别在循环里读。
        ⚠️ 思考链：Part E 里你从 delta.reasoning_content 拿到的那段深色文字，
           在 LangChain 里默认【拿不到】—— 实测 additional_kwargs 只有 refusal，
           content_blocks 也只有 text，但 usage 里 reasoning token 数照算。
           这是 Part G 最重要的一个发现，等下写进对比表。

    ── 验证 ──
        uv run python -m llm_basics.langchain_agent "现在几点"
    """
    # 🔴 在这里写  ↓
    if not stream:
        return llm.invoke(messages)  # ← 这行直接给你，没坑

    # 流式：三件事
    full = AIMessageChunk(content="")  # 空壳打底
    for chunk in llm.stream(messages):
        if chunk.content:  # ② 有正文就实时打
            print(chunk.content, end="", flush=True)
        full += chunk  # ③ 累加到 full 上
    print()  #    收尾换行
    return full


# ══════════════════════════════════════════════════════════════════
#  G-5 · 工具循环　🔴 你写（约 25 行）· 这是 G 的核心
# ══════════════════════════════════════════════════════════════════


def run_agent(llm, history: list, max_turns: int = 5, stream: bool = True) -> str:
    """G-5 · 反复问模型，直到它不再要工具为止。返回它最后说的那段话。

    拿 Part E 的 run_agent 逐句对照着写，工作量少一大截：

        手写版（E）                            LangChain 版（G）
        ────────────────────────────────────────────────────────────────
        messages = trim(history)               messages = trim_lc(history)
        自己调 client.chat...create(...)        resp = get_response(llm, messages, stream)
        自己扫 delta 找 finish_reason           resp.response_metadata["finish_reason"]
        if finish_reason != "tool_calls":      if not resp.tool_calls:      ← 更直观
            return text                            return resp.content
        自己拼 assistant_message dict           messages.append(resp)       ← 整个对象直接塞
        自己 json.loads(arguments)             tc["args"] 已经是 dict
        自己 TOOL_MAP[name](**args)            TOOL_MAP[tc["name"]](**tc["args"])
        自己拼 {"role":"tool",...}             ToolMessage(content=..., tool_call_id=tc["id"])

    ⚠️ 两件「框架不会替你做」的事，别指望它：

        ① 执行工具 —— bind_tools 只把说明书挂上去，绝不会替你调函数。
        ② 多轮记忆 —— history 仍然要 main() 自己维护（除非上 create_agent + checkpointer）。

    ⚠️ append 顺序：先 messages.append(resp)，再 append 各条 ToolMessage。
       反了 → 400（跟 Part E 那条 error 一模一样）。

    ⚠️ 别用 `if resp.response_metadata["finish_reason"] != "tool_calls"` 当退出条件 ——
       能跑，但 DeepSeek 偶尔会给 "length"，那你就永远退不出来了。
       用 `if not resp.tool_calls` 更稳：有工具就转，没工具就收工。

    🎯 注意 messages 必须是 trim_lc 返回的【新列表】，
       否则你这里 append 的中间消息会污染 main() 里的 history。

    ── 验证 ──
        uv run python -m llm_basics.langchain_agent "读一下 demo.md"
        预期：工具被调用 → 答出 7319
    """
    # 🔴 在这里写  ↓
    messages = trim_lc(history)
    for turn in range(1, max_turns + 1):
        resp = get_response(llm, messages, stream)
        finish_reason = resp.response_metadata.get("finish_reason")
        print_usage(resp.usage_metadata, turn, finish_reason)

        if not resp.tool_calls:  # 不再要工具 → 收工
            return resp.content

        messages.append(resp)  # 先 append assistant 的回答
        for tc in resp.tool_calls:  # 再 append 各条 tool 调用
            print(f"\n  ⚙️  {tc['name']}({tc['args']})", flush=True)  # 🟢 展示用
            tool_func = TOOL_MAP.get(tc["name"])
            if tool_func is None:
                # 不 raise：把错误当工具结果喂回去，让模型自己改
                tool_result = f"没有这个工具：{tc['name']}"
            else:
                tool_result = tool_func(**tc["args"])
            messages.append(
                ToolMessage(content=tool_result, tool_call_id=tc["id"])
            )
    return f"（超过最大轮数 {max_turns}，停手）"
# ══════════════════════════════════════════════════════════════════
#  🟢 以下是已写好的：费用换算 + 入口
# ══════════════════════════════════════════════════════════════════


def print_usage(usage: dict | None, turn: int, finish_reason) -> None:
    """把 LangChain 的 usage_metadata 换算成 Part B 的计费口径。

    ⚠️ 为什么需要这层：estimate_cost() 吃的是裸 SDK 的 usage 对象，字段是
       usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens / completion_tokens；
       LangChain 给的是 dict，字段名全变了：

           input_tokens                      = 总输入（实测等同裸 SDK 的 prompt_tokens）
           input_token_details["cache_read"] = 其中命中的部分
           → 未命中 = input_tokens - cache_read
           output_tokens                     = 输出

       （依据：langchain_openai 的 _create_usage_metadata 源码，它自己算
         service tier 时也是 input_tokens - cache_read）

    这层「适配」本身就是对比表里的一行：**框架换了字段名，你的计费代码得跟着改**。
    """
    if not usage:
        print(f"\n[轮 {turn}] finish={finish_reason} | ⚠️ 没拿到 usage")
        return

    from types import SimpleNamespace

    cache_read = (usage.get("input_token_details") or {}).get("cache_read", 0) or 0
    shim = SimpleNamespace(
        prompt_cache_hit_tokens=cache_read,
        prompt_cache_miss_tokens=usage.get("input_tokens", 0) - cache_read,
        completion_tokens=usage.get("output_tokens", 0),
    )
    print(
        f"\n[轮 {turn}] finish={finish_reason}"
        f" | 本轮花费 ≈ {estimate_cost(shim):.6f} 元"
    )


def main() -> None:
    """🟢 入口。从 Part F 的 main() 搬过来，只换了消息类型和调用的函数。"""
    llm = build_llm()
    history = [SystemMessage(content=load_prompt())]

    if sys.argv[1:]:
        question = " ".join(sys.argv[1:])
        history.append(HumanMessage(content=question))
        try:
            run_agent(llm, history)
        except OpenAIError as e:
            print(f"\n调用失败：{e}")
            raise SystemExit(1)
        return

    print("输入 /exit 退出，/clear 清空对话\n")
    while True:
        try:
            user_input = input("你 > ").strip()
            if not user_input:
                continue
            if user_input in ("/exit", "/quit"):
                break
            if user_input == "/clear":
                history = [SystemMessage(content=load_prompt())]
                print("会话已清空，只保留人设。")
                continue

            history.append(HumanMessage(content=user_input))
            answer = run_agent(llm, history)
            history.append(AIMessage(content=answer))

        except EOFError:
            print("\n退出（EOF）")
            return
        except KeyboardInterrupt:
            print("\n退出（中断）")
            raise SystemExit(130)
        except OpenAIError as e:
            print(f"\n调用失败：{e}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
