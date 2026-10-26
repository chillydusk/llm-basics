"""本项目自带的 MCP 服务器：把 notes/ 暴露成标准 MCP 工具。

它和 `mcp_client.py` 是一对 —— 一个说协议，一个听协议。
加起来不到 400 行，但能把「工具」从进程内的函数变成**可跨进程分发的服务**。

协议（stdio 传输）就四条：

    initialize                握手，交换版本和能力
    notifications/initialized 通知（无 id，不用回）
    tools/list                我有哪些工具，schema 是什么
    tools/call                执行某个工具，返回 content 数组

⚠️ 写服务器最容易踩的一条：**stdout 只能放协议消息**。
   任何 print / 日志跑到 stdout，客户端就会解析失败。
   所以下面所有日志都走 stderr（`logging` 默认就是）。

跑法：

    uv run study-assistant mcp serve            ← 手动起（自己看得到日志）
    # 或者由客户端按 mcp_servers.json 自动拉起
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from study_assistant.config import PROJECT_ROOT
from study_assistant.tools import notes_tools

logger = logging.getLogger("study_assistant.mcp_server")

SERVER_INFO = {"name": "study-notes", "version": "1.0.0"}
SUPPORTED_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}


def _notes_dir() -> Path:
    raw = os.getenv("STUDY_NOTES_DIR")
    return Path(raw).expanduser().resolve() if raw else PROJECT_ROOT / "notes"


class NotesServer:
    def __init__(self, notes_dir: Path | None = None) -> None:
        self.notes_dir = notes_dir or _notes_dir()
        self.tools = {t.name: t for t in notes_tools(self.notes_dir)}

    # ── 协议入口 ──────────────────────────────────────────
    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """处理一条消息。返回 None 表示这是通知，不用回。"""
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        if method is None:
            logger.warning("收到没有 method 的消息：%s", msg)
            return None

        # 通知：没有 id，不回
        if req_id is None:
            logger.debug("收到通知 %s", method)
            return None

        try:
            if method == "initialize":
                return _ok(req_id, self._initialize(params))
            if method == "ping":
                return _ok(req_id, {})
            if method == "tools/list":
                return _ok(req_id, {"tools": self._list_tools()})
            if method == "tools/call":
                return _ok(req_id, self._call_tool(params))
            if method == "resources/list":
                return _ok(req_id, {"resources": []})
            if method == "prompts/list":
                return _ok(req_id, {"prompts": []})
            return _err(req_id, -32601, f"不支持的方法：{method}")
        except Exception as e:  # 服务器不能因为一条请求崩掉
            logger.exception("处理 %s 失败", method)
            return _err(req_id, -32603, f"{type(e).__name__}: {e}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion", "")
        version = requested if requested in SUPPORTED_VERSIONS else "2024-11-05"
        logger.info("客户端 %s 握手，协议版本 %s", params.get("clientInfo", {}), version)
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }

    def _list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.parameters,
            }
            for t in self.tools.values()
        ]

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        tool = self.tools.get(name)
        if tool is None:
            return _text_result(
                f"没有这个工具：{name}。可用：{', '.join(self.tools)}", is_error=True
            )
        try:
            result = tool.handler(**arguments)
        except TypeError as e:
            return _text_result(f"参数不对：{e}", is_error=True)
        except Exception as e:
            logger.exception("工具 %s 执行失败", name)
            return _text_result(f"{type(e).__name__}: {e}", is_error=True)
        return _text_result(str(result))


# ══════════════════════════════════════════════════════════════════
#  JSON-RPC 信封
# ══════════════════════════════════════════════════════════════════


def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


# ══════════════════════════════════════════════════════════════════
#  主循环
# ══════════════════════════════════════════════════════════════════


def serve(notes_dir: Path | None = None, *, stdin=None, stdout=None) -> int:
    """阻塞式主循环：一行进，一行出。"""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    server = NotesServer(notes_dir)
    logger.info("MCP 服务器已启动，笔记目录 %s（%d 个工具）", server.notes_dir, len(server.tools))

    for raw in stdin:
        line = (
            raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else raw.strip()
        )
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning("收到非 JSON 行：%s（%s）", line[:120], e)
            continue
        if isinstance(msg, list):  # 批量请求
            replies = [r for m in msg if (r := server.handle(m)) is not None]
            payload = replies
        else:
            reply = server.handle(msg)
            if reply is None:
                continue
            payload = reply
        stdout.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        stdout.flush()

    logger.info("stdin 关闭，MCP 服务器退出")
    return 0


def main() -> None:
    logging.basicConfig(
        level=os.getenv("STUDY_LOG_LEVEL", "INFO"),
        stream=sys.stderr,  # ⚠️ 只能 stderr
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(serve())


if __name__ == "__main__":
    main()
