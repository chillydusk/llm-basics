"""pytest 公共夹具。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SAMPLE_NOTES = {
    "网络.md": """# 网络

## 一、TCP 三次握手

建立连接要三次，客户端初始序列号 x 随机，服务端 y 随机。
确认号等于对方的序列号加一。

## 二、UDP

UDP 是无连接的传输协议，不建立连接、不确认、不重传。
""",
    "短的.md": "# 短的\n\n三次握手的序列号规则：初始序列号随机，确认号加一。\n",
}


@pytest.fixture()
def notes_dir(tmp_path: Path) -> Path:
    d = tmp_path / "notes"
    d.mkdir()
    for name, body in SAMPLE_NOTES.items():
        (d / name).write_text(body, encoding="utf-8")
    return d


@pytest.fixture()
def repo_notes() -> Path:
    """仓库里真实的 notes/（只读使用）。"""
    src = ROOT / "notes"
    if not src.is_dir():
        pytest.skip("仓库里没有 notes/ 目录")
    return src


@pytest.fixture(scope="session")
def python_exe() -> str:
    return sys.executable


@pytest.fixture()
def copy_notes(tmp_path: Path, repo_notes: Path) -> Path:
    d = tmp_path / "notes"
    shutil.copytree(repo_notes, d)
    return d
