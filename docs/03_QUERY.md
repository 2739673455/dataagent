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
→ 管理查询经验
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
→ 流式执行
  → 使用服务端游标分批读取
  → 校验各批次列名和结果形状一致
→ 流式写临时 CSV
  → 转义表格公式注入值
  → 统计 columns、nullable、time_range 和 sample
→ 原子保存为当前 Explorer Session 下的 query_<uuid>.csv
→ 返回 path、columns、row_count、time_range 和 sample
```

查询受 Doris 查询超时和内存限制。完整结果只保存在沙箱 CSV 中，工具响应只返回路径和有限摘要。

执行失败时，工具会区分 SQL 校验拒绝、无权限、计划不可估算、查询超时、Doris 故障和结果结构异常，并尽量返回具体原因。

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
→ 保存 purpose、sql_template 和 active 状态
→ 保存 SQL 使用的表和字段资产快照
→ revision 从 1 开始

经验已经存在
→ 更新 authorization_epoch 和 sql_template
→ 最近最多保留 5 个不同 purpose
→ 替换表和字段资产快照
→ 增加 revision
→ metadata_changed 禁用恢复为 active
→ admin 禁用保持 disabled
→ deleting 状态不再更新经验内容

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
→ 校验 status 仍为 active
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
→ 将 status 设置为 disabled
→ 记录 disabled_reason=metadata_changed
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

## 6. 管理查询经验

查询经验管理入口位于管理员中心，所有后端接口均要求平台管理员身份。列表和详情读取 PostgreSQL 事实数据，因此可以查看有效、禁用、删除中和索引待同步记录。

```text
管理员打开查询经验页面
→ 按 Doris 角色、状态或关键词筛选
→ 查看 purpose、SQL 模板、元数据资产和来源执行记录
→ 不展示 authorization_epoch、meta_version 和内部 revision

管理员禁用 active 或 metadata_changed 经验
→ 按经验 ID 获取行锁
→ status 设置为 disabled
→ disabled_reason 设置为 admin
→ 记录管理员和禁用时间
→ revision 增加 1
→ 索引任务删除 Elasticsearch 文档
→ 后续成功执行更新内容但保持管理员禁用

管理员直接删除 active 或 disabled 经验
→ 按经验 ID 获取行锁
→ status 设置为 deleting
→ 记录管理员和删除请求时间
→ revision 增加 1
→ 经验立即停止参与召回
→ 索引任务按 revision 删除 Elasticsearch 文档
→ 索引删除成功后删除 PostgreSQL 经验和资产
→ QueryExecution 继续保留并将 experience_id 置空

索引任务提交或执行失败
→ PostgreSQL 保留 pending 或 deleting 状态
→ 周期 repair 任务根据 revision 差异重新提交
```

管理页面不提供手工创建、编辑 SQL、直接启用和手工重新提交索引功能。

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
→ app/query/services/contracts.py
→ app/query/services/experience.py
→ app/query/services/experience_invalidation.py
→ app/query/services/experience_management.py
→ app/query/api/admin
→ app/query/tasks.py
→ app/assistant/agents/explorer/tools/execute_sql.py
```
