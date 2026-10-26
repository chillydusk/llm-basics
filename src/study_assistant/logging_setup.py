"""日志。两路输出，各管各的：

    控制台（stderr）  WARNING 以上 —— 用户需要立刻知道的事
    文件（logs/）     DEBUG 以上 —— 出问题事后查

为什么不用 print：打印是给用户的，日志是给排查的。混在一起，两边都做不好。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> Path | None:
    """配置根 logger。返回日志文件路径（没写文件则返回 None）。"""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):  # 幂等：重复调用不会叠加 handler
        root.removeHandler(h)

    # 控制台只放"用户该知道的事"；INFO 级别的流程日志留给文件。
    # 想看全过程就把 STUDY_LOG_LEVEL 设成 DEBUG。
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if level.upper() == "DEBUG" else logging.WARNING)
    console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root.addHandler(console)

    if log_dir is None:
        return None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "study-assistant.log"
        file_handler = RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(file_handler)
        return path
    except OSError as e:  # 只读目录之类，不能让程序起不来
        root.warning("日志文件不可写，只输出到控制台：%s", e)
        return None


def quiet_noisy_libraries() -> None:
    """把第三方库的 INFO 日志压下去（httpx 每次请求都刷一行）。"""
    for name in ("httpx", "httpcore", "openai", "urllib3", "jieba"):
        logging.getLogger(name).setLevel(logging.WARNING)
