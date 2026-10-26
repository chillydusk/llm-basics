"""命令行入口。

    study-assistant chat                交互模式（默认）
    study-assistant ask "问题"          单问一次
    study-assistant search "查询"       只测检索，不花 token
    study-assistant tools               看当前注册了哪些工具（含 MCP）
    study-assistant mcp list            看 MCP 服务器状态
    study-assistant mcp serve           把自己当 MCP 服务器起起来
    study-assistant doctor              环境自检
    study-assistant version

退出码约定（写脚本的人会感谢你）：

    0    正常
    1    模型/网络错误
    2    配置错误
    130  用户 Ctrl+C
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from study_assistant import __version__, rag
from study_assistant.agent import Agent
from study_assistant.config import ConfigError, Settings
from study_assistant.errors import LLMError, StudyAssistantError
from study_assistant.history import append_assistant, append_user, new_history
from study_assistant.llm import LLMClient
from study_assistant.logging_setup import quiet_noisy_libraries, setup_logging
from study_assistant.tools import ToolRegistry, builtin_tools, notes_tools
from study_assistant.usage import CostMeter

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
#  组装运行时
# ══════════════════════════════════════════════════════════════════


def build_registry(settings: Settings, *, use_mcp: bool = True):
    """内置工具 + MCP 工具（起不来就本地兜底）。

    这就是 MCP 的价值所在：**同一批工具，换一种传输方式接入**。
    本地模式下 notes 工具是普通函数；MCP 模式下它们在另一个进程里，
    通过 JSON-RPC 说话 —— 但对 Agent 来说长得一模一样。
    """
    registry = ToolRegistry()
    registry.extend(builtin_tools())

    manager = None
    if use_mcp and settings.enable_mcp:
        from study_assistant.mcp_client import MCPManager

        manager = MCPManager.from_config(settings.mcp_config_path)
        registry.extend(manager.to_tools(), disambiguate=True)
        if manager.failures:
            logger.warning(
                "有 %d 个 MCP 服务器不可用，已降级：%s",
                len(manager.failures),
                "、".join(manager.failures),
            )

    # 兜底：MCP 没把 notes 工具带进来（关掉了 / 起不来），就用本地实现
    if "search_notes" not in registry:
        registry.extend(notes_tools(settings.notes_dir))
        logger.info("notes 工具走本地模式")

    return registry, manager


def build_agent(settings: Settings, *, use_mcp: bool = True, stream: bool = True):
    registry, manager = build_registry(settings, use_mcp=use_mcp)
    meter = CostMeter()
    agent = Agent(LLMClient(settings), registry, settings, meter, stream=stream)
    return agent, meter, manager


# ══════════════════════════════════════════════════════════════════
#  子命令
# ══════════════════════════════════════════════════════════════════


def cmd_ask(args, settings: Settings) -> int:
    question = " ".join(args.question).strip()
    if not question:
        print("问题是空的", file=sys.stderr)
        return 2

    agent, meter, manager = build_agent(
        settings, use_mcp=not args.no_mcp, stream=not args.no_stream
    )
    history = new_history(settings.prompt_path.read_text(encoding="utf-8"))
    append_user(history, question)
    try:
        result = agent.run(history)
    finally:
        if manager:
            manager.close()
    print(f"\n{meter.summary()}")
    return 0 if not result.hit_max_turns else 1


def cmd_chat(args, settings: Settings) -> int:
    agent, meter, manager = build_agent(settings, use_mcp=not args.no_mcp)
    history = new_history(settings.prompt_path.read_text(encoding="utf-8"))

    print("study-assistant｜输入 /exit 退出，/clear 清空，/tools 看工具，/cost 看花费\n")
    try:
        while True:
            try:
                line = input("你 > ").strip()
            except EOFError:
                print("\n退出（EOF）")
                break
            except KeyboardInterrupt:
                print("\n退出（中断）")
                return 130

            if not line:
                continue
            if line in ("/exit", "/quit"):
                break
            if line == "/clear":
                history = new_history(settings.prompt_path.read_text(encoding="utf-8"))
                print("会话已清空，只保留人设。")
                continue
            if line == "/tools":
                print(agent.registry.describe())
                continue
            if line == "/cost":
                print(meter.summary())
                continue

            append_user(history, line)
            try:
                result = agent.run(history)
            except LLMError as e:
                print(f"\n调用失败：{e}", file=sys.stderr)
                continue
            append_assistant(history, result.text)
    finally:
        if manager:
            manager.close()
    print(f"\n{meter.summary()}")
    return 0


def cmd_search(args, settings: Settings) -> int:
    query = " ".join(args.query).strip()
    if not query:
        print("查询是空的", file=sys.stderr)
        return 2
    print(rag.index_stats(settings.notes_dir))
    print()
    print(rag.format_hits(query, rag.search(query, notes_dir=settings.notes_dir)))
    return 0


def cmd_tools(args, settings: Settings) -> int:
    registry, manager = build_registry(settings, use_mcp=not args.no_mcp)
    print(registry.describe())
    if manager:
        print("\nMCP：")
        print(manager.describe())
        manager.close()
    return 0


def cmd_mcp(args, settings: Settings) -> int:
    if args.mcp_cmd == "serve":
        from study_assistant.mcp_server import serve

        return serve(settings.notes_dir)

    from study_assistant.mcp_client import MCPManager

    manager = MCPManager.from_config(settings.mcp_config_path)
    print(f"MCP 配置：{settings.mcp_config_path}")
    print(manager.describe() or "（没有服务器）")
    if manager.tools:
        print("\n工具清单：")
        for t in manager.tools:
            print(f"  {t.qualified_name:<28} {t.description.splitlines()[0][:60]}")
    manager.close()
    return 0


def cmd_doctor(args, settings: Settings) -> int:
    print("── 配置 ────────────────────────────────────────")
    print(settings.describe())

    print("\n── 检索索引 ────────────────────────────────────")
    try:
        print(rag.index_stats(settings.notes_dir))
    except Exception as e:
        print(f"❌ 建索引失败：{e}")

    print("\n── 工具 ────────────────────────────────────────")
    registry, manager = build_registry(settings, use_mcp=not args.no_mcp)
    print(registry.describe())
    if manager:
        print("\n── MCP 服务器 ──────────────────────────────────")
        print(manager.describe())
        manager.close()

    if args.online:
        print("\n── 连通性（会花一次极小的调用）─────────────────")
        try:
            client = LLMClient(settings)
            resp = client.invoke([{"role": "user", "content": "回复 ok"}])
            print(f"✅ {settings.model} 可用：{resp.choices[0].message.content!r}")
        except StudyAssistantError as e:
            print(f"❌ {e}")
            return 1
    return 0


def cmd_version(args, settings) -> int:
    print(f"study-assistant {__version__}")
    return 0


# ══════════════════════════════════════════════════════════════════
#  解析
# ══════════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="study-assistant",
        description="不依赖 Agent 框架、从零手写的命令行学习助手",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--env-file", type=Path, default=None, help="指定 .env 路径")
    p.add_argument("--no-mcp", action="store_true", help="不启动 MCP 服务器")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("chat", help="交互对话（默认）").set_defaults(func=cmd_chat)

    a = sub.add_parser("ask", help="单问一次")
    a.add_argument("question", nargs="+")
    a.add_argument("--no-stream", action="store_true", help="关闭流式输出")
    a.set_defaults(func=cmd_ask)

    s = sub.add_parser("search", help="只测检索，不调模型")
    s.add_argument("query", nargs="+")
    s.set_defaults(func=cmd_search)

    sub.add_parser("tools", help="列出工具").set_defaults(func=cmd_tools)

    m = sub.add_parser("mcp", help="MCP 相关")
    msub = m.add_subparsers(dest="mcp_cmd")
    msub.add_parser("list", help="列出 MCP 服务器与工具")
    msub.add_parser("serve", help="启动本项目的 notes MCP 服务器")
    m.set_defaults(func=cmd_mcp, mcp_cmd="list")

    d = sub.add_parser("doctor", help="环境自检")
    d.add_argument("--online", action="store_true", help="额外做一次真实调用")
    d.set_defaults(func=cmd_doctor)

    sub.add_parser("version", help="版本").set_defaults(func=cmd_version)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        args.func = cmd_chat
    if not hasattr(args, "no_mcp"):
        args.no_mcp = False  # 子命令没定义时补一个默认值
    if not hasattr(args, "no_stream"):
        args.no_stream = False

    try:
        settings = Settings.load(args.env_file)
    except Exception as e:
        print(f"配置读取失败：{e}", file=sys.stderr)
        raise SystemExit(2) from None

    setup_logging(settings.log_level, settings.log_dir)
    quiet_noisy_libraries()

    try:
        settings.ensure_ready(need_llm=args.func not in (cmd_search, cmd_mcp, cmd_version))
    except ConfigError as e:
        print(e, file=sys.stderr)
        raise SystemExit(2) from None

    try:
        raise SystemExit(args.func(args, settings))
    except LLMError as e:
        print(f"\n调用失败：{e}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("\n中断", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
