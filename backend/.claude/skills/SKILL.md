---
name: data-query
description: 这个技能用于根据用户的查询问题，从元数据存储中搜索相关信息，并基于元数据编写SQL进行查询
---

# Data Query
## 任务清单
1. 添加上下文信息
2. 扩展信息
    - LLM根据上下文扩展查询时可能需要的字段
    - LLM根据上下文扩展查询时可能需要的字段值
3. 检索信息
    - 结合查询和关键词，检索相关知识
    - 结合关键词和扩展后的字段，检索相关字段
    - 结合关键词和扩展后的字段值，检索相关字段值
4. 合并字段与单元格信息，并根据检索分数截取topk表和字段
5. 过滤信息
    - LLM过滤掉不需要的表和字段
    - LLM过滤掉不需要的知识
6. 获取知识相关字段，并与之前检索出的字段合并
## 1. 添加上下文信息
执行脚本 `uv run scripts/add_context.py 查询文本` 添加相关上下文信息
## 2. LLM根据上下文扩展查询时可能需要的字段 & LLM根据上下文扩展查询时可能需要的字段值
并行执行下列脚本
- 执行脚本 `uv run scripts/extend_column.py` 扩展字段
- 执行脚本 `uv run scripts/extend_cell.py` 扩展字段值
## 3. 结合查询和关键词，检索相关知识 & 结合关键词和扩展后的字段，检索相关字段 & 结合关键词和扩展后的字段值，检索相关字段值
并行执行下列脚本
- 执行脚本 `uv run scripts/recall_column.py` 检索字段信息
- 执行脚本 `uv run scripts/recall_cell.py` 检索单元格信息
- 执行脚本 `uv run scripts/recall_knowledge.py` 检索知识信息
## 4. 合并字段与单元格信息，并根据检索分数截取topk表和字段
执行脚本 `uv run scripts/merge_col_cell.py` 合并并截取表与字段信息
## 5. LLM过滤掉不需要的表和字段 & LLM过滤掉不需要的知识
并行执行下列脚本
- 执行脚本 `uv run scripts/filter_tb_col.py` 过滤表与字段
- 执行脚本 `uv run scripts/filter_knowledge.py` 过滤知识
## 6. 获取知识相关字段，并与之前检索出的字段合并
执行脚本 `uv run scripts/add_kn_col.py` 获取知识相关字段并与先前字段信息合并