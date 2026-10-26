"""工具层：注册表语义 + 安全边界。

这里有一条硬约束：**registry.call 永远不抛异常**。
它是「工具失败不中断对话」的实现，破了这条，一句笔误就能把 REPL 干崩。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from study_assistant.errors import ToolError
from study_assistant.tools import Tool, ToolRegistry, _safe_note_path, builtin_tools, notes_tools


def make_tool(name: str = "echo", fn=None) -> Tool:
    return Tool(
        name=name,
        description="回声",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        handler=fn or (lambda x: f"echo:{x}"),
    )


def test_register_and_schema():
    reg = ToolRegistry()
    reg.register(make_tool())
    schemas = reg.schemas()
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "echo"


def test_duplicate_name_rejected():
    reg = ToolRegistry()
    reg.register(make_tool())
    with pytest.raises(ToolError):
        reg.register(make_tool())


def test_disambiguate_on_conflict():
    reg = ToolRegistry()
    reg.extend(builtin_tools())
    reg.extend(notes_tools(Path("notes")), disambiguate=True)
    reg.extend(notes_tools(Path("notes")), disambiguate=True)
    names = reg.names()
    assert len(names) == len(set(names))
    assert any(n.startswith("notes__") for n in names)


def test_call_ok():
    reg = ToolRegistry()
    reg.register(make_tool())
    assert reg.call("echo", {"x": "hi"}) == "echo:hi"


def test_call_accepts_json_string():
    reg = ToolRegistry()
    reg.register(make_tool())
    assert reg.call("echo", '{"x": "hi"}') == "echo:hi"


def test_call_unknown_tool_returns_text():
    reg = ToolRegistry()
    out = reg.call("nope", {})
    assert "没有这个工具" in out


def test_call_bad_json_returns_text():
    reg = ToolRegistry()
    reg.register(make_tool())
    assert "JSON" in reg.call("echo", "{oops")


def test_call_wrong_params_returns_text():
    reg = ToolRegistry()
    reg.register(make_tool())
    out = reg.call("echo", {"y": 1})
    assert "参数不对" in out


def test_call_exception_is_swallowed():
    def boom(x):
        raise RuntimeError("炸了")

    reg = ToolRegistry()
    reg.register(make_tool(fn=boom))
    out = reg.call("echo", {"x": "1"})
    assert "RuntimeError" in out and "炸了" in out


def test_calculate():
    reg = ToolRegistry()
    reg.extend(builtin_tools())
    assert reg.call("calculate", {"a": 1, "b": 2}) == "3"
    assert reg.call("calculate", {"a": 1.5, "b": 2}) == "3.5"


# ── 安全 ──────────────────────────────────────────────


def test_path_traversal_blocked(notes_dir: Path):
    with pytest.raises(ToolError):
        _safe_note_path(notes_dir, "../../.env")


def test_path_inside_ok(notes_dir: Path):
    p = _safe_note_path(notes_dir, "网络.md")
    assert p.exists()


def test_read_notes_missing(notes_dir: Path):
    reg = ToolRegistry()
    reg.extend(notes_tools(notes_dir))
    out = reg.call("read_notes", {"filename": "不存在.md"})
    assert "找不到" in out
    assert "网络.md" in out, "应该把现有文件列出来，方便模型自我修正"


def test_read_notes_strips_prefix(notes_dir: Path):
    reg = ToolRegistry()
    reg.extend(notes_tools(notes_dir))
    assert "三次握手" in reg.call("read_notes", {"filename": "notes/网络.md"})


def test_list_notes(notes_dir: Path):
    reg = ToolRegistry()
    reg.extend(notes_tools(notes_dir))
    out = reg.call("list_notes", {})
    assert "网络.md" in out
