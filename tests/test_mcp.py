"""MCP：协议两端都要测。

服务端：喂 JSON 进去，看吐出来的 JSON 对不对（纯内存，快）
客户端：真的起一个子进程，走完整握手 + tools/call（慢一点，但这是集成测试）
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from study_assistant.config import MCPServerConfig
from study_assistant.mcp_client import MCPClient
from study_assistant.mcp_server import serve


def run_server(messages: list[dict], notes_dir: Path) -> list[dict]:
    raw = b"".join(json.dumps(m, ensure_ascii=False).encode("utf-8") + b"\n" for m in messages)
    out = io.BytesIO()
    serve(notes_dir, stdin=io.BytesIO(raw), stdout=out)
    return [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines() if line]


def test_initialize(notes_dir: Path):
    res = run_server([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}], notes_dir)
    assert res[0]["id"] == 1
    assert res[0]["result"]["protocolVersion"] == "2024-11-05"
    assert res[0]["result"]["capabilities"]["tools"]


def test_initialize_echoes_supported_version(notes_dir: Path):
    res = run_server(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        ],
        notes_dir,
    )
    assert res[0]["result"]["protocolVersion"] == "2025-06-18"


def test_notification_gets_no_reply(notes_dir: Path):
    res = run_server(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ],
        notes_dir,
    )
    # 只有 initialize 那条有回，通知没有 id → 不回
    assert len(res) == 1


def test_tools_list(notes_dir: Path):
    res = run_server([{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}], notes_dir)
    tools = res[0]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"read_notes", "list_notes", "search_notes"} <= names
    for t in tools:
        assert t["inputSchema"]["type"] == "object"


def test_tools_call(notes_dir: Path):
    res = run_server(
        [
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "read_notes", "arguments": {"filename": "网络.md"}},
            }
        ],
        notes_dir,
    )
    result = res[0]["result"]
    assert result["isError"] is False
    assert "三次握手" in result["content"][0]["text"]


def test_tools_call_unknown(notes_dir: Path):
    res = run_server(
        [
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            }
        ],
        notes_dir,
    )
    assert res[0]["result"]["isError"] is True


def test_unknown_method(notes_dir: Path):
    res = run_server([{"jsonrpc": "2.0", "id": 5, "method": "foo/bar"}], notes_dir)
    assert res[0]["error"]["code"] == -32601


def test_bad_json_line_ignored(notes_dir: Path):
    raw = b"not json\n" + json.dumps({"jsonrpc": "2.0", "id": 6, "method": "ping"}).encode() + b"\n"
    out = io.BytesIO()
    serve(notes_dir, stdin=io.BytesIO(raw), stdout=out)
    lines = [ln for ln in out.getvalue().decode().splitlines() if ln]
    assert len(lines) == 1 and json.loads(lines[0])["id"] == 6


# ── 集成：真的起进程 ────────────────────────────────────


@pytest.fixture()
def client(notes_dir: Path):
    cfg = MCPServerConfig(
        name="notes",
        command=sys.executable,
        args=["-m", "study_assistant.mcp_server"],
        env={
            "STUDY_NOTES_DIR": str(notes_dir),
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        },
    )
    c = MCPClient(cfg, timeout=30)
    c.start()
    yield c
    c.close()


def test_client_roundtrip(client: MCPClient):
    tools = client.list_tools()
    assert {t.name for t in tools} >= {"read_notes", "list_notes", "search_notes"}
    assert all(t.server == "notes" for t in tools)

    text = client.call_tool("read_notes", {"filename": "网络.md"})
    assert "三次握手" in text


def test_client_search(client: MCPClient):
    text = client.call_tool("search_notes", {"query": "三次握手", "top_k": 2})
    assert "网络.md" in text


def test_client_unknown_tool(client: MCPClient):
    text = client.call_tool("nope", {})
    assert "没有这个工具" in text
