"""study-assistant · Part H：最小 RAG（关键词基线）。

一句话：**把 notes/ 切成小段，给每段算一个「和问题有多像」的分，返回最高的几段。**

    切分 chunking  →  分词 tokenize  →  打分 score  →  排序取前 k  →  带引用交给模型

⚠️ 为什么不用向量：DeepSeek 没有 embedding 接口（实测 404）。
   所以 v1 走关键词。这不是妥协 —— 做检索本来就该先立一个能跑的基线，
   再拿它量出「升级到语义检索能提升多少」。没有基线的升级叫换玄学。

⚠️ 本模块已知的两个缺陷（都能实测复现，见 _demo_rag.py）：

    ① 长度偏置：长片段天然容易命中更多次 → 分虚高。
       修法：BM25 的长度归一化（k1 / b 两个参数）。
    ② 不懂语义：「无连接」和「连接」在关键词眼里是同一件事 ——
       分词器把「无连接」切成「无 / 连接」，于是讲 UDP 的段子会命中「连接」。
       **这个修不了**，只能靠向量。它就是你 v2 的升级理由。

跑法（Git Bash）：

    uv run study-assistant search "三次握手的序列号怎么定"   ← 只测检索，不花钱
    uv run python _demo_rag.py                              ← 看两个缺陷的实测数字
"""

from __future__ import annotations

import logging
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import jieba

# jieba 首次加载词典会往终端打 "Building prefix dict..."，对 CLI 来说太吵
jieba.setLogLevel(logging.WARNING)

# ── 参数（都是可调的，括号里是调大/调小的后果）────────────────────
MAX_CHARS = 260  # 一段最多多少字（调大：上下文更全但更吵；调小：更准但容易断章取义）
TOP_K = 3  # 默认返回几段
K1 = 1.5  # BM25 词频饱和系数（调大：重复出现的词更能加分）
B = 0.75  # BM25 长度归一化强度（0 = 不归一化，1 = 完全按长度折平）

_NOTES_DIR = Path("notes")

# 停用词：这些词在任何一段里都常见，留着只会稀释分数
_STOP = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "们",
    "这", "那", "就", "都", "也", "还", "又", "很", "和", "与",
    "及", "或", "吗", "呢", "吧", "啊", "呀", "哦", "么",
    "一个", "一种", "一些", "可以", "什么", "怎么", "怎样", "如何", "为什么",
    "一下", "我们", "你们", "他们", "这个", "那个", "这些", "那些",
    "不是", "没有", "而且", "但是", "因为", "所以", "如果", "虽然",
    "然后", "已经", "自己", "时候", "东西", "事情",
    "the", "a", "an", "is", "are", "of", "to", "and", "or",
}  # fmt: skip


# ══════════════════════════════════════════════════════════════════
#  ① 切分：把一份笔记切成若干「段」
# ══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Chunk:
    """一段。检索的最小单位。"""

    source: str  # 来自哪个文件，如 "计算机网络.md"
    section: str  # 来自哪个小节，如 "一、TCP 三次握手"
    text: str  # 正文（**已经拼上了小节标题**，见下面的解释）
    tokens: tuple[str, ...]  # 分好的词

    @property
    def cite(self) -> str:
        """引用标签：《文件》· 小节。"""
        return f"《{self.source}》· {self.section}"


_HEADING = re.compile(r"^(#{1,6})\s*(.+?)\s*$")


def _split_long(body: str, max_chars: int) -> list[str]:
    """一段太长就按空行拆，再把小段贪心地攒回到 max_chars 以内。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    out: list[str] = []
    buf = ""
    for p in paras:
        if len(p) > max_chars:  # 单个自然段就超长 → 先冲掉 buf，再硬切
            if buf:
                out.append(buf)
                buf = ""
            out.extend(p[i : i + max_chars] for i in range(0, len(p), max_chars))
            continue
        if not buf:
            buf = p
        elif len(buf) + 1 + len(p) <= max_chars:
            buf = f"{buf}\n{p}"
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out


def chunk_text(text: str, source: str, max_chars: int = MAX_CHARS) -> list[Chunk]:
    """按 markdown 标题切段；超长的再按空行切。

    🎯 一个容易被忽略的设计：**把小节标题拼进正文**。
       用户问「三次握手」，正文里可能只有「建立连接要三次」，一个字都没提"握手"。
       标题 `## 一、TCP 三次握手` 里明明有 —— 不拼进去就检索不到。
       （这是 RAG 里最便宜、收益最高的一招：标题即摘要。）
    """
    chunks: list[Chunk] = []
    section = "(开头)"
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        buf.clear()
        if not body:
            return
        for piece in _split_long(body, max_chars):
            # 标题拼进正文，否则「标题里有、正文里没有」的词永远搜不到
            full = f"{section}\n{piece}" if section else piece
            chunks.append(
                Chunk(
                    source=source,
                    section=section,
                    text=full,
                    tokens=tokenize(full),
                )
            )

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            section = m.group(2).strip()
            continue
        buf.append(line)
    flush()
    return chunks


# ══════════════════════════════════════════════════════════════════
#  ② 分词：中文没有空格，得先切开
# ══════════════════════════════════════════════════════════════════

_PUNCT = re.compile(r"^[\W_]+$")  # 全是标点/符号 → 丢掉


def tokenize(text: str) -> tuple[str, ...]:
    """jieba 分词 + 去掉停用词和标点。

    实测（jieba 0.42.1）：
        "三次握手是怎么建立连接的" → 三次 / 握手 / 是 / 怎么 / 建立 / 连接 / 的
        "UDP 是无连接的传输协议"  → UDP / 是 / 无 / 连接 / 的 / 传输 / 协议

    ⚠️ 第二行就是缺陷②的现场：**「无连接」被切成了「无」+「连接」**。
       于是「UDP 是怎么建立连接的」这个查询会高高兴兴地命中断言"无连接"的那一段。
       关键词检索没有能力区分「连接」和「无连接」。

    ⚠️ 为什么不去掉单字：中文单字经常是有效信息（树、锁、页、栈）。
       去掉能少一点噪音，但也会把「B+ 树」拆完后剩下的「树」一起干掉。
       这个基线先留着，用数据说话。
    """
    words = jieba.lcut(text)
    return tuple(
        w.strip() for w in words if w.strip() and not _PUNCT.match(w) and w.lower() not in _STOP
    )


# ══════════════════════════════════════════════════════════════════
#  ③ 建索引：统计 df（每个词出现在几段里）
# ══════════════════════════════════════════════════════════════════


@dataclass
class Index:
    chunks: list[Chunk]
    df: Counter  # 词 → 出现在几段里
    avg_len: float  # 平均每段多少个词（BM25 用）

    @property
    def n(self) -> int:
        return len(self.chunks)


def build_index(chunks: list[Chunk]) -> Index:
    df: Counter = Counter()
    for c in chunks:
        df.update(set(c.tokens))  # set：同一段里出现 10 次也只算 1 段
    avg = sum(len(c.tokens) for c in chunks) / len(chunks) if chunks else 0.0
    return Index(chunks=chunks, df=df, avg_len=avg)


def idf(term: str, index: Index) -> float:
    """逆文档频率：log(总段数 / 含这个词的段数)。

    「的」出现在每一段 → log(N/N) = 0 → 一点分都不加（停用词是它的手工版）。
    「握手」只在 2 段里出现 → log(20/2) ≈ 2.3 → 很有区分度。
    """
    df = index.df.get(term, 0)
    return math.log(index.n / df) if df else 0.0


# ══════════════════════════════════════════════════════════════════
#  ④ 打分：这就是 H.3 的核心
# ══════════════════════════════════════════════════════════════════


def naive_score(query_tokens, chunk: Chunk, index: Index) -> float:
    """v0 原式：score = Σ (词在这段出现次数) × log(总段数 / 含该词的段数)。

    ⚠️ 这个函数留着**不是为了用，是为了当对照组**。
       它有一个致命缺陷：`次数` 会随段落变长而变大 —— 长段天然占便宜。
       跑 _demo_rag.py 你能看到它把一篇又长又跑题的段子排到了正确短段前面。
    """
    tf = Counter(chunk.tokens)
    return sum(tf.get(t, 0) * idf(t, index) for t in set(query_tokens))


def bm25_score(query_tokens, chunk: Chunk, index: Index, k1: float = K1, b: float = B) -> float:
    """v1 修好版：BM25 的两个补丁。

    补丁 A —— 词频饱和（k1）：
        原式里一个词出现 10 次就加 10 倍分。
        但一段里出现 10 次「握手」，并不会比出现 3 次更相关 3 倍。
        BM25 用  tf*(k1+1) / (tf + k1*...)  让它封顶在 (k1+1) 附近。

    补丁 B —— 长度归一化（b）：
        除以这段的长度（相对平均长度）。**这就是任务卡里说的 `b` 参数。**
        b=0 → 完全不归一化（退化成原式）；b=1 → 长段被彻底折平；
        b=0.75 → 常用默认值。

    分母里的 `1 - b + b*dl/avgdl` 就是「这段比平均长多少」的惩罚项。
    """
    if not index.avg_len:
        return 0.0
    dl = len(chunk.tokens)
    tf = Counter(chunk.tokens)
    total = 0.0
    for t in set(query_tokens):
        f = tf.get(t, 0)
        if not f:
            continue
        denom = f + k1 * (1 - b + b * dl / index.avg_len)
        total += idf(t, index) * f * (k1 + 1) / denom
    return total


@dataclass
class Hit:
    chunk: Chunk
    score: float
    matched: list[str]  # 命中了查询里的哪些词（给用户看，方便调试）

    @property
    def cite(self) -> str:
        return self.chunk.cite


def search(
    query: str,
    top_k: int = TOP_K,
    *,
    index: Index | None = None,
    scorer=bm25_score,
    notes_dir: Path | None = None,
) -> list[Hit]:
    """给一个问题，返回最相关的 top_k 段。"""
    index = index or get_index(notes_dir)
    q_tokens = tokenize(query)
    if not q_tokens or not index.chunks:
        return []

    q_set = set(q_tokens)
    hits: list[Hit] = []
    for c in index.chunks:
        s = scorer(q_tokens, c, index)
        if s > 0:
            hits.append(Hit(chunk=c, score=s, matched=sorted(q_set & set(c.tokens))))

    # 分高的在前；同分时短的在前（同分时短段信息密度更高，是个便宜的好习惯）
    hits.sort(key=lambda h: (-h.score, len(h.chunk.tokens)))
    return hits[:top_k]


# ══════════════════════════════════════════════════════════════════
#  ⑤ 语料加载（带 mtime 缓存：笔记改了自动重建索引）
# ══════════════════════════════════════════════════════════════════

_CACHE: dict[Path, tuple[tuple, Index]] = {}


def _resolve_dir(notes_dir: Path | None) -> Path:
    """没传就用当前工作目录下的 notes/（命令行直接跑时方便）。"""
    return (notes_dir or Path("notes")).resolve()


def _signature(root: Path) -> tuple:
    """所有笔记的 (路径, 修改时间, 大小)。任何一个变了 → 重新建索引。"""
    if not root.exists():
        return ()
    return tuple(
        sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in root.glob("*.md"))
    )


def load_chunks(notes_dir: Path | None = None) -> list[Chunk]:
    """读 notes/*.md，切成段。"""
    root = _resolve_dir(notes_dir)
    if not root.exists():
        return []
    chunks: list[Chunk] = []
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")  # ⚠️ 必须 utf-8，否则 Windows 下按 GBK 乱码
        chunks.extend(chunk_text(text, source=path.name))
    return chunks


def get_index(notes_dir: Path | None = None) -> Index:
    """拿索引；笔记变了就重建（几份笔记重建一次是毫秒级，不用怕）。

    按目录分别缓存 —— 因为同一个进程里可能既查默认的 notes/，
    又被测试指向临时目录。
    """
    root = _resolve_dir(notes_dir)
    sig = _signature(root)
    cached = _CACHE.get(root)
    if cached is None or cached[0] != sig:
        cached = (sig, build_index(load_chunks(root)))
        _CACHE[root] = cached
    return cached[1]


def index_stats(notes_dir: Path | None = None) -> str:
    idx = get_index(notes_dir)
    return (
        f"索引：{idx.n} 段｜{len(idx.df)} 个不同的词｜平均 {idx.avg_len:.1f} 词/段"
        f"｜来源 {len({c.source for c in idx.chunks})} 份笔记"
    )


# ══════════════════════════════════════════════════════════════════
#  ⑥ 出口 ①：给人看（命令行调试）
# ══════════════════════════════════════════════════════════════════


def format_hits(query: str, hits: list[Hit], *, max_chars: int = 320) -> str:
    """把检索结果排成一段文本 —— **模型看到的就是这个**。"""
    if not hits:
        return (
            f"没有找到和「{query}」相关的笔记片段。\n"
            "可以换个关键词再搜一次，或者用 read_notes 直接读整份笔记。"
        )

    lines = [f"检索「{query}」命中 {len(hits)} 段（相关度由高到低）："]
    for i, h in enumerate(hits, 1):
        body = h.chunk.text
        if len(body) > max_chars:
            body = body[:max_chars] + "…"
        quoted = "\n".join("> " + ln for ln in body.splitlines())
        lines.append(
            f"\n【{i}】{h.cite}　相关度 {h.score:.2f}\n"
            f"命中词：{'、'.join(h.matched) or '（无）'}\n{quoted}"
        )
    lines.append("\n（回答时请注明来源，格式：《文件名》· 小节名）")
    return "\n".join(lines)


def cli(argv: list[str], notes_dir: Path | None = None) -> int:
    """独立跑检索，不花 token。等价于 `study-assistant search "..."`。"""
    if not argv:
        print('用法：study-assistant search "你的问题"')
        return 2
    query = " ".join(argv)
    print(index_stats(notes_dir))
    print()
    print(format_hits(query, search(query, notes_dir=notes_dir)))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 管道下中文不乱码
    raise SystemExit(cli(sys.argv[1:]))
