# VS Code 常见问题（项目迁移版）

> 针对 2026-10-27 的这次迁移：项目从
> `D:\桌面\Agent开发\agents\study-assistant\` 搬到了 **`D:\桌面\学习助手\`**，
> 包名从 `llm_basics` 改成了 `study_assistant`，`.venv` 是重建的。

**这套问题 90% 都指向同一个原因：VS Code 记着旧路径。** 先按第 0 步做，再往下看。

---

## 0. 先做这一件事（大概率一次解决全部）

### 0.1 用「打开文件夹」打开新目录

`Ctrl+K Ctrl+O` → 选 **`D:\桌面\学习助手`**

> ⚠️ 如果你现在打开的仍然是 `Agent开发\agents\study-assistant`，
> 那是个**空文件夹**（项目已经搬走），下面所有问题都会出现。
> 顺便：`文件 → 打开最近的文件` 里把旧路径那条删掉。

### 0.2 选对 Python 解释器

`Ctrl+Shift+P` → 输入 `Python: Select Interpreter` → 选

```
D:\桌面\学习助手\.venv\Scripts\python.exe
```

看不到这一项就选 **`Enter interpreter path...`** → 手动粘贴上面那行。

### 0.3 重启语言服务器

`Ctrl+Shift+P` → `Python: Restart Language Server`

**做完这三步，八成的红线会消失。** 没消失再往下查。

---

## 1. 「未在所选环境中安装包 jieba / langchain / pydantic」

**症状**：`pyproject.toml` 里 `dependencies` 每一项下面都有黄色波浪线，提示"未在所选环境中安装包 X"。

**原因**：这个提示是 **VS Code 的 Python 扩展**给的，不是 ruff、不是 Python 本身。
它在说"**我选中的那个解释器**里没装这些包"。

**判断方法**（Git Bash，项目根目录）：

```bash
uv run python -c "import jieba, langchain, pydantic; print('都装了')"
```

- 打印「都装了」→ **环境没问题，是 IDE 选错了解释器** → 回第 0 步
- 报 ModuleNotFoundError → 环境真没装 → 跑 `uv sync`

**为什么会选错**：`.venv` 是在新目录重建的，VS Code 记住的是旧目录下的
`.venv\Scripts\python.exe`，那个路径已经不存在了。

---

## 2. `Import "study_assistant" could not be resolved`（Pylance 红线）

**症状**：`from study_assistant.config import Settings` 下面一条红波浪线，但代码能跑。

**原因**：本项目是 **src 布局**（代码在 `src/study_assistant/`），
`src` 不在 `sys.path` 里 —— 它靠 `uv sync` 装成**可编辑包**，才 import 得到。

**排查顺序**：

```bash
# Git Bash，项目根目录
uv run python -c "import study_assistant as s; print(s.__file__)"
```

- 输出 `D:\桌面\学习助手\src\study_assistant\__init__.py` → 装好了，是 IDE 的问题 → 第 0 步
- 报 ModuleNotFoundError → 重装：

```bash
uv sync
# 还不行就强制重装可编辑包
uv sync --reinstall-package study-assistant
```

**Pylance 专用开关**（如果以上都对、还是红线）：

`Ctrl+Shift+P` → `Preferences: Open Workspace Settings (JSON)`，加上：

```json
{
  "python.analysis.extraPaths": ["./src"]
}
```

> 这只是让 Pylance 找到源码，**不改变运行时行为** —— 运行时靠的是可编辑安装。

---

## 3. 测试面板里看不到 tests / 跑不起来

**修复**：`Ctrl+Shift+P` → `Python: Configure Tests` → 选 **pytest** →
选 **Root directory** → 输入 `tests`

或者直接在 `.vscode/settings.json` 里写（本仓库已经带了一份，见文末）。

命令行对照（Git Bash）：

```bash
uv run pytest -q              # 应该 67 passed
uv run pytest tests/test_mcp.py -v
```

> 如果测试面板跑出来是 0 个用例、命令行却是 67 个，
> 那还是解释器问题 —— 回第 0 步。

---

## 4. Ruff 扩展不生效 / 格式化结果和命令行不一样

**症状**：命令行 `uv run ruff check .` 全过，编辑器里却一堆波浪线（或反过来）。

**原因**：Ruff 扩展默认可能**自己下载一个 ruff**，版本和你 `.venv` 里的不一致。
两条规则可能不同，结果自然不同。

**修复**：`.vscode/settings.json` 里指定用项目里的那个：

```json
{
  "ruff.importStrategy": "fromEnvironment",
  "ruff.configurationPreference": "filesystemFirst"
}
```

改完 `Ctrl+Shift+P` → `Ruff: Restart Server`。

**统一口径**（Git Bash）：

```bash
uv run ruff check .          # 静态检查
uv run ruff format --check . # 格式检查
uv run ruff format .         # 真的格式化
```

---

## 5. 终端打开在旧目录 / `cd` 过去报错

**症状**：终端提示符里还是 `agents/study-assistant`，一敲命令就
`No such file or directory`。

**原因**：VS Code 的集成终端会**沿用上次的工作目录**，旧路径已经不存在了。

**修复**：把那个终端标签关掉（点垃圾桶），重开一个 `Ctrl+Shift+\``。

想一劳永逸，在 `.vscode/settings.json` 里固定：

```json
{
  "terminal.integrated.cwd": "${workspaceFolder}"
}
```

> 📌 补充：本项目**已经不再依赖当前工作目录** ——
> `.env` / `study_prompt.txt` / `notes/` 全部锚定到项目根目录。
> 所以你 `cd` 到任何地方跑 `uv run study-assistant` 都不会再报"没找到人设文件"。

---

## 6. 中文乱码 / `UnicodeEncodeError`

**症状**：终端里中文变成 `????` 或 `锟斤拷`；或者 Python 报
`UnicodeEncodeError: 'gbk' codec can't encode`。

**原因**：Windows 终端默认代码页是 GBK（936），而项目文件全是 UTF-8。

**三处都要设**：

1. **文件编码**：`.vscode/settings.json`

   ```json
   { "files.encoding": "utf8", "files.autoGuessEncoding": true }
   ```

   （右下角状态栏也能看到当前编码，点它 → `通过编码重新打开` → `UTF-8`）

2. **Python 输出**：`.env` 里加一行

   ```
   PYTHONIOENCODING=utf-8
   ```

   或者每次运行前 `export PYTHONIOENCODING=utf-8`（Git Bash）

3. **终端代码页**（PowerShell，一次性）：

   ```powershell
   chcp 65001
   ```

> 本项目已经把 `PYTHONIOENCODING` 传给 MCP 子进程了，所以 MCP 那条链路不会乱码。
> 手工 `print` 中文的那部分，靠上面三条。

---

## 7. Git 面板看不到改动 / 显示的是旧仓库

**症状**：左侧源代码管理面板空的，或者提交历史停在 `927b901`。

**原因**：VS Code 打开的还是旧文件夹，或者 Git 扩展缓存了旧的工作区。

**修复**：

1. `Ctrl+K Ctrl+O` 重新打开 `D:\桌面\学习助手`
2. 底部状态栏左下角应该显示 `main*`，点它能切分支
3. 还是不对：`Ctrl+Shift+P` → `Git: Close Repository` → 再 `Git: Open Repository`

命令行对照（Git Bash）：

```bash
cd /d/桌面/学习助手
git status
git log --oneline -3     # 应该能看到 cfba2dc
```

---

## 8. 运行按钮（▶）跑出来的结果不对

**症状**：点右上角 ▶ 运行 `cli.py`，报 `No module named study_assistant`。

**原因**：▶ 用的是"运行当前文件"，工作目录是文件所在目录，
而且不走 `uv run` —— 拿不到项目环境。

**正确跑法**：

- 用 **终端**（Git Bash）：

  ```bash
  uv run study-assistant ask "现在几点"
  ```

- 或者用 **调试配置**：新建 `.vscode/launch.json`

  ```json
  {
    "version": "0.2.0",
    "configurations": [
      {
        "name": "study-assistant: ask",
        "type": "debugpy",
        "request": "launch",
        "module": "study_assistant",
        "args": ["ask", "现在几点"],
        "cwd": "${workspaceFolder}",
        "console": "integratedTerminal"
      }
    ]
  }
  ```

---

## 9. ✅ 一分钟自检

按顺序过，全绿就说明 IDE 配好了：

- [ ] 左下角状态栏显示 `Python 3.13.x ('.venv': venv)`
- [ ] `pyproject.toml` 里的依赖**没有**黄色波浪线
- [ ] `from study_assistant.config import Settings` **没有**红线
- [ ] 测试面板能看到并跑通 **67 个**用例
- [ ] 终端（Git Bash）里 `uv run ruff check .` → `All checks passed!`
- [ ] 终端里 `uv run study-assistant doctor` → 各项 ✅

---

## 10. 还是不行？按这个顺序排除

```bash
# Git Bash，项目根目录
cd /d/桌面/学习助手

# ① 环境真的对吗
uv run python -c "import sys, study_assistant; print(sys.executable); print(study_assistant.__file__)"

# ② 依赖真的全吗
uv sync

# ③ 代码真的能跑吗
uv run study-assistant doctor
uv run pytest -q
```

**如果三条命令行全过、IDE 还是红** → 百分之百是 IDE 的选择问题，不是代码问题。
依次试：

1. `Ctrl+Shift+P` → `Python: Restart Language Server`
2. `Ctrl+Shift+P` → `Developer: Reload Window`
3. `Ctrl+Shift+P` → `Python: Clear Cache and Reload Window`
4. 卸载重装 Python 扩展（最后手段）

> ⚠️ 判断标准只有一条：**命令行对 = 代码没问题**。
> 别为了消掉红线去改代码 —— 那会把一个 IDE 配置问题变成真的 bug。

---

## 附：本仓库推荐的 `.vscode/settings.json`

仓库里已经带了一份（`.vscode/settings.json`），内容就是下面这些。
如果你的编辑器不认，可以手动合并进去。

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/Scripts/python.exe",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": ["tests"],
  "python.analysis.extraPaths": ["./src"],
  "ruff.importStrategy": "fromEnvironment",
  "files.encoding": "utf8",
  "files.autoGuessEncoding": true,
  "terminal.integrated.cwd": "${workspaceFolder}",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": false
  }
}
```

> `editor.formatOnSave` 故意设成 `false`：
> 多人协作时自动格式化会产生大量无关 diff。想开就自己开，但提交前记得
> `uv run ruff format .` 统一一次。
