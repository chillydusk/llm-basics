"""Part H 的两个缺陷 —— 实测脚本（🟢 我写好的，你直接跑）。

    uv run python _demo_rag.py

它干四件事：

    ① 控制变量实验：只改长度，其他不变 —— 看原式怎么被长段骗、BM25 怎么纠正
    ② 真实语料上两种打分的排名对比
    ③ 查「连接」，把命中「无连接」的那一段单独标出来（BM25 修不了）
    ④ 你的语料画像（几段、多长、哪些词最有区分度）

⚠️ 所有数字都是本机实测。换语料数字会变，但**现象**不会变。
"""

from __future__ import annotations

import re
import sys

from study_assistant.rag import (
    Chunk,
    bm25_score,
    build_index,
    get_index,
    idf,
    index_stats,
    naive_score,
    search,
    tokenize,
)

sys.stdout.reconfigure(encoding="utf-8")

BAR = "═" * 78


def rank(query: str, scorer, index):
    """返回 (查询分词, [(分数, 段), ...])，按分数降序。"""
    q = tokenize(query)
    hits = []
    for c in index.chunks:
        s = scorer(q, c, index)
        if s > 0:
            hits.append((s, c))
    hits.sort(key=lambda x: -x[0])
    return q, hits


def line(score: float, c) -> str:
    text = c.text.replace("\n", " ")[:30]
    return f"  {score:6.2f}  [{len(c.tokens):3d} 词] {c.cite:<24} {text}…"


# ══════════════════════════════════════════════════════════════════
#  ① 控制变量实验：手工造一个小语料，只改长度
# ══════════════════════════════════════════════════════════════════

# 短段：直接回答，四个关键词各出现 1 次，「序列号」2 次
SHORT = "三次握手的序列号规则：客户端初始序列号 x 随机，服务端 y 随机，确认号是对方的序列号加一。"

# 长段：同样的四个词各出现 1 次，但「序列号」多出现 2 次 ——
# 不是因为它更相关，只是因为它更长、绕得多。这就是长度偏置的全部秘密。
LONG = (
    "TCP 的可靠性靠重传实现。三次握手时双方交换初始序列号，这只是开始。"
    "之后发送方给每个字节编号，接收方回 ACK，确认号等于期望收到的下一个序列号。"
    "超时没收到 ACK 就重传那段序列号对应的数据。序列号还能去重，"
    "看到重复的序列号直接丢掉。这里有个不成文的规则：序列号空间是 2 的 32 次方，"
    "绕回来的时候要小心。滑动窗口、拥塞控制、延迟确认，全都建立在这套编号机制上。"
)

FILLER = [
    ("数据库.md", "事务", "事务的原子性靠 undo log，持久性靠 redo log，隔离性靠锁和 MVCC。"),
    ("数据库.md", "索引", "索引用 B+ 树，因为树矮、范围查询快、数据只在叶子节点。"),
    ("计算机网络.md", "UDP", "UDP 不建立连接、不确认、不重传，头部只有 8 字节。"),
    ("计算机网络.md", "拥塞控制", "慢开始阶段拥塞窗口每个 RTT 翻倍，到门限后转拥塞避免。"),
]


def controlled_experiment() -> None:
    print(f"\n{BAR}\n① 控制变量实验：两段内容差不多的文本，只改长度，看排名怎么翻\n{BAR}")

    raw = [("计算机网络.md", "短段·直接回答", SHORT), ("计算机网络.md", "长段·讲重传", LONG)]
    raw += [(src, sec, body) for src, sec, body in FILLER]

    chunks = [
        Chunk(source=src, section=sec, text=f"{sec}\n{body}", tokens=tokenize(f"{sec}\n{body}"))
        for src, sec, body in raw
    ]
    idx = build_index(chunks)

    q = "三次握手的序列号规则"
    print(f"查询「{q}」→ 分词：{' / '.join(tokenize(q))}")
    print(f"（语料 {idx.n} 段，平均 {idx.avg_len:.1f} 词/段）")
    print(f"（短段 {len(chunks[0].tokens)} 词，长段 {len(chunks[1].tokens)} 词）\n")

    got: dict[str, dict[str, float]] = {}
    for name, scorer in (("原式", naive_score), ("BM25", bm25_score)):
        _, hits = rank(q, scorer, idx)
        scores = {c.section: s for s, c in hits}
        got[name] = {
            "短段": scores.get(chunks[0].section, 0.0),
            "长段": scores.get(chunks[1].section, 0.0),
        }
        print(f"  ── {name} ──")
        for s, c in hits[:2]:
            print(line(s, c))
        print(f"     👉 第一名：{hits[0][1].section}\n")

    print("  对照（只看前两名）：")
    for name in ("原式", "BM25"):
        short_score, long_score = got[name]["短段"], got[name]["长段"]
        verdict = "长段赢 ← 错" if long_score > short_score else "短段赢 ← 对"
        print(f"    {name:<5} 短段 {short_score:5.2f}｜长段 {long_score:5.2f}　→ {verdict}")
    print(
        "\n  🎯 结论：原式里「出现次数」是唯一信号 → 谁长谁的次数就多 → 谁赢。\n"
        "     BM25 用 tf 饱和（重复到一定程度不再加分）+ 长度惩罚，把长段按了回去。"
    )


# ══════════════════════════════════════════════════════════════════
#  ② 真实语料
# ══════════════════════════════════════════════════════════════════


def real_corpus() -> None:
    idx = get_index()
    print(f"\n\n{BAR}\n② 真实语料：同一问题，两种打分并排\n{BAR}")
    print(index_stats())

    for q in ["索引为什么用 B+ 树", "事务隔离级别有哪几种", "三次握手的序列号怎么定"]:
        _, n_hits = rank(q, naive_score, idx)
        _, b_hits = rank(q, bm25_score, idx)
        if not n_hits:
            continue
        print(f"\n【问题】{q}　分词：{' / '.join(tokenize(q))}")
        print("  ── 原式 ──")
        for s, c in n_hits[:3]:
            print(line(s, c))
        print("  ── BM25 ──")
        for s, c in b_hits[:3]:
            print(line(s, c))
        if n_hits[0][1].cite != b_hits[0][1].cite:
            print(f"  🎯 第一名变了：原式「{n_hits[0][1].cite}」→ BM25「{b_hits[0][1].cite}」")

    lens = [len(c.tokens) for c in idx.chunks]
    print(
        f"\n  注：真实语料最长 {max(lens)} 词 / 最短 {min(lens)} 词，只差 {max(lens) / min(lens):.1f} 倍 ——"
        "\n      因为切分时已经限了 260 字上限，**第一道长度归一化其实在切分那一步就做了**。"
    )


# ══════════════════════════════════════════════════════════════════
#  ③ 不懂语义
# ══════════════════════════════════════════════════════════════════


def semantic_gap() -> None:
    idx = get_index()
    print(f"\n\n{BAR}\n③ 不懂语义：查「建立连接」，「无连接」那一段拿了多高\n{BAR}")

    q = "建立连接"
    print(f"查询「{q}」→ 分词：{' / '.join(tokenize(q))}")
    print(f"「连接」的 idf = {idf('连接', idx):.3f}（出现在 {idx.df['连接']} / {idx.n} 段里）\n")

    for i, h in enumerate(search(q, top_k=6), 1):
        mark = "  ← 这段讲的其实是「无连接」" if "无连接" in h.chunk.text else ""
        print(f"  {i}. {h.score:6.2f}  {h.cite:<26} 命中：{'、'.join(h.matched)}{mark}")

    print(
        "\n  ⚠️ 关键词检索认为「无连接」=「无」+「连接」，是**正相关**的证据。\n"
        "     它看不见「否定」。这是词袋模型的硬伤，调参救不了 —— 只能换语义检索。"
    )


# ══════════════════════════════════════════════════════════════════
#  ④ 语料画像
# ══════════════════════════════════════════════════════════════════


def profile() -> None:
    idx = get_index()
    print(f"\n\n{BAR}\n④ 语料画像\n{BAR}")

    ranked = sorted(((len(c.tokens), i, c) for i, c in enumerate(idx.chunks)))
    n, _, shortest = ranked[0]
    m, _, longest = ranked[-1]
    print(f"最短 {n} 词（{shortest.cite}，是个一句话的测试文件）")
    print(f"最长 {m} 词（{longest.cite}）")
    print(f"平均 {idx.avg_len:.1f} 词　→ 最长 / 平均 = {m / idx.avg_len:.1f} 倍")
    print("   ↑ 切分时已经限了 260 字上限，**第一道长度归一化其实在切分那一步就做了**")

    print("\n一个查询里每个词的 idf（出现在越少段里，越能区分）：")
    for w in tokenize("三次握手的序列号规则"):
        df = idx.df.get(w, 0)
        print(f"  {w:<8} 出现在 {df:2d} / {idx.n} 段　idf={idf(w, idx):.2f}")

    print("\n出现在最多段里的词（idf 低 = 区分度低）：")
    common = sorted(
        ((df, w) for w, df in idx.df.items() if re.search(r"[一-鿿]{2,}", w)), reverse=True
    )[:6]
    for df, w in common:
        print(f"  {w:<8} 出现在 {df:2d} / {idx.n} 段　idf={idf(w, idx):.2f}")

    print("\n分布：")
    buckets: dict[int, int] = {}
    for c in idx.chunks:
        b = len(c.tokens) // 20 * 20
        buckets[b] = buckets.get(b, 0) + 1
    for b in sorted(buckets):
        print(f"  {b:3d}-{b + 19:3d} 词  {'█' * buckets[b]} ({buckets[b]})")


def main() -> None:
    controlled_experiment()
    real_corpus()
    semantic_gap()
    profile()


if __name__ == "__main__":
    main()
