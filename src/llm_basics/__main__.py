"""让 `python -m llm_basics` 能直接跑（🟢 入口胶水，三行）。

没有这个文件的话：

    $ uv run python -m llm_basics "现在几点"
    No module named llm_basics.__main__; 'llm_basics' is a package
    and cannot be directly executed

因为 `-m 包名` 找的是包里的 `__main__.py`，而我们的代码写在 `__init__.py` 里。

现在两种跑法都行（Git Bash，项目根目录）：

    uv run llm-basics "现在几点"              ← pyproject 里注册的命令
    uv run python -m llm_basics "现在几点"    ← 这个文件在起作用
"""

from llm_basics import main

if __name__ == "__main__":
    main()
