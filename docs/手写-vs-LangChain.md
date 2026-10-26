# 手写 Agent 循环 vs LangChain —— 对比表

> **2026-10-26 整理。数据全部来自你自己的代码和你自己跑出来的输出。**
> 这一页是面试讲稿。面完之前，你要能**不看它**把第二节讲出来。

---

## 先说三句话

1. **框架省的是「拼装」**：分片累加、消息构造、schema 生成、字段解析。
2. **框架不省「逻辑」**：工具执行、多轮记忆、循环出口、错误处理，一样都得你写。
3. **框架还会瞒你**：思考链被丢、字段名被换、说明书可能悄悄变空、错误栈被包几层。

---

## 一、主表

### 1. 依赖包数

| | |
|---|---|
| **手写** | **17** 个包（openai / dotenv / pydantic / ruff 等） |
| **LangChain** | **47** 个包 |
| **怎么测** | `uv pip list \| tail -n +3 \| wc -l`，装前装后各一次 |

净增 **30** 个。其中你**一个都没直接用**的：

| 包 | 谁拖进来的 | 用途 |
|---|---|---|
| `langgraph` + `langgraph-checkpoint` + `-prebuilt` + `-sdk` | `langchain` | 多智能体编排，本项目没用到 |
| `langsmith` | `langchain` | 云端链路追踪，你没开 |
| `tiktoken` | `langchain-openai` | OpenAI 分词器，DeepSeek 用不上 |
| `orjson` `ormsgpack` `xxhash` `zstandard` `regex` | 间接依赖 | 你没调用过 |

> **一句话**：省了 50 行循环代码，代价是 30 个包，一半是别的场景准备的。

---

### 2. 流式分片累积

| | |
|---|---|
| **手写** | 按 `tc.index` 分槽；`id`/`name` 先到先得；`arguments` 用 `+=` 拼；约 **30 行** |
| **LangChain** | **分片照样存在**，但累加只要一行：`full += chunk` |
| **实测** | 同一句「读一下 demo.md」→ **11 个**带 `tool_call_chunks` 的片 |

实测分片长相（和手写时几乎一样）：

```
片 1  {'name': 'read_notes', 'args': '',  'id': 'call_...', 'index': 0}
片 2  {'name': None,         'args': '{', 'id': None, ...}
片 3  {'name': None,         'args': '"', ...}
...
片 11 {'name': None,         'args': '}', ...}
```

`AIMessageChunk.__add__` 会把同 index 的 `args` 字符串接起来，并在拼完那一刻自动 `json.loads`。

> ⚠️ **精确结论**：框架**没有消灭分片**，它消灭的是**累加逻辑**。
> 这句话的措辞差别，就是"看过源码"和"背过文档"的差别。

---

### 3. tool_calls 解析

| | |
|---|---|
| **手写** | `args = json.loads(slot["arguments"])` |
| **LangChain** | `tc["args"]` **已经是 dict** |
| **证据** | `langchain_agent.py` 里 `import json` 出现了 **0 次** |

实测返回结构：

```python
[{'name': 'read_notes',
  'args': {'filename': 'demo.md'},     # ← 已经解析好
  'id': 'call_00_ET_...',
  'type': 'tool_call'}]
```

---

### 4 & 5. 消息回填

| | 手写 | LangChain |
|---|---|---|
| **assistant** | 自己拼 dict：`role` / `content` / `tool_calls`（列表推导还原 id、type、name、arguments）**约 15 行** | `messages.append(resp)` **1 行** |
| **tool** | `{"role": "tool", "tool_call_id": ..., "content": ...}` | `ToolMessage(content=..., tool_call_id=tc["id"])` |

> 注意第 5 行：**框架没省多少**，一个 dict 换成一次构造函数调用而已。
> 真正省的是第 4 行——那个 `assistant_message` 字典你在 Part E 里拼了两遍才拼对。

---

### 6. 工具说明书从哪来

**这是全表最反直觉的一行。**

| 写法 | 结果 |
|---|---|
| `bind_tools(TOOLS)`（传你手写的 dict） | **零损耗**。description、参数说明一字不差 |
| `bind_tools([get_time, calculate, read_notes])`（传函数对象） | **三个 description 全变成空字符串 `''`**，参数说明也丢了 |
| `@tool` 装饰器 + 写 docstring | docstring 全塞进 description（连 `Args:` 那段一起），但**参数级说明丢了**，`filename` 只剩 `{"title": "Filename", "type": "string"}` |

**原因**：框架是从【函数名 + 类型注解 + docstring】里抠出说明书的。你那三个工具函数当初没写 docstring，抠出来就是空的。

**你的证据链**（完整闭环）：

1. Part F：模型传 `notes/demo.md` → 工具变成 `notes\notes\demo.md` → 找不到文件
2. 修复手段：在 description 里写「不要带 notes/ 前缀」+ 函数里 `removeprefix`
3. Part G 走 `bind_tools([裸函数])`：**第 2 步的修复被整条回退**
4. 实测它没立刻出事，因为参数名叫 `filename` 已经够自解释 —— **这是运气，不是保证**

> ⚠️ 这就是「假通过」：3 个工具、参数名语义清晰时，空 description 也能蒙对。
> 一旦工具变多、名字变模糊（`query` / `data` / `input`），模型就开始瞎猜。

---

### 7. 思考链 reasoning_content

| | |
|---|---|
| **手写（Part E）** | `delta.reasoning_content` → 深色实时打，用户能看到模型在想什么 |
| **LangChain** | **默认拿不到** |
| **你的实测** | usage 里 `reasoning: 22`（另几次 57 / 110）—— token 照算，正文一个字没给 |

排查过程（我自己跑过一遍）：

```
裸 openai SDK      → reasoning_content 265 个字符  ✅
LangChain invoke   → additional_kwargs 只有 {'refusal': None}
LangChain stream   → 0 个 chunk 有这个属性
content_blocks     → 只有 [{'type': 'text', ...}]
```

> **面试金句**：「LangChain 会告诉你花了多少 reasoning token，但不会给你 reasoning token 的内容。」

---

### 8. 多轮记忆

| | |
|---|---|
| **手写** | `history` 活在 `main()` 里，每轮整段传进 `run_agent` |
| **LangChain** | **一模一样，框架不管** |
| **证据** | 说「37」→ 追问「我刚才给的数是几」→ 两边都答 **37** |

想要框架管？得上 `create_agent` + `checkpointer`。为了存 20 条消息引入 langgraph，不划算。

---

### 9. 消息格式 / trim()

| | 手写 | LangChain |
|---|---|---|
| **格式** | `list[dict]` | `list[消息对象]` |
| **取角色** | `d["role"]` | `m.type` |
| **角色值** | `user` / `assistant` | **`human` / `ai`** ← 注意不是同义词 |
| **trim 代码量** | 5 行 | **5 行，一行没少** |

两边 `trim` 的唯一差别：

```python
tail[0]["role"] == "tool"      # 手写
tail[0].type   == "tool"       # LangChain
```

> 🎯 **反直觉结论**：在消息管理这一层，框架不但没让代码变短，还让你付了额外成本 ——
> 你的工具函数、你的历史、你的截断逻辑，**全部要按它的类型改一遍**。

---

### 10. 费用计算

| | 手写 | LangChain |
|---|---|---|
| **对象** | `usage` 对象，直接点属性 | `usage_metadata` **dict** |
| **字段** | `prompt_cache_hit_tokens`<br>`prompt_cache_miss_tokens` | `input_tokens`（总数）<br>`input_token_details["cache_read"]`（命中数） |
| **未命中怎么算** | 直接读 | **得做减法**：`input_tokens - cache_read` |
| **改动量** | 0 行 | 写了 15 行 `print_usage()` 适配层 |

依据是 `langchain_openai/chat_models/base.py` 里 `_create_usage_metadata` 的源码 ——
它自己算 service tier 时用的也是 `input_tokens - cache_read`，同一套算术。

> **这层适配本身就是成本**：框架换了字段名，你的计费代码就得跟着改。

---

### 11. 出错时看到什么

**你在 G-4 亲身撞过一次，就是最好的证据。**

```python
full = chunk if full is None else full + chunk   # noqa: F821
```

- **ruff 在编辑时**就告诉你：`F821 undefined name 'full'`
- 你把它静音了 → 报错被推迟到**运行时**
- 运行时抛 `UnboundLocalError`，栈里混着框架代码

对比：

| | 报错位置 | 栈长度 | 定位成本 |
|---|---|---|---|
| **手写** | 你写的那一行 | 短 | 一眼看到 |
| **LangChain** | 可能在你写的那行，也可能是框架内部 | 长，一半是 `langchain_core` / `langgraph` | 得往下翻 |

> ⚠️ 你在 Part G 学到的最硬一条：**`# noqa` 是消音器，不是修理工**。
> 它把问题从「编辑时」挪到「运行时」，没有让它消失。

---

### 12. 循环代码行数

精确测量（去掉注释和空行）：

| | 行数 |
|---|---|
| 手写 `run_agent` | **74** |
| LangChain `run_agent` | **18** |
| LangChain 额外适配层 | `trim_lc` 7 + `get_response` 9 + `print_usage` ~15 = **31** |
| **LangChain 合计** | **49** |

> **诚实的结论：省了大约 1/3，不是省 4 倍。**
> 而且省下来的那 25 行，主要是流式累积的样板代码 —— 写过一次就忘不掉的那种。

---

### 13. 换模型 / 换供应商

| | 手写 | LangChain |
|---|---|---|
| **OpenAI 兼容协议内换家** | 改 `base_url` + `model` 两处 | 改两处，一样 |
| **换非兼容协议（如 Anthropic）** | **重写整个调用层** | 装 `langchain-anthropic`，换个类名 |
| **代价** | 换协议 = 重写 | 工具格式、消息格式、usage 字段全变 → **你的适配层要重写** |

> 框架真正值钱的场景是**第二种**：一家没跑通马上换另一家。
> 而你只连 DeepSeek 一家时，它带来的收益就很薄。

---

## 二、三个面试结论

### ① 框架省掉的是哪一层？

> 「省的是**拼装层** —— 流式分片怎么累加、assistant 消息怎么构造、工具调用的 JSON 怎么解析、schema 怎么生成。
> **不省逻辑层** —— 工具还是我来执行，多轮记忆还是我维护，循环什么时候退出还是我判断，出错怎么兜还是我写。
>
> 最典型的一个数：我的循环从 74 行降到 18 行，但为了适配它的消息类型和 usage 字段，又写了 31 行胶水。**净省 1/3。**」

### ② 框架瞒着你扔了什么？

> 「三样东西，都是我自己测出来的。
>
> **第一，思考链。** 同一个 prompt，裸 SDK 能拿到 265 个字符的 reasoning_content，LangChain 一个字都不给 —— 但 usage 里 reasoning token 照算。**token 花了，内容丢了。**
>
> **第二，工具说明书。** `bind_tools` 传函数对象时，框架从 docstring 里抠 description。我那几个函数当初没写 docstring，抠出来就是空的 —— 而空的 description 在工具少、参数名清晰时**也能蒙对**，这比直接报错更危险。
>
> **第三，错误的位置。** 手写时错误栈就在我那一行；框架里一半的栈是它的代码。」

### ③ 选哪个？

> 「看**你要不要能调试**。
>
> 我要控成本、要看到模型每一步在想什么、要能钻进每一行加日志 —— 手写。
> 我要快速试五家模型、团队里别人要能接手、工具生态要标准 —— 框架。
>
> 我现在这个项目，只有一个供应商、只有四个工具、成本要精确到小数点后六位。**所以主线用手写，框架版放旁边当对照组。**」

---

## 三、选做：`create_agent` —— 三行版（已实测）

> ⚠️ 任务卡上写的 `create_react_agent` 在 **langchain 1.4.7 里已经不存在了**。
> 现在叫 `create_agent`，在 `langchain.agents` 下。
> 这条本身就是面试素材：**框架 API 变动很快，你的手写版本不会被 deprecate。**

```python
agent = create_agent(model=build_llm(), tools=[get_time, calculate, read_notes],
                     system_prompt=load_prompt())
out = agent.invoke({"messages": [HumanMessage("读一下 demo.md，告诉我实验编号")]})
```

| 问题 | 答案 |
|---|---|
| `out` 是什么结构？ | `dict`，只有一个 key `'messages'`，里面是完整消息序列 |
| 里面有哪几条？ | `HumanMessage` → `AIMessage`（空 content + tool_calls）→ `ToolMessage` → `AIMessage`（最终答案） |
| 你的 `while` / `ToolMessage` 回填 / `TOOL_MAP` 分发去哪了？ | **全部消失**，被 LangGraph 的图执行替代 |
| `@tool` 保住了参数级说明吗？ | **没有**。`filename` 只剩 `{"title": "Filename", "type": "string"}`，docstring 全挤在总 description 里 |
| 还能在框架内部加日志、超时、重试吗？ | 能，但要学 `middleware` / `interrupt_before` 这套新概念 —— **学习成本换回来了** |

**一行总结**：`create_agent` 把 `run_agent` 的 18 行也省掉了，代价是你再也看不见中间过程，
除非去读 LangGraph 的状态机文档。

---

## 四、三分钟自测（面试前必过）

合上这一页，用嘴答：

1. `bind_tools` 传 dict 和传函数对象，框架分别做了什么？**哪个会让说明书变空？**
2. 流式下 `tool_calls` 的分片，LangChain 帮你做了什么、**没帮你做什么**？
3. 为什么说 `# noqa` 是消音器？你在哪一步被它坑过？
4. 自写 `trim` 和 LangChain 版 `trim` 差几行？**这个"没变短"说明了什么？**
5. 用一句话说清楚：什么情况下你会选手写，什么情况下选框架？

**五题都能答，这一节才算过。** 答不上的回主表对应那一行再看一遍。
