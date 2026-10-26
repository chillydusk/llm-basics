"""异常层级。

企业级项目里，异常是**接口的一部分**：调用方要知道该 catch 哪一种。

    StudyAssistantError        所有自家异常的根
    ├── ConfigError            配置错（启动时就该炸，退出码 2）
    ├── LLMError               模型调用失败（网络/鉴权/限流，退出码 1）
    ├── ToolError              工具执行失败（通常不抛，见 ToolRegistry.call）
    └── MCPError               MCP 服务器启动/通信失败（降级，不中断会话）
"""

from __future__ import annotations


class StudyAssistantError(Exception):
    """本项目的所有可预期错误都继承它。"""


class ConfigError(StudyAssistantError):
    """配置缺失或非法。启动阶段抛出，不要拖到运行中。"""


class LLMError(StudyAssistantError):
    """调用模型失败。"""


class ToolError(StudyAssistantError):
    """工具本身的错误（参数不对、文件不存在等）。"""


class MCPError(StudyAssistantError):
    """MCP 协议层错误：进程起不来、握手超时、返回格式不对。"""
