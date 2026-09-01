# `app/metadata` 模块待修复问题

本文只记录已经通过实现和调用链确认、值得修改的问题。调整时从 Repository 和基础设施边界开始，并同步更新 Service、任务调用方与逻辑测试。

## 1. 字段与指标语义索引 Repository 重复实现 Elasticsearch 原语

[`ColumnESRepo`](../app/metadata/repositories/column_index.py) 和 [`MetricESRepo`](../app/metadata/repositories/metric_index.py) 重复实现了以下技术逻辑：

- 创建索引及更新 mapping。
- 按资源读取差量文档。
- 应用索引差量。
- 按过滤条件删除文档。
- 向量查询及候选数量计算。
- 全文查询及精确匹配权重。
- Elasticsearch 响应解包。

两者真正的业务差异包括索引名称、字段 mapping、授权过滤键和命中实体类型。当前重复实现容易导致字段索引与指标索引的查询策略、mapping 升级和异常处理逐渐偏离。

### 建议调整

扩展 [`semantic_index.py`](../app/metadata/repositories/semantic_index.py) 中的公共技术实现，集中处理索引创建、删除、全文查询和向量查询。继续保留 `ColumnESRepo` 与 `MetricESRepo` 作为业务入口，由它们负责：

- 提供各自的索引名称和额外 mapping。
- 将授权资源转换为 Elasticsearch filter。
- 将 payload 转换为 `ColumnInfo` 或 `MetricInfo`。
- 暴露字段和指标各自准确的方法签名。

公共实现应围绕稳定的 Elasticsearch 操作抽取，避免引入依赖多个回调、工厂或配置对象的框架式泛型 Repository。

## 2. 元数据 Celery 任务提交逻辑重复

[`task_scheduler.py`](../app/metadata/task_scheduler.py) 和 [`tasks.py`](../app/metadata/tasks.py) 分别实现了相同的 `celery_app.send_task` 调用，并重复维护以下协议参数：

- Queue：`metadata-index`。
- Routing key：`metadata-index`。
- 任务名称。
- `TaskSubmission` 构造。

修改队列或提交策略时需要同步维护两处实现。

### 建议调整

新增不依赖 `providers.py` 和 Celery 任务定义的轻量提交模块，统一保存任务名称、路由参数和 `send_task` 调用。`CeleryMetadataSemanticIndexScheduler` 与 API 使用的 enqueue 函数共同调用该模块。

不能让 `task_scheduler.py` 直接导入 `tasks.py`：`tasks.py` 当前导入 `providers.py`，而 `providers.py` 又导入 Scheduler，这会形成循环依赖。

日志应由提交入口记录一次，并包含任务 ID、资源数量和有限长度的资源摘要。

## 3. 字段联合键与字段引用之间的转换散落

当前同一字段身份在不同边界使用两种合理表达：

- `ColumnKey = tuple[str, str]`：用于集合、排序、字典键和 Service 方法参数。
- `ColumnReference = {"t_name": str, "c_name": str}`：用于 ORM 投影、JSON payload、配置和 API Schema。

这两种表达承担不同职责，应继续保留。问题在于转换逻辑散落在 [`catalog.py`](../app/metadata/services/catalog.py)、[`import_service.py`](../app/metadata/services/import_service.py)、[`search.py`](../app/metadata/services/search.py)、[`recall.py`](../app/metadata/services/recall.py) 和 [`authorization_filter.py`](../app/metadata/services/authorization_filter.py) 中，多次手写：

```python
(reference["t_name"], reference["c_name"])
```

以及：

```python
{"t_name": t_name, "c_name": c_name}
```

### 建议调整

在 metadata 领域模型附近提供两个明确的纯转换函数：

```python
def column_reference_key(reference: ColumnReference) -> ColumnKey: ...

def column_key_reference(key: ColumnKey) -> ColumnReference: ...
```

所有调用方统一使用这两个函数。Pydantic 配置模型、API Schema、`ColumnKey` 和 ORM 使用的 `ColumnReference` 继续保持各自边界，不通过继承、别名或兼容转换合并。

## 4. 字段取值索引同步在长时间外部 I/O 期间持有 PostgreSQL 事务锁

[`MetaIndexService._sync_column_value_index`](../app/metadata/services/index.py) 的注释说明长时间 Doris/Elasticsearch I/O 不应占用 PostgreSQL 事务。当前第二段 `session.begin()` 在取得事务级 advisory lock 后调用：

- `_run_full_value_sync()`；或
- `_run_incremental_value_sync()`。

这两个方法会读取 Doris 数据、分批写入 Elasticsearch、刷新索引并清理旧 generation。整个过程仍位于 PostgreSQL 事务内，因此会长期占用数据库连接、事务和 `pg_advisory_xact_lock`。实际事务边界与注释声明的约束不一致。

### 建议调整

将同步状态机明确拆成三个阶段：

1. 在短 PostgreSQL 事务中取得 advisory lock，校验配置，登记 `run_id`、generation 和同步参数，然后提交事务。
2. 在 PostgreSQL 事务外执行 Doris 读取与 Elasticsearch 写入。
3. 在新的短事务中重新取得 advisory lock，通过 `run_id` 和配置版本确认运行所有权，再提交水位和成功状态。

异常补偿继续使用独立短事务，通过 `run_id` 条件更新失败状态，防止旧任务覆盖新的同步运行。配置在外部 I/O 期间发生变化时，终态提交必须拒绝旧结果。

该调整需要覆盖以下逻辑测试：

- 全量同步成功并切换 generation。
- 增量同步成功并推进水位。
- 同步期间配置变化导致旧运行无法提交。
- 新运行接管后旧运行失败不会覆盖新状态。
- Doris 或 Elasticsearch 失败时记录当前运行的失败状态。

## 处理顺序

1. 修正字段取值索引事务边界。
2. 提取 Elasticsearch 公共技术原语。
3. 统一 Celery 任务提交。
4. 集中字段引用转换。

每项修改都应保持现有权限过滤、跨模块业务端口和 Catalog/Import 服务边界，不增加兼容入口或无实际用途的抽象层。
