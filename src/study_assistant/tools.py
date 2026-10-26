"""工具层：注册表 + 内置工具 + 笔记工具。

一个工具 = **说明书**（模型看的 JSON Schema）+ **函数**（我们执行的）。
这两件事必须配对注册，否则模型会调一个不存在的函数 —— 这是 Part D 的原始教训。

    ToolRegistry
      ├── register(Tool)          手工登记
      ├── extend([Tool, ...])     批量登记（MCP 工具就是这么进来的）
      ├── schemas()               喂给 API 的 tools 参数
      └── call(name, args)        **永不抛异常** —— 出错就把错误当结果返回
                                   （简历上那句「工具失败不中断对话」的实现处）
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from study_assistant import rag
from study_assistant.errors import ToolError

logger = logging.getLogger(__name__)

BEIJING = timezone(timedelta(hours=8))


# ══════════════════════════════════════════════════════════════════
#  数据结构
# ══════════════════════════════════════════════════════════════════


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]
    source: str = "builtin"  # builtin | notes | mcp:xxx
    read_only: bool = True

    def schema(self) -> dict[str, Any]:
        """转成 OpenAI tools 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
                or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


# ══════════════════════════════════════════════════════════════════
#  注册表
# ══════════════════════════════════════════════════════════════════


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, *, override: bool = False) -> None:
        if tool.name in self._tools and not override:
            raise ToolError(f"工具名冲突：{tool.name}（来自 {tool.source}）")
        self._tools[tool.name] = tool
        logger.debug("注册工具 %s（来源 %s）", tool.name, tool.source)

    def extend(
        self, tools: list[Tool], *, override: bool = False, disambiguate: bool = False
    ) -> None:
        """批量登记。

        disambiguate=True 时，重名的工具会自动加上来源前缀
        （如 `notes__search_notes`）—— MCP 服务器来自不同仓库，重名是常态。
        """
        for t in tools:
            if disambiguate and t.name in self._tools:
                t = replace(t, name=f"{t.source.split(':')[-1]}__{t.name}")
            self.register(t, override=override)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def call(self, name: str, arguments: dict[str, Any] | str) -> str:
        """执行工具。**任何情况下都返回字符串，不抛异常。**

        为什么：模型会传错参数、会调不存在的工具、会传半截 JSON。
        把这些错误当成**工具的输出**喂回去，模型下一轮就能自己改。
        直接 raise 的话，整个 REPL 会被一个笔误搞崩（坑 #45）。
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"没有这个工具：{name}。可用的工具有：{', '.join(self.names())}"

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as e:
                return f"参数不是合法 JSON：{e}。请重新生成参数。"

        try:
            result = tool.handler(**arguments)
        except TypeError as e:
            return f"参数不对：{e}"
        except Exception as e:  # 工具边界：必须兜住一切异常
            logger.exception("工具 %s 执行失败", name)
            return f"工具 {name} 执行出错：{type(e).__name__}: {e}"

        return result if isinstance(result, str) else str(result)

    def describe(self) -> str:
        lines = [f"共 {len(self._tools)} 个工具："]
        w = max((len(n) for n in self._tools), default=8)
        for t in self._tools.values():
            lines.append(f"  {t.name:<{w}}  [{t.source}]  {t.description.splitlines()[0]}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  内置工具（纯计算，不碰文件，所以不需要 MCP）
# ══════════════════════════════════════════════════════════════════


def _get_time() -> str:
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")


def _calculate(a: float, b: float) -> str:
    return str(a + b)


def builtin_tools() -> list[Tool]:
    return [
        Tool(
            name="get_time",
            description="获取当前日期和时间（北京时间 UTC+8）",
            parameters=_obj({}),
            handler=_get_time,
        ),
        Tool(
            name="calculate",
            description="做两个数的加法。参数必须是数字。",
            parameters=_obj(
                {
                    "a": {"type": "number", "description": "第一个加数"},
                    "b": {"type": "number", "description": "第二个加数"},
                },
                ["a", "b"],
            ),
            handler=_calculate,
        ),
    ]


# ══════════════════════════════════════════════════════════════════
#  笔记工具（碰文件 → 默认跑在 MCP server 里；本地模式直接注册同一批函数）
# ══════════════════════════════════════════════════════════════════


def _safe_note_path(notes_dir: Path, filename: str) -> Path:
    """把用户给的文件名解析成 notes/ 下的真实路径，越界就拒绝。

    ⚠️ 这是 README「已知限制 #2」的修复：
       以前直接 Path("notes") / filename，传 "../../.env" 就能读到密钥。
       现在 resolve() 之后检查是不是还在 notes_dir 里。
    """
    name = filename.replace("\\", "/").removeprefix("./").removeprefix("notes/")
    root = notes_dir.resolve()
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root):
        raise ToolError(f"拒绝访问 notes/ 之外的路径：{filename}")
    return candidate


def _read_notes(notes_dir: Path, filename: str) -> str:
    path = _safe_note_path(notes_dir, filename)
    if not path.exists():
        available = [p.name for p in sorted(notes_dir.glob("*.md"))]
        return f"找不到文件：{filename}。现有笔记：{', '.join(available) or '（空）'}"
    if path.is_dir():
        return f"{filename} 是个目录，不是文件"
    return path.read_text(encoding="utf-8")


def _list_notes(notes_dir: Path) -> str:
    files = sorted(notes_dir.glob("*.md"))
    if not files:
        return "notes/ 目录下还没有笔记"
    lines = [f"notes/ 下有 {len(files)} 份笔记："]
    for p in files:
        chars = len(p.read_text(encoding="utf-8"))
        lines.append(f"  {p.name}  （{chars} 字）")
    return "\n".join(lines)


def _search_notes(notes_dir: Path, query: str, top_k: int = 3) -> str:
    hits = rag.search(query, top_k=max(1, min(int(top_k), 6)), notes_dir=notes_dir)
    return rag.format_hits(query, hits)


def notes_tools(notes_dir: Path) -> list[Tool]:
    """笔记相关的三个工具。MCP server 和本地模式共用这一份定义。"""
    return [
        Tool(
            name="read_notes",
            description=(
                "读取我的学习笔记文件。filename 只传文件名（如 demo.md），"
                "不要带 notes/ 前缀 —— 工具内部会自动到 notes/ 目录下找。"
            ),
            parameters=_obj(
                {"filename": {"type": "string", "description": "笔记文件名，如 demo.md"}},
                ["filename"],
            ),
            handler=lambda filename: _read_notes(notes_dir, filename),
            source="notes",
        ),
        Tool(
            name="list_notes",
            description="列出 notes/ 目录下所有的笔记文件及其字数。",
            parameters=_obj({}),
            handler=lambda: _list_notes(notes_dir),
            source="notes",
        ),
        Tool(
            name="search_notes",
            description=(
                "在我的全部学习笔记里做关键词检索，返回最相关的几段，每段带"
                "《文件名》· 小节名 的来源标签。"
                "不知道答案在哪份笔记里时用它；已经知道文件名、想看全文时用 read_notes。"
                "回答用户时请注明引用的来源。"
            ),
            parameters=_obj(
                {
                    "query": {
                        "type": "string",
                        "description": "检索关键词，中文直接用，如「三次握手 序列号」",
                    },
                    "top_k": {"type": "integer", "description": "返回几段，默认 3，最多 6"},
                },
                ["query"],
            ),
            handler=lambda query, top_k=3: _search_notes(notes_dir, query, top_k),
            source="notes",
        ),
    ]


# MCP server 用得到：名字 → 函数，方便按名字直接调
def notes_dispatch(notes_dir: Path) -> dict[str, Callable[..., str]]:
    return {t.name: t.handler for t in notes_tools(notes_dir)}


__all__ = [
    "Tool",
    "ToolRegistry",
    "builtin_tools",
    "notes_dispatch",
    "notes_tools",
]
