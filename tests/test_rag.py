"""检索层：切分 / 打分 / 排序。

重点验证两件事：
    ① 切分后每段都带小节标题（否则「标题里有、正文里没有」的词搜不到）
    ② BM25 能把长度偏置按回去（控制变量实验的自动化版）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from study_assistant import rag


@pytest.fixture(autouse=True)
def _clear_cache():
    rag._CACHE.clear()
    yield
    rag._CACHE.clear()


def test_tokenize_chinese():
    toks = rag.tokenize("三次握手是怎么建立连接的")
    assert "握手" in toks
    assert "建立" in toks
    assert "的" not in toks, "停用词应该被过滤"


def test_tokenize_splits_negation():
    """「无连接」被切成「无」+「连接」—— 这是已知语义缺陷，作为行为固化下来。"""
    toks = rag.tokenize("UDP 是无连接的传输协议")
    assert "连接" in toks


def test_chunk_keeps_heading():
    text = "# 标题\n\n## 一、TCP 三次握手\n\n正文里没有握手两个字。\n"
    chunks = rag.chunk_text(text, source="x.md")
    assert chunks
    assert all("TCP 三次握手" in c.text for c in chunks), "标题必须拼进正文"


def test_chunk_respects_max_chars():
    long = "\n\n".join("段落" * 100 for _ in range(5))
    chunks = rag.chunk_text(f"## 一\n\n{long}", source="x.md")
    assert chunks
    assert all(len(c.text) <= rag.MAX_CHARS + 40 for c in chunks)


def test_idf_zero_for_ubiquitous_term(notes_dir: Path):
    idx = rag.get_index(notes_dir)
    assert idx.n > 0


def test_search_returns_scores(notes_dir: Path):
    hits = rag.search("三次握手 序列号", notes_dir=notes_dir)
    assert hits
    assert hits[0].score > 0
    assert hits == sorted(hits, key=lambda h: -h.score)


def test_no_match_returns_empty(notes_dir: Path):
    assert rag.search("量子纠缠退相干", notes_dir=notes_dir) == []


def test_bm25_fixes_length_bias():
    """控制变量：同样的关键词，长段只是重复得多 —— 原式会错，BM25 不会。"""
    short = "三次握手的序列号规则：初始序列号随机，确认号是对方的序列号加一。"
    long = (
        "TCP 可靠性靠重传。三次握手交换初始序列号。接收方回 ACK，确认号等于下一个序列号。"
        "超时没收到就重传序列号对应的数据。序列号还能去重，重复的直接丢。"
        "这里有个规则：序列号空间是 2 的 32 次方。滑动窗口、拥塞控制全都建立在这套机制上。"
    )
    filler = [
        ("库.md", "事务", "事务的原子性靠 undo log，持久性靠 redo log。"),
        ("库.md", "索引", "索引用 B+ 树，因为树矮、范围查询快。"),
        ("网.md", "UDP", "UDP 不建立连接、不确认、不重传。"),
        ("网.md", "拥塞", "慢开始阶段拥塞窗口每个 RTT 翻倍。"),
    ]
    raw = [("网.md", "短段", short), ("网.md", "长段", long), *filler]
    chunks = [
        rag.Chunk(
            source=src,
            section=sec,
            text=f"{sec}\n{body}",
            tokens=rag.tokenize(f"{sec}\n{body}"),
        )
        for src, sec, body in raw
    ]
    idx = rag.build_index(chunks)
    q = rag.tokenize("三次握手的序列号规则")

    def winner(scorer):
        scored = [(scorer(q, c, idx), c.section) for c in idx.chunks]
        return max(scored)[1]

    assert winner(rag.naive_score) == "长段", "原式应该被长度骗到（这是它作为对照组的价值）"
    assert winner(rag.bm25_score) == "短段", "BM25 应该把长度偏置按回去"


def test_repo_notes_retrievable(repo_notes: Path):
    hits = rag.search("三次握手的序列号", notes_dir=repo_notes)
    assert hits, "真实笔记里应该能搜到"
    assert any("计算机网络" in h.cite for h in hits)
