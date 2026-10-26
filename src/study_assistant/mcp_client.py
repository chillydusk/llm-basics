"""MCP 客户端（stdio 传输，手写）。

MCP（Model Context Protocol）做的事很短一句话：
**把「一个工具」从一个 Python 函数，变成一个说 JSON-RPC 的独立进程。**

这样工具就能跨语言、跨仓库、跨团队分发 —— 别人的 MCP 服务器，
你 `command + args` 一配就能用，不用 import 他的代码。

    模型看到的：{name, description, inputSchema}   ← tools/list 拿到的
    我们发出去的：{"method": "tools/call", "params": {"name":..., "arguments":...}}
    拿回来的：  {"result": {"content": [{"type": "text", "text": "..."}]}}

传输层：**标准输入输出**，一行一条 JSON。不装库、不走 HTTP。

    stdin  ← 我们写请求
    stdout → 服务器写响应
    stderr → 服务器的日志（我们会原样转到 DEBUG 日志里）

⚠️ 为什么不用官方 `mcp` SDK：它的客户端是 async 的，而本项目的 Agent 循环是同步的，
   中间要架一层事件循环线程。这个协议本体只有四个方法，手写反而更清楚、更好调试
   —— 而且你能在面试里说「我对着协议实现了 MCP 客户端」，而不是「我 import 了 mcp」。
   （要连公司现成的 MCP 生态，把 MCPClient 换成官方 SDK 即可，接口是一样的。）
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from study_assistant.config import MCPServerConfig, load_mcp_config
from study_assistant.errors import MCPError
from study_assistant.tools import Tool

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "study-assistant", "version": "1.0.0"}


@dataclass
class MCPToolInfo:
    """tools/list 返回的一条。"""

    server: str
    name: str
    description: str
    input_schema: dict[str, Any]

    @property
    def qualified_name(self) -> str:
        return f"{self.server}__{self.name}"


class MCPClient:
    """一个 MCP 服务器进程的连接。

    生命周期：start() → list_tools() / call_tool() … → close()
    所有方法都是同步的，内部用一个读线程把 stdout 的响应投递到队列。
    """

    def __init__(self, cfg: MCPServerConfig, timeout: float = 30.0) -> None:
        self.cfg = cfg
        self.timeout = timeout
        self._proc: subprocess.Popen[bytes] | None = None
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_thread: threading.Thread | None = None
        self._reader_thread: threading.Thread | None = None
        self._next_id = 0
        self._closed = False
        self.server_info: dict[str, Any] = {}

    # ── 生命周期 ──────────────────────────────────────────
    def start(self) -> None:
        cmd = self.cfg.resolve()
        argv = [cmd.command, *cmd.args]
        logger.info("启动 MCP 服务器 %s：%s", self.cfg.name, " ".join(argv))

        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_merged_env(cmd.env),
            )
        except FileNotFoundError as e:
            raise MCPError(
                f"MCP 服务器 {self.cfg.name} 启动失败：找不到命令 {cmd.command}。"
                f"检查 mcp_servers.json 里的 command。"
            ) from e
        except OSError as e:
            raise MCPError(f"MCP 服务器 {self.cfg.name} 启动失败：{e}") from e

        self._reader_thread = threading.Thread(
            target=self._read_stdout, name=f"mcp-{self.cfg.name}-out", daemon=True
        )
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, name=f"mcp-{self.cfg.name}-err", daemon=True
        )
        self._stderr_thread.start()

        self._handshake()

    def _handshake(self) -> None:
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        self.server_info = result.get("serverInfo", {})
        self._notify("notifications/initialized", {})
        logger.info(
            "MCP %s 已连接：%s %s",
            self.cfg.name,
            self.server_info.get("name", "?"),
            self.server_info.get("version", ""),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass
        logger.debug("MCP 服务器 %s 已关闭", self.cfg.name)

    # ── 协议方法 ──────────────────────────────────────────
    def list_tools(self) -> list[MCPToolInfo]:
        result = self._request("tools/list", {})
        out = []
        for t in result.get("tools", []):
            out.append(
                MCPToolInfo(
                    server=self.cfg.name,
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
                )
            )
        return out

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        return _content_to_text(result)

    # ── 收发 ──────────────────────────────────────────────
    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise MCPError(f"MCP 服务器 {self.cfg.name} 还没启动")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            proc.stdin.write(line.encode("utf-8"))
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPError(f"MCP 服务器 {self.cfg.name} 的管道断了（进程可能已退出）：{e}") from e

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        req_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(
                    f"MCP 服务器 {self.cfg.name} 在 {self.timeout:.0f}s 内没有响应 {method}"
                )
            try:
                msg = self._responses.get(timeout=remaining)
            except queue.Empty:
                continue

            if msg.get("id") != req_id:
                # 服务器主动推的通知（日志之类），忽略
                logger.debug("[mcp:%s] 忽略消息 %s", self.cfg.name, msg.get("method"))
                continue

            if "error" in msg:
                err = msg["error"]
                raise MCPError(
                    f"MCP 服务器 {self.cfg.name} 返回错误 [{err.get('code')}] {err.get('message')}"
                )
            return msg.get("result") or {}

    # ── 读线程 ────────────────────────────────────────────
    def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                self._responses.put(json.loads(line))
            except json.JSONDecodeError:
                # 服务器往 stdout 打了非协议内容 —— 常见错误，记下来但不炸
                logger.warning("[mcp:%s] stdout 出现非 JSON：%s", self.cfg.name, line[:200])
        # 进程退出 → 放一个哨兵，避免 _request 干等
        self._responses.put({"id": -1, "error": {"code": -32000, "message": "进程已退出"}})

    def _read_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                logger.debug("[mcp:%s] %s", self.cfg.name, line)


# ══════════════════════════════════════════════════════════════════
#  管理器：多个服务器 + 把工具接进注册表
# ══════════════════════════════════════════════════════════════════


@dataclass
class MCPManager:
    """管理所有 MCP 服务器，并把它们的工具包装成本项目的 Tool。"""

    clients: dict[str, MCPClient] = field(default_factory=dict)
    tools: list[MCPToolInfo] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls, config_path: Path, *, timeout: float = 30.0, strict: bool = False
    ) -> MCPManager:
        """按配置启动所有服务器。

        strict=False 时，**单个服务器起不来不阻断启动** —— 降级运行，
        把失败记在 failures 里，由 doctor 命令或日志告诉用户。
        企业系统里这比"一个插件挂了整个服务起不来"要合理得多。
        """
        manager = cls()
        try:
            servers = load_mcp_config(config_path)
        except Exception as e:
            manager.failures["(config)"] = str(e)
            if strict:
                raise
            logger.error("MCP 配置读取失败，已跳过：%s", e)
            return manager

        for cfg in servers:
            if not cfg.enabled:
                logger.info("MCP 服务器 %s 已禁用，跳过", cfg.name)
                continue
            client = MCPClient(cfg, timeout=timeout)
            try:
                client.start()
                infos = client.list_tools()
            except MCPError as e:
                client.close()
                manager.failures[cfg.name] = str(e)
                logger.error("MCP 服务器 %s 不可用：%s", cfg.name, e)
                if strict:
                    raise
                continue

            manager.clients[cfg.name] = client
            manager.tools.extend(infos)
            logger.info("MCP 服务器 %s 提供 %d 个工具", cfg.name, len(infos))
        return manager

    def to_tools(self) -> list[Tool]:
        """把 MCP 工具包装成本项目的 Tool（同一个注册表、同一个 call 接口）。"""
        out: list[Tool] = []
        for info in self.tools:
            out.append(
                Tool(
                    # 用原始名字，冲突交给 ToolRegistry(disambiguate=True) 处理 ——
                    # 否则一个笔记服务器就会把 search_notes 变成 notes__search_notes，
                    # 和本地兜底的同名工具同时存在，模型会看见两份。
                    name=info.name,
                    description=info.description or info.name,
                    parameters=info.input_schema,
                    handler=self._make_handler(info),
                    source=f"mcp:{info.server}",
                    read_only=True,
                )
            )
        return out

    def _make_handler(self, info: MCPToolInfo):
        def handler(**kwargs: Any) -> str:
            client = self.clients.get(info.server)
            if client is None:
                return f"MCP 服务器 {info.server} 已断开"
            try:
                return client.call_tool(info.name, kwargs)
            except MCPError as e:
                # 工具边界：失败当结果返回，让模型自己决定怎么办
                return f"MCP 工具 {info.name} 调用失败：{e}"

        return handler

    def close(self) -> None:
        for client in self.clients.values():
            client.close()
        self.clients.clear()

    def describe(self) -> str:
        if not self.clients and not self.failures:
            return "没有配置任何 MCP 服务器"
        lines = []
        for name, client in self.clients.items():
            info = client.server_info
            n = sum(1 for t in self.tools if t.server == name)
            lines.append(
                f"  ✅ {name}  {info.get('name', '?')} {info.get('version', '')}  —— {n} 个工具"
            )
        for name, err in self.failures.items():
            lines.append(f"  ❌ {name}  {err}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  小工具
# ══════════════════════════════════════════════════════════════════


def _merged_env(extra: dict[str, str]) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.update({k: str(v) for k, v in extra.items()})
    # 保证子进程输出是 UTF-8（Windows 默认 GBK 会把中文搞乱）
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def _content_to_text(result: dict[str, Any]) -> str:
    """把 tools/call 的 content 数组拼成纯文本。"""
    items = result.get("content") or []
    parts: list[str] = []
    for item in items:
        kind = item.get("type")
        if kind == "text":
            parts.append(item.get("text", ""))
        elif kind == "resource":
            res = item.get("resource", {})
            parts.append(res.get("text") or f"[resource: {res.get('uri')}]")
        else:
            parts.append(json.dumps(item, ensure_ascii=False))
    text = "\n".join(p for p in parts if p)
    if result.get("isError"):
        return f"[MCP 工具返回错误] {text}"
    return text or "（MCP 工具没有返回内容）"
