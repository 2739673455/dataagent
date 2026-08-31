# Metadata 模块功能

`metadata` 负责维护分析使用的表、字段和指标目录，并将目录构建为可检索索引和持续召回上下文。

## 功能清单

```text
Metadata
→ 查看和导出元数据目录
→ 维护表、字段和指标
→ 批量导入元数据
→ 同步字段和指标语义索引
→ 同步字段取值索引
→ 召回语义资源
→ 持续构建 query 上下文
→ 查询、合并和删除 query 上下文
```

## 1. 查看和导出元数据目录

```text
管理员查看目录
→ 查询已维护的表
→ 查询某张表的字段
→ 查询全部指标
→ 查询 Doris 当前物理表

管理员导出目录
→ 从 PostgreSQL 读取全部表、字段和指标
→ 转换为统一 MetaConfig
→ 输出 UTF-8 YAML
```

PostgreSQL 目录是元数据事实来源，Elasticsearch 只保存可重建的检索文档。

## 2. 维护表、字段和指标

```text
管理员新增或修改表
→ 校验 Doris 物理表存在
→ 校验主键字段和取值游标字段
→ 内容变化时增加表 meta_version
→ 保存事实表/维表角色和描述
→ 调度表下相关索引
→ 失效受影响的查询经验

管理员新增或修改字段
→ 以 t_name + name 定位字段
→ 校验 Doris 物理字段和类型
→ 校验外键引用目标
→ 保存描述、别名、示例和 index_values
→ 内容变化时增加字段 meta_version
→ 调度字段语义索引
→ 根据 index_values 调度或清理取值索引
→ 失效受影响的查询经验

管理员新增或修改指标
→ 以 name 定位指标
→ 校验 relevant_columns 全部存在
→ 保存描述和别名
→ 内容变化时增加指标 meta_version
→ 调度指标语义索引

管理员批量删除资源
→ 删除字段或表前检查保留字段的外键引用和指标依赖
→ 存在依赖时返回冲突，由管理员先调整依赖资源
→ 删除 PostgreSQL 目录记录
→ 删除对应 Elasticsearch 文档
→ 清理索引状态
→ 表或字段变化时失效使用相关资产的查询经验
```

字段的稳定业务主键是 `(t_name, name)`，指标的稳定业务主键是 `name`。召回合并和版本替换都使用这些主键。

## 3. 批量导入元数据

```text
管理员上传 YAML
→ 按 UTF-8 读取
→ 使用 MetaConfig 校验结构
→ 根据 mode 生成目标目录
  → merge 保留文件外已有资源
  → replace 删除文件外已有资源
→ 校验表、字段、主键、外键和指标依赖
→ 计算 created、updated 和 deleted

dry_run=true
→ 只返回变更预览
→ 不写数据库
→ 不提交索引任务

dry_run=false
→ 提交 Celery 导入任务
→ Worker 应用完整变更
→ 调度需要创建、更新或删除的索引
→ 失效受影响的查询经验
```

开发环境的基线目录 `conf/meta_config.yaml` 由
`scripts/development/generate_meta_config.py` 生成。生成器以 Doris DDL 提供的物理表、字段和原始注释为基础，再应用语义规则：表粒度与角色、字段别名、取值索引范围、外键引用，以及指标口径所需的时间、状态事件和连接字段。生成文件不手工修改。

```bash
# 重新生成基线目录。
uv run python scripts/development/generate_meta_config.py

# 检查已提交文件是否与 DDL 和语义规则一致。
uv run python scripts/development/generate_meta_config.py --check
```

原始搜索词等高基数或可能包含敏感内容的事实字段不进入全局取值索引。状态、类型和业务实体名称等受控字段才开启 `index_values`。

## 4. 同步字段和指标语义索引

```text
字段或指标需要同步
→ 按资源键获取 PostgreSQL advisory lock
→ 读取资源当前 meta_version
→ 将 name、description 和每个 alias 拆成独立语义文档
→ 为每条文本生成稳定文档 ID
→ 读取 Elasticsearch 当前资源文档
→ 计算差量
  → 新文本 create
  → payload 或版本变化 update
  → 已删除文本 delete
  → 完全一致 unchanged
→ 只为新文本或 embedding revision 变化文本生成向量
→ 批量应用 Elasticsearch 差量
→ 再次检查资源 meta_version
→ 版本未变化时提交 index_version
→ 版本已变化时等待下一任务追平
```

`embedding_revision` 包含 Embedding 模型、向量维度和预处理版本。索引模型变化后，差量算法会重新生成需要更新的向量。

## 5. 同步字段取值索引

```text
字段关闭 index_values
→ 删除该字段全部取值文档
→ 删除该字段取值同步状态

字段首次同步或请求 full
→ 生成新的 generation
→ 从 Doris 分批读取全部 distinct value
→ 写入新 generation
→ refresh Elasticsearch
→ 删除该字段其他 generation
→ 提交 current_generation
→ 表配置游标字段时同时提交固定 upper_bound

字段请求 incremental
→ 要求表配置 value_index_cursor_column
→ 要求已有 cursor 和 current_generation
→ 开始时从 Doris 读取固定 upper_bound
→ 以“旧水位 - lookback”作为 lower_bound
→ 读取重叠窗口内变化记录的 distinct value
→ upsert 到当前 generation
→ 成功后提交固定 upper_bound

同步过程中发生异常
→ 校验 run_id 仍拥有本次运行
→ 将状态标记为 failed 并保存具体错误
→ 不切换 current_generation
→ 旧索引继续提供检索
```

增量同步不能识别已经从源表完全消失的值。全量同步负责清除旧 generation，承担周期校准。

## 6. 召回语义资源

```text
调用方提交 terms、resource_types 和 limit_per_type
→ 从 PostgreSQL 读取目录
→ 按当前 AssetAccessPolicy 移除无权限资源
→ 对每个 term 并行检索
  → 字段全文检索
  → 字段向量检索
  → 指标全文检索
  → 指标向量检索
  → 字段值全文检索
→ 记录每个 resource_type、channel 和 term 的独立失败
→ 使用 RRF 融合各通道名次
→ 回到当前目录解析稳定资源主键
→ 补全 SQL 所需上下文
  → 指标 relevant_columns
  → 命中值所属字段
  → 参与表主键
  → 一跳外键两端字段
  → 参与字段所属表
→ 返回资源结果
```

单个 term 或单个检索通道失败时，其余结果继续返回，内部状态为 `partial`。错误范围会精确到失败的资源类型、检索通道和 term。

## 7. 持续构建 query 上下文

```text
Explorer 首次调用 recall_context(query, resource_types, terms, limit)
→ 工具入口规范化 query
→ 执行语义资源召回
→ 使用 query 检索最多 3 条查询经验
→ 保存当前 query 的第一份 SemanticRecallRecord

Explorer 再次使用同一 query 和新的 terms
→ 执行本次语义资源召回
→ 读取该 query 最新记录
→ 按稳定业务主键合并新旧字段、字段值和指标
→ 同一资源保留 meta_version 更大的结果
→ 合并表、主键和外键上下文
→ 保存新的快照版本

查询经验处理
→ query 首次出现时检索
→ 上次成功检索已满 24 小时后重新检索
→ 角色或 authorization_epoch 变化时重新检索
→ 查询经验检索失败时使用空列表
→ 查询经验失败不影响语义资源结果

语义资源召回失败
→ 不写入本次上下文
→ 向 Explorer 返回包含具体原因的错误
```

`query` 是持续上下文的业务键；`recall_id` 只标识某次快照版本。

## 8. 查询、合并和删除 query 上下文

```text
Explorer 调用 list_recalls
→ 列出当前用户、当前会话已有 query

Explorer 调用 get_recall(query)
→ 读取该 query 最新快照
→ 按用户当前权限再次过滤
→ 返回轻量引用

Explorer 调用 merge_recalls(target_query, source_query)
→ 按稳定顺序锁定两个 query
→ 把来源字段、值、指标和表上下文合入目标
→ 保留目标 query 原有查询经验
→ 不合入来源查询经验
→ 将来源 query 记录到 target.source_queries
→ 删除来源 query 全部快照
→ 返回目标 query 最终结构

Explorer 调用 delete_recalls
→ 只传 query 时删除整个上下文
→ tables.{table}={} 时删除整张表
→ tables.{table}.columns.{column}={} 时删除字段
→ column.values=[...] 时只删除指定字段值
→ metrics.{metric}={} 时删除指标
→ query_experiences=[{id: ...}] 时删除指定经验
→ 自动清理失去依赖的指标、主键和外键
→ 返回删除后的最终结构
```

## 9. 提供给模型的信息

```text
内部 SemanticRecallRecord
→ 每次模型调用前重新读取和授权
→ 投影为模型可见结构
  → query
  → tables
    → role、description、primary_key_columns
    → columns
      → type、description、alias、examples、reference
      → values 直接放在所属字段下
  → metrics
    → description、alias、relevant_columns
  → query_experiences
    → id、purpose、sql_template、assets

内部检索信息
→ rank_score、match_reasons
→ index_status、meta_version、index_version
→ failures、warnings、truncated
→ recall_id、source_queries
→ 查询经验缓存时间和授权 scope
→ 不提供给模型
```

## 接口、任务和代码

```text
/api/v1/meta
→ 目录查询和 YAML 导入导出
→ 表、字段和指标维护
→ 语义索引和取值索引任务提交

metadata Celery 任务
→ 表/字段语义索引
→ 表/字段取值索引
→ 指标语义索引
→ YAML 导入
→ 每日到期取值索引调度

代码
→ app/metadata/config.py
→ app/metadata/models
→ app/metadata/api/meta
→ app/metadata/services/catalog.py、import_service.py、index.py
→ app/metadata/services/search.py、recall.py
→ app/metadata/repositories
→ app/metadata/tasks.py
```
