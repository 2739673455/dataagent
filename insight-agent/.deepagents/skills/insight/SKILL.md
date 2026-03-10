---
name: insight
description: 当任务涉及业务数据查询、数据清洗、统计分析、数据文件处理、pandas 分析或在会话工作区中编写 Python 数据处理脚本时使用此技能。优先通过 `db_query` 工具查询数据库；查询结果会保存到当前会话工作区，可继续用 pandas 读取、分析、汇总、导出或生成衍生文件。工作区 Python 环境使用 uv 管理，执行脚本、单文件命令或安装依赖时都要使用 `uv run` 或 `uv add`，不要直接调用 `python`。
---

# 数据分析技能

## 快速参考

| 任务                 | 做法                                 |
| -------------------- | ------------------------------------ |
| 查询业务数据库       | 调用 `db_query` 工具                 |
| 读取查询结果文件     | 优先使用 pandas                      |
| 执行 Python 脚本     | 使用 `uv run script.py`              |
| 执行单条 Python 命令 | 使用 `uv run python -c "..."`        |
| 安装分析依赖         | 使用 `uv add 包名`                   |
| 工作区内落分析结果   | 写入当前会话工作区，避免写到工作区外 |

## 工作流

1. 先判断是否需要查询数据库数据。
2. 需要时调用 `db_query`，不要手写数据库连接或绕过现有工具。
3. 读取 `db_query` 返回的 `file_path`、`fields`、`preview_rows`，确认结果结构。
4. 需要进一步分析时，在当前会话工作区内编写脚本或 notebook 风格的临时 `.py` 文件。
5. 使用 pandas 读取结果文件并完成清洗、聚合、统计、透视、导出等处理。
6. 将衍生结果继续保存在工作区内，并在回复中引用生成的文件路径。

## db_query 工具

`db_query` 用于把自然语言查询发送给 data-agent。

工具返回重点关注这些字段：

- `status`: 是否成功
- `file_path`: 查询结果已保存到当前会话工作区的文件路径
- `file_format`: 结果文件格式，通常是 `csv` 或 `json`
- `pandas_read_hint`: 推荐的 pandas 读取方式
- `fields`: 结果字段名
- `preview_rows`: 前几行样例数据
- `row_count`: 结果行数，表格结果时提供

使用约定：

- 只把 `db_query` 当作数据库入口。
- 如果 `status` 为 `error`，先基于错误信息调整查询，再决定是否重试。
- 拿到结果后，优先基于工作区中的文件继续分析，而不是重复查询。

## pandas 处理

常见读取方式：

```bash
uv run python -c "import pandas as pd; df = pd.read_csv('/abs/path/result.csv'); print(df.head())"
```

```bash
uv run python -c "import pandas as pd; df = pd.read_json('/abs/path/result.json'); print(df.head())"
```

建议：

- 表格结果优先读成 DataFrame 再处理。
- 做字段检查时，先看 `df.columns`、`df.dtypes`、`df.head()`。
- 做分析时优先产出可复用文件，如清洗后的 `.csv`、汇总后的 `.csv`、中间结果 `.json`。
- 如果结果较大，先抽样或只保留分析所需字段，避免无意义的大文件反复处理。

## Python 执行规则

工作区 Python 环境由 `uv` 管理，默认规则如下：

- 不要直接运行 `python script.py`
- 不要直接运行 `pip install`
- 要用 `uv run script.py`
- 要用 `uv run python -c "..."` 执行单条 Python
- 要用 `uv add pandas openpyxl ...` 安装新增依赖

正确示例：

```bash
uv run analyze_sales.py
```

```bash
uv run python -c "import pandas as pd; df = pd.read_csv('db_query_results/result.csv'); print(df.describe())"
```

```bash
uv add seaborn
```

错误示例：

```bash
python analyze_sales.py
```

```bash
pip install seaborn
```

## 工作区约束

- 所有分析脚本、查询结果、清洗结果、导出文件都应保存在当前会话工作区。
- 优先使用相对清晰的目录名，例如 `db_query_results/`、`analysis/`、`outputs/`。
- 不要把分析产物写到工作区外。
- 回复用户时，如果生成了文件，明确给出工作区内文件路径和用途。

## 输出要求

- 如果只是回答结论，给出结论同时引用关键字段和样例数据来源。
- 如果做了进一步处理，说明读取了哪个结果文件、产出了哪些新文件。
- 如果结果适合继续复用，优先保留结构化文件，而不只是在回复里贴文本。
