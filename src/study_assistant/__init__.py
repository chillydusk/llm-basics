"""study-assistant —— 不依赖 Agent 框架、从零手写的命令行学习助手。

模块地图：

    config.py       配置（路径锚定项目根、fail fast）
    logging_setup   日志（控制台 + 文件轮转）
    errors.py       异常层级
    llm.py          模型客户端（流式 / 重试 / 异常翻译）
    usage.py        token 计费（缓存命中/未命中分开算）
    history.py      会话历史与截断
    agent.py        手写 ReAct 循环（本项目的核心）
    tools.py        工具注册表 + 内置工具 + 笔记工具
    rag.py          关键词检索（切分 / jieba / BM25）
    mcp_client.py   MCP 客户端（stdio + JSON-RPC，手写）
    mcp_server.py   本项目自带的 notes MCP 服务器
    cli.py          命令行入口
    langchain_agent.py  LangChain 对照实现（同一套工具）
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
