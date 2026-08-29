# Query 模块功能

`query` 负责安全执行分析 SQL、保存查询结果和执行历史，并把成功 SQL 沉淀为可召回的角色级查询经验。

## 功能清单

```text
Query
→ 执行分析查询
→ 记录查询执行历史
→ 沉淀查询经验
→ 检索查询经验
→ 失效和修复查询经验
```

## 1. 执行分析查询

```text
Explorer 提交 purpose 和 SQL
→ 从 ToolRuntime 读取 user、conversation、analysis 和 session
→ 解析当前用户的 Doris 查询身份
  → 校验用户启用且已绑定角色
  → 读取角色 query_user、workload_group 和 authorization_epoch
  → 在连接前解密查询密码
  → 读取当前 AssetAccessPolicy
→ 关闭 identity PostgreSQL Session
→ 执行 SQL Guard
  → 使用 sqlglot Doris dialect 解析一条 SQL
  → 只允许 SELECT 或最终返回 SELECT 的 WITH
  → 拒绝 DDL、DML、命令和不安全函数
  → 从 metadata 加载表和字段目录
  → 解析 CTE、子查询、表别名和字段限定
  → 部分字段授权的表拒绝不安全星号查询
  → 校验 JOIN 和重复输出列名
  → 校验实际读取的每张表和每个字段
  → 生成 normalized_sql 和结构化校验结果
→ 关闭 metadata PostgreSQL Session
→ 对 normalized_sql 执行 Doris EXPLAIN
  → 提取 ScanNode cardinality 和 avgRowSize
  → 无法得到物理扫描估算时拒绝执行
→ 设置 Doris 查询限制
  → workload_group
  → query_timeout
  → exec_mem_limit
→ 包装行数限制并流式执行
  → SELECT * FROM (...) LIMIT max_rows + 1
  → 使用服务端游标分批读取
  → 校验各批次列名和结果形状一致
→ 流式写临时 CSV
  → 转义表格公式注入值
  → 限制最大 UTF-8 文件字节数
  → 统计 schema、nullable、time_range 和 sample
→ 校验最大行数
→ 原子保存为当前 Explorer Session 下的 query_<uuid>.csv
→ 返回 path、schema、row_count、time_range 和 sample
```

查询同时受 Doris 查询超时、内存、最大行数和最大输出文件限制。完整结果只保存在沙箱 CSV 中，工具响应只返回路径和有限摘要。

执行失败时，工具会区分 SQL 校验拒绝、无权限、计划不可估算、查询超时、Doris 故障、行数超限、文件超限和结果结构异常，并尽量返回具体原因。

## 2. 记录查询执行历史

```text
每次 execute_sql 开始
→ 准备 QueryExecutionContext
  → user_id、role_name、authorization_epoch
  → conversation_id、analysis_id、session_id、tool_call_id
  → purpose 和 raw_sql

SQL 在 Guard 或 EXPLAIN 阶段被拒绝
→ 保存 status=rejected
→ 保存 error_code、error_detail 和 validation

SQL 在执行、结果处理或文件写入阶段失败
→ 保存 status=failed
→ 保存已得到的 normalized_sql、validation 和 plan_estimate
→ 保存具体错误

SQL 成功并提交 CSV
→ 保存 status=succeeded
→ 保存 normalized_sql、plan_estimate 和 result_summary
→ 保存 SQL 模板和 fingerprint
→ 关联生成或更新的 QueryExperience
```

执行历史写入失败只记录日志，不会把成功查询改成失败，也不会覆盖原始查询错误。

## 3. 沉淀查询经验

```text
一次 SQL 成功
→ 使用 sqlglot 将字面量替换为 :pN
→ 得到可复用 sql_template
→ 对模板计算 SHA-256 fingerprint
→ 按 role_name + fingerprint 查找已有经验

经验不存在
→ 创建 QueryExperience
→ 保存当前 authorization_epoch
→ 保存 purpose、sql_template 和 candidate 状态
→ 保存 SQL 使用的表和字段资产快照
→ revision 从 1 开始

经验已经存在
→ 更新 authorization_epoch 和 sql_template
→ 最近最多保留 20 个不同 purpose
→ 替换表和字段资产快照
→ 增加 revision
→ 将 quality 恢复为 candidate

经验保存成功
→ 提交指定 revision 的 Elasticsearch 索引任务
```

资产快照保存稳定 `resource_key` 和当时的 `meta_version`，用于检索时识别元数据是否已经变化。

## 4. 检索查询经验

查询经验没有独立 Agent 工具，由 `recall_context` 使用 query 内置检索，最多返回 3 条。

```text
recall_context 提供 query、role_name 和 authorization_epoch
→ Elasticsearch 全文检索和向量检索并行执行
→ 向量结果应用最低相似度阈值
→ 使用 RRF 融合两个通道名次
→ 按候选 ID 从 PostgreSQL 读取完整经验
→ 校验 quality 仍为 candidate
→ 校验 role_name 和 authorization_epoch 仍匹配
→ 按稳定资源键读取当前元数据
→ 校验每个资产 meta_version 没有过期
→ 使用当前 AssetAccessPolicy 校验全部资产可见
→ 只按融合后的语义名次返回前 N 条
```

```text
一个检索通道失败
→ 返回另一个通道的结果
→ 状态标记为 partial

全文和向量都失败
→ 状态标记为 failed
→ recall_context 将查询经验降级为空列表
→ 元数据召回结果继续返回

候选不足 3 条
→ 按实际数量返回
→ 不使用近期经验补位
```

检索不使用资产重叠、成功次数、采纳次数或最近使用时间进行二次排序。

## 5. 失效和修复查询经验

```text
表或字段元数据发生变化
→ metadata 提供受影响 resource_key
→ 找到引用这些资产的 QueryExperience
→ 将 quality 设置为 disabled
→ 增加或更新索引 revision
→ 从 Elasticsearch 删除经验文档

SELECT 权限回收或 Row Policy 变化
→ identity 轮换角色 authorization_epoch
→ 旧经验不再命中新授权代次过滤

检索时发现资产版本过期
→ 将经验设置为 disabled
→ 删除 Elasticsearch 文档
→ 不把该经验返回给模型

经验 revision 大于 indexed_revision
→ 周期 repair 任务扫描到该经验
→ 重新提交索引任务
→ 当前 revision 同步成功后更新 indexed_revision
```

## 数据、任务和代码

```text
元数据 PostgreSQL
→ QueryExecution
→ QueryExperience
→ QueryExperienceAsset

Elasticsearch
→ 查询经验语义文档

Doris
→ EXPLAIN
→ 受限只读 SQL

Sandbox
→ query_<uuid>.csv

Celery
→ dataagent.query.sync_index
→ dataagent.query.repair_indexes
→ 路由 metadata-index 队列

代码
→ app/query/models
→ app/query/repositories
→ app/query/services/principal.py
→ app/query/services/guard.py
→ app/query/services/executor.py
→ app/query/services/execution_handler.py
→ app/query/services/experience.py
→ app/query/tasks.py
→ app/analytics/agents/explorer/tools/execute_sql.py
```
