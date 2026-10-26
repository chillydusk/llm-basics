# study-assistant

> 一个**不依赖 Agent 框架**、从零手写的命令行学习助手。
> 会调工具、会查笔记、答案标注出处、每一轮都告诉你花了多少钱。

![终端实录（节选）](docs/demo.svg)

*上图是真实运行输出（节选）。想看动图的话可以自己录一个，方法见 [docs/录屏说明.md](docs/录屏说明.md)。*

---

## 这是什么

一个跑在终端里的 AI 学习助手，人设是「计算机学习导师」。

它的特别之处不在于功能多，而在于**每一层都是自己写的**：

| 能力 | 实现方式 |
|---|---|
| Agent 循环 | 手写 ReAct 风格：流式累积工具调用 → 执行 → 回填 → 继续推理 |
| 工具调用 | 4 个工具，自己定义 schema、自己分发、失败不中断 |
| 上下文管理 | 自己实现截断，保住 system 人设、丢掉孤儿 tool 消息 |
| 成本核算 | 流式 usage → 缓存命中/未命中分开计价 → 元，精确到小数点后 6 位 |
| 检索增强 | 自建关键词基线：切分 + jieba 分词 + BM25 打分 + 引用 |
| 框架对照 | 同一套工具用 LangChain 再实现一遍，量化「框架省了什么、瞒了什么」 |

**这不是什么**：不是 RAG 教程、不是 LangChain 封装、不是聊天玩具。
它的价值在于**可解释** —— 你能看到模型每一步在想什么、调了什么工具、花了多少钱，
也能看到每个设计选择的代价。

---

## 快速开始

**前置**：Python 3.13+、[uv](https://docs.astral.sh/uv/)

```bash
# 1. 拿代码
git clone https://github.com/chillydusk/study-assistant.git
cd study-assistant

# 2. 配置 key（去 https://platform.deepseek.com/ 申请）
cp .env.example .env
#    然后编辑 .env，把 sk-xxxx 换成你自己的

# 3. 装依赖
uv sync

# 4. 跑
uv run python -m llm_basics "三次握手的序列号是怎么定的？"
```

> ⚠️ **必须在本项目根目录运行。**
> `.env`、`study_prompt.txt`、`notes/` 三个路径都是相对当前目录找的 ——
> 在别的目录下跑会直接报「没找到 study_prompt.txt」。这是已知设计债，见[已知限制](#已知限制)。

### 交互模式

```bash
uv run python -m llm_basics
```

```
输入 /exit 退出，/clear 清空对话

你 > 帮我看看现在几点，再算一下 12345 + 67890
      ⚙️  get_time({})
      ⚙️  calculate({"a": 12345, "b": 67890})
      ...
你 > /clear
会话已清空，只保留人设。
```

- `/exit` 或 `/quit` 退出（退出码 0）
- `/clear` 清空对话，只保留人设
- `Ctrl+D` 干净退出，`Ctrl+C` 退出码 130，都不会甩一屏 traceback

### 单独测检索（不花 token）

```bash
uv run python -m llm_basics.rag "三次握手的序列号怎么定"
uv run python _demo_rag.py          # 看关键词检索的两个缺陷实测
```

---

## 架构

```
                      ┌───────────┐
   你 > 问题 ────────►│  main()   │  REPL / 单问 / /clear / 退出码
                      └─────┬─────┘
                            │  history: list[dict]，每轮整段传下去
                            ▼
                      ┌───────────┐
                      │  trim()   │  超 20 条就丢最早的；保 system；清孤儿 tool
                      └─────┬─────┘
                            ▼
                     ╔═════════════╗
                     ║ run_agent() ║ ◄──── 工具结果回填后回到循环开头
                     ╚══════╤══════╝
                            │ 1. 流式请求
                            ▼
                      ┌───────────┐
                      │ DeepSeek  │
                      └─────┬─────┘
                            │ 2. finish_reason
                    ┌───────┴───────┐
               tool_calls         stop
                    │               │
                    ▼               ▼
              ┌───────────┐   ┌───────────┐
              │  TOOL_MAP │   │  answer   │
              └─────┬─────┘   └───────────┘
                    │ 3. 执行工具，结果包成 tool 消息
                    │
                    ├── get_time       当前时间（北京时间 UTC+8）
                    ├── calculate      两数相加
                    ├── read_notes     读整份笔记
                    └── search_notes   检索笔记（带引用）
                              │
                              ▼
                        ┌───────────┐
                        │  rag.py   │  notes/*.md
                        └───────────┘
                        切段 → jieba → BM25 → Top-K → 带引用
```

**关键点**：模型只负责「决定调哪个工具、传什么参数」，**执行永远在我的代码里**。
它在云端，没有我的时钟、没有我的文件 —— 这是 Tool Calling 的第一性原理。

---

## 技术要点

### 1. 流式工具调用：分片要自己拼

流式下 `tool_calls` 是**碎片**：第一片带 `id` 和 `name`，之后几十片是 `arguments`
的逐字符分片。按 `index` 分槽累积，**第一片先到先得，arguments 永远拼接**。
攒完才能 `json.loads` —— 把半截 JSON 塞回 API 会直接 400。

### 2. 上下文截断：三条不能破的线

- 第一条 system（人设）永远留住
- 不是原 list 的引用，而是新 list（否则 `append` 会污染会话状态）
- 开头若是孤儿 `tool` 消息就丢掉（它的配对 assistant 已被裁掉 → API 400）

仓库里有两个可执行的自检脚本，共 14 项不变量断言：
`_selftest_trim.py`（手写版）、`_selftest_trim_lc.py`（LangChain 版）。

### 3. 成本：缓存命中要分开算

DeepSeek 的 usage 里，输入分成 `prompt_cache_hit_tokens` 和
`prompt_cache_miss_tokens` 两笔，单价差 50 倍。直接拿 `prompt_tokens` 乘单价会算错。
本项目按命中/未命中分别计价，再判断峰谷时段（高峰 ×2）。

### 4. 检索：先立基线，再谈升级

DeepSeek **没有 embedding 接口**（实测 404），所以 v1 走关键词：

```
score = Σ 词频 × log(总段数 / 含该词的段数)      ← 朴素版
      ↓ 加上词频饱和 + 长度归一化
score = Σ idf × tf·(k1+1) / (tf + k1·(1-b+b·|d|/avgdl))   ← BM25
```

两个缺陷都做了**可复现的量化**（`uv run python _demo_rag.py`）：

| 缺陷 | 实测 |
|---|---|
| 长度偏置 | 控制实验：朴素版 长段 9.89 > 短段 6.59（**错**）；BM25 短段 5.57 > 长段 3.42（**对**） |
| 不懂语义 | 查「建立连接」→ 讲「**无连接**」的 UDP 段以 5.42 排第一。调参救不了，只能上 embedding |

> 面试金句：我的关键词基线有个已知缺陷 ——「无连接」会匹配到「连接」。
> 我能量化它，这就是我下一步要上向量检索的原因。

### 5. 工具是工具，不是流程

检索**做成了工具**而不是「提问前先查一遍」。

- 塞进主流程：问「现在几点」也要翻一遍笔记，白花 token，还容易被笔记内容带偏
- 做成工具：模型自己决定要不要查。问时间就调 `get_time`，问笔记才调 `search_notes`

决策权在模型，执行权在代码。

---

## 项目结构

```
study-assistant/
├── src/llm_basics/
│   ├── __init__.py          手写 Agent 主程序：工具层 + 循环 + REPL
│   ├── rag.py              最小 RAG：切分 / 分词 / BM25 / 引用格式
│   ├── langchain_agent.py  LangChain 对照实现（同一套工具、同一个人设）
│   └── __main__.py         让 `python -m llm_basics` 能跑
├── notes/                  笔记语料（检索对象）
├── docs/
│   ├── 手写-vs-LangChain.md 两套实现的对比表（含实测数据）
│   └── demo.svg            终端实录
├── _selftest_trim.py       截断逻辑自检（7 项）
├── _selftest_trim_lc.py    LangChain 版截断自检（7 项）
├── _demo_rag.py            关键词检索两个缺陷的实测脚本
├── study_prompt.txt        人设
└── .env.example            配置模板（真 key 在 .env，已被 gitignore）
```

---

## 实测数据

| 项 | 数值 |
|---|---|
| 依赖包 | 纯手写 **18** 个 → 引入 LangChain 后 **48** 个（+30，其中 langgraph / langsmith / tiktoken 一个都没直接用） |
| Agent 循环代码 | 手写 74 行 → LangChain 18 行 + 适配层 31 行（净省 1/3，不是 4 倍） |
| 一次两轮问答成本 | ≈ 0.003 元（deepseek-flash，含两次检索） |
| 检索语料 | 16 段 / 4 份笔记 / 427 个词项 |
| 流式分片 | 一句「读一下 demo.md」收到 11 个 tool_call 分片 |

完整对比见 [docs/手写-vs-LangChain.md](docs/手写-vs-LangChain.md)，那里有 13 行主表和三个面试结论。

---

## 已知限制

写在这里，是因为**知道自己系统的边界比功能列表更能说明水平**。

1. **关键词检索不懂语义**。「无连接」会命中「连接」，否定、同义词、缩写全部抓不到。
   量化结果见上方表格。下一步：接 embedding（DeepSeek 不提供，需换供应商或本地模型）。
2. **`read_notes` 没有路径校验**。传 `../../.env` 能读到密钥。
   应该在读之前 `resolve()` 并检查是否还在 `notes/` 目录内。
3. **三个路径依赖当前工作目录**（`.env` / `study_prompt.txt` / `notes/`），
   必须在项目根目录运行。正确做法是全部基于 `Path(__file__)` 定位。
4. **无持久化**。关掉终端对话就没了，没有 checkpointer / 数据库。
5. **单用户单会话**。没有并发，没有鉴权 —— 它本来就是个本地 CLI 工具。

---

## 路线图

- [x] 手写 ReAct 循环（流式 + 工具回填 + 多轮）
- [x] 成本核算与人设加载
- [x] 关键词检索基线 + 引用
- [x] LangChain 对照实现与量化对比
- [ ] 向量检索（找一家有 embedding 的供应商，量出相对基线的提升）
- [ ] 路径安全（`read_notes` 路径穿越）
- [ ] 会话持久化

---

## License

MIT
