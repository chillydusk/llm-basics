"""配置层：所有「可变的东西」都从这一个入口来。

为什么要有这一层（对比 Part F 之前）：

    以前   路径散落在各处：load_dotenv(".env")、Path("study_prompt.txt")、Path("notes")
           三个全是**相对当前工作目录**的 —— 换个目录跑就炸（坑 #39）
    现在   全部锚定到 PROJECT_ROOT，跟你在哪儿敲命令无关

    以前   配置错了要等到第一次调 API 才发现
    现在   Settings.load() 一进来就校验，错在哪、怎么改，一次说清（fail fast）

设计原则：**配置是只读的**。启动时读一次，之后谁都不许改。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

# src/study_assistant/config.py → parents[2] 就是项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_MCP_CONFIG = {
    "mcpServers": {
        "notes": {
            "command": "{python}",
            "args": ["-m", "study_assistant.mcp_server"],
            "enabled": True,
            "description": "把 notes/ 目录暴露成 MCP 工具（本项目自带）",
        }
    }
}


class Settings(BaseModel):
    """一次启动里所有的可变项。字段名就是 .env 里的名字（全大写）。"""

    # ── 模型 ──────────────────────────────────────────────
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    temperature: float = 1.0
    request_timeout: float = 60.0

    # ── 会话 ──────────────────────────────────────────────
    max_turns: int = Field(5, ge=1, le=20)
    keep_messages: int = Field(20, ge=4, le=200)

    # ── 路径（全部锚定到项目根，不依赖当前工作目录）────────
    project_root: Path = PROJECT_ROOT
    notes_dir: Path = PROJECT_ROOT / "notes"
    prompt_path: Path = PROJECT_ROOT / "study_prompt.txt"
    log_dir: Path = PROJECT_ROOT / "logs"
    mcp_config_path: Path = PROJECT_ROOT / "mcp_servers.json"

    # ── 行为开关 ──────────────────────────────────────────
    trace_tools: bool = True
    enable_mcp: bool = True
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _check_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"log_level 只能是 {sorted(allowed)} 之一")
        return v

    # ── 构造 ──────────────────────────────────────────────
    @classmethod
    def load(cls, env_file: Path | None = None) -> Settings:
        """从 .env + 环境变量读配置。环境变量优先级更高（方便 CI）。"""
        env_path = env_file or (PROJECT_ROOT / ".env")
        if env_path.exists():
            load_dotenv(env_path, override=False)

        raw: dict[str, Any] = {
            "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
            "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "model": os.getenv("STUDY_MODEL", "deepseek-flash"),
            "temperature": float(os.getenv("STUDY_TEMPERATURE", "1.0")),
            "request_timeout": float(os.getenv("STUDY_TIMEOUT", "60")),
            "max_turns": int(os.getenv("STUDY_MAX_TURNS", "5")),
            "keep_messages": int(os.getenv("STUDY_KEEP_MESSAGES", "20")),
            "enable_mcp": os.getenv("STUDY_ENABLE_MCP", "1") not in ("0", "false", "False"),
            "trace_tools": os.getenv("STUDY_TRACE_TOOLS", "1") not in ("0", "false", "False"),
            "log_level": os.getenv("STUDY_LOG_LEVEL", "INFO"),
        }
        if notes := os.getenv("STUDY_NOTES_DIR"):
            raw["notes_dir"] = Path(notes).expanduser().resolve()

        return cls(**raw)

    def ensure_ready(self, *, need_llm: bool = True) -> None:
        """启动前的最后一道闸：缺什么就在这里说清楚，别等到调 API 才炸。"""
        problems: list[str] = []

        if need_llm and not self.api_key:
            problems.append(
                "没找到 DEEPSEEK_API_KEY。\n"
                "    修法：cp .env.example .env，然后把 sk-xxxx 换成你自己的 key"
            )

        if need_llm and not self.prompt_path.exists():
            problems.append(
                f"没找到人设文件 {self.prompt_path}\n"
                "    修法：从仓库恢复 study_prompt.txt（它应该和 pyproject.toml 同级）"
            )

        if not self.notes_dir.is_dir():
            problems.append(
                f"笔记目录不存在：{self.notes_dir}\n"
                "    修法：mkdir notes，或设 STUDY_NOTES_DIR 指向你的笔记目录"
            )

        if problems:
            raise ConfigError("配置有问题：\n  - " + "\n  - ".join(problems))

    def describe(self) -> str:
        """给 doctor 子命令用：脱敏后的一览表。"""
        key = self.api_key
        masked = f"{key[:6]}…{key[-4:]}" if len(key) > 12 else ("(未设置)" if not key else "***")
        return "\n".join(
            [
                f"项目根目录    {self.project_root}",
                f"模型          {self.model} @ {self.base_url}",
                f"API Key       {masked}",
                f"人设文件      {self.prompt_path}  {'✅' if self.prompt_path.exists() else '❌'}",
                f"笔记目录      {self.notes_dir}  {'✅' if self.notes_dir.is_dir() else '❌'}",
                f"日志目录      {self.log_dir}",
                f"MCP 配置      {self.mcp_config_path}  {'✅' if self.mcp_config_path.exists() else '（未配置）'}",
                f"MCP 开关      {'开' if self.enable_mcp else '关'}",
                f"最大轮数      {self.max_turns}",
                f"截断阈值      {self.keep_messages} 条",
            ]
        )


class MCPServerConfig(BaseModel):
    """一个 MCP 服务器怎么启动。字段名沿用 Claude Desktop 的 mcpServers 约定。"""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    description: str = ""

    def resolve(self) -> MCPServerConfig:
        """展开 {python} 占位符和 ${ENV_VAR}。"""
        command = self.command.replace("{python}", sys.executable)
        args = [a.replace("{python}", sys.executable) for a in self.args]
        env = {k: v.replace("{python}", sys.executable) for k, v in self.env.items()}
        return self.model_copy(update={"command": command, "args": args, "env": env})


class ConfigError(Exception):
    """配置错误：启动阶段就该炸，别拖到运行中。"""


def load_mcp_config(path: Path) -> list[MCPServerConfig]:
    """读 mcp_servers.json。文件不存在就用内置的 notes 服务器。"""
    if not path.exists():
        data = DEFAULT_MCP_CONFIG
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"{path} 不是合法的 JSON（第 {e.lineno} 行）：{e.msg}") from None

    servers: list[MCPServerConfig] = []
    for name, spec in (data.get("mcpServers") or {}).items():
        servers.append(
            MCPServerConfig(
                name=name,
                command=spec.get("command", ""),
                args=spec.get("args", []),
                env=spec.get("env", {}),
                enabled=spec.get("enabled", True),
                description=spec.get("description", ""),
            )
        )
    return servers
