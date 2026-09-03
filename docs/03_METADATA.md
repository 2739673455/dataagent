# 03. Metadata 模块职责与实现

`metadata` 负责维护分析使用的表、字段和指标目录，并将目录构建为可检索索引和持续召回上下文。

## 模块职责与边界

`metadata` 将 Doris 的物理表结构补充为模型能够理解的业务目录，并负责把目录同步到 Elasticsearch、执行语义检索、补全表关系以及维护 Conversation 内的 query 上下文。

平台管理员通过 HTTP 接口维护和索引目录；Explorer 通过召回工具检索业务资源并持续整理查询上下文；`query` 使用当前目录校验 SQL 中的表、字段和类型。PostgreSQL 保存目录事实与召回快照，Elasticsearch 保存可以重建的搜索投影，Doris 提供物理目录和字段取值。

该模块不负责生成或执行 SQL，也不决定用户身份。SQL 由 `query` 处理，当前资产可见范围由 `identity` 提供的 `AssetAccessPolicy` 决定。

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
→ 提供模型可见的授权上下文
```

## 1. 查看和导出元数据目录

**实现目的**

让管理员检查系统当前掌握的业务语义，并获得可审阅、可纳入版本管理和可再次导入的完整目录文件。

**使用者与使用方式**

- 管理员通过 `/api/v1/meta/tables`、`columns` 和 `metrics` 查看业务目录。
- 管理员通过 `/api/v1/meta/source-tables` 对照 Doris 物理表。
- 管理员通过 `/api/v1/meta/export` 下载 UTF-8 YAML。

**具体实现**

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

### 设计细节：导出文件由当前事实重新投影

导出服务一次读取完整目录，按表归组字段，再构造与导入模型相同的 `MetaConfig`：

```python
table_infos = await self._meta_repo.list_table_infos()
column_infos = await self._meta_repo.list_column_infos()
metric_infos = await self._meta_repo.list_metric_infos()

columns_by_table: dict[str, list[ColumnInfo]] = {
    table_info.name: [] for table_info in table_infos
}
for column_info in column_infos:
    columns_by_table[column_info.t_name].append(column_info)

return MetaConfig(
    tables=[
        TableConfig(
            name=table_info.name,
            role=cast(TableRole, table_info.role),
            description=table_info.description,
            columns=[
                ColumnConfig(
                    name=column.name,
                    description=column.description,
                    alias=column.alias,
                    index_values=column.index_values,
                )
                for column in sorted(
                    columns_by_table[table_info.name],
                    key=lambda item: item.name,
                )
            ],
        )
        for table_info in table_infos
    ],
    metrics=[
        MetricConfig(
            name=metric_info.name,
            description=metric_info.description,
            relevant_columns=[
                ColumnReferenceConfig(**reference)
                for reference in metric_info.relevant_columns
            ],
            alias=metric_info.alias,
        )
        for metric_info in metric_infos
    ],
)
```

导出不读取 Elasticsearch，也不复制数据库内部版本和索引状态。字段按稳定业务键排序，使相同事实生成稳定 YAML，便于代码审查；导出的模型与导入模型相同，因此文件可以直接用于 `merge` 或 `replace`。

## 2. 维护表、字段和指标

**实现目的**

把物理表结构、业务名称、指标口径、字段关系和可检索范围维护为稳定事实，并在语义变化后及时更新索引与查询经验。

**使用者与使用方式**

- 管理员通过 `/api/v1/meta` 下的表、字段和指标接口新增或修改单个资源。
- 管理员通过批量删除接口移除资源；存在外键或指标依赖时，需要先调整依赖项。
- `query` 读取表和字段目录执行 SQL Guard。
- Explorer 通过语义召回消费这些目录信息。

**具体实现**

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


### 设计细节：meta_version 表示事实变化，index_version 表示投影进度

表、字段和指标都通过 `metadata_snapshot()` 比较真正影响语义的内容。只有快照变化时才推进 `meta_version`；重复提交相同内容不会制造新版本。字段和指标保留旧 `index_version`，因此调用方可以直接判断索引是 `current`、`stale` 还是 `missing`。

```python
@staticmethod
def _set_versions(
    item: TableInfo | ColumnInfo | MetricInfo,
    existing: TableInfo | ColumnInfo | MetricInfo | None,
    changed: bool,
) -> None:
    item.meta_version = (
        1 if existing is None else existing.meta_version + int(changed)
    )
    if isinstance(item, TableInfo):
        return
    if existing is None:
        item.index_version = 0
    elif isinstance(existing, (ColumnInfo, MetricInfo)):
        item.index_version = existing.index_version
```

索引任务完成后使用条件更新确认版本。任务读取版本 5 后，如果管理员已把元数据更新到版本 6，版本 5 的任务可以完成 ES 写入，但不能把数据库标成已同步：

```python
result = await self._session.execute(
    update(ColumnInfo)
    .where(
        ColumnInfo.t_name == t_name,
        ColumnInfo.name == c_name,
        ColumnInfo.meta_version == target_version,
    )
    .values(index_version=target_version)
    .returning(ColumnInfo.name)
)
return result.scalar_one_or_none() is not None
```

后续任务会以版本 6 再次生成投影。这个条件发布机制阻止完成较晚的旧任务覆盖新事实的同步状态。

## 3. 批量导入元数据

**实现目的**

支持使用一份声明式 YAML 批量建立或更新业务目录，并在正式写入前预览影响范围，降低大批量人工维护产生的不一致。

**使用者与使用方式**

- 管理员上传符合 `MetaConfig` 的 UTF-8 YAML。
- `merge` 用于增量合并，`replace` 用于让数据库目录与文件完全一致。
- `dry_run=true` 用于预览，确认后再提交正式异步导入。
- 开发人员通过生成脚本维护项目自带的基线目录。

**具体实现**

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

### 设计细节：导入把预检、事实提交和派生任务分成三个阶段

服务先用短事务取得 PostgreSQL 一致快照，随后在事务外访问 Doris 并构造目标目录，避免远程调用长期占用数据库事务：

```python
async with self._meta_repo.session.begin():
    existing_tables = {
        item.name: item for item in await self._meta_repo.list_table_infos()
    }
    existing_columns = {
        (item.t_name, item.name): item
        for item in await self._meta_repo.list_column_infos()
    }
    existing_metrics = {
        item.name: item for item in await self._meta_repo.list_metric_infos()
    }

table_infos, column_infos, metric_infos = await self._build_metadata(meta_config)
table_changes = self._get_changes(
    self._table_snapshots(existing_tables),
    self._table_snapshots(self._index_tables(table_infos)),
    mode,
)
result = MetaImportResult(
    mode=mode,
    dry_run=dry_run,
    tables=table_changes,
    columns=column_changes,
    metrics=metric_changes,
)
if dry_run:
    return result
```

正式执行时，`replace` 先删除已失去同步入口的 ES 文档，再在单个 PostgreSQL 事务中删除旧事实并 upsert 新事实。事务提交后才失效查询经验并投递语义索引任务：

```python
async with self._meta_repo.session.begin():
    if mode is ImportMode.REPLACE:
        await self._meta_repo.delete_metric_infos(metric_changes.deleted)
        await self._meta_repo.delete_column_infos(column_changes.deleted)
        await self._meta_repo.delete_table_infos(table_changes.deleted)
    await self._meta_repo.upsert_column_infos(
        changed_columns,
        force_version_increment_keys=set(column_changes.updated),
    )

await self._asset_invalidator.invalidate_assets(
    table_names=set(table_changes.updated + table_changes.deleted),
    column_keys=set(column_changes.updated + column_changes.deleted),
)
self._semantic_index_scheduler.enqueue_columns(changed_column_keys)
```

`dry_run` 与正式导入共用同一套解析、Doris 实态校验和差量算法。派生动作发生在事实提交之后，任务读取时一定能看到新的 `meta_version`；即使任务发布失败，事实仍然明确，管理员可以重新提交同步。

## 4. 同步字段和指标语义索引

**实现目的**

把名称、描述和别名转成同时支持全文与向量检索的文档，并通过版本和差量同步减少 Embedding 调用与 Elasticsearch 写入。

**使用者与使用方式**

- 管理员可以按表、字段或指标手动提交同步任务。
- 目录新增、修改和导入流程会自动调度相关索引。
- Explorer 的语义召回透明使用同步后的索引。
- 运维人员通过任务状态和资源索引状态检查同步结果。

**具体实现**

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


### 设计细节：语义索引按文本单元做稳定差量

字段或指标的名称、描述和每个别名分别生成文档。文本先做 NFC 规范化和去重，文档 ID 使用资源类型、资源键和文本计算 UUIDv5，因此同一业务文本在重复同步时保持相同 ID。

```python
for text_value, text_type in source_texts:
    canonical = unicodedata.normalize("NFC", text_value).strip()
    if canonical:
        entries.setdefault(canonical, text_type)

id = str(
    uuid.uuid5(
        uuid.NAMESPACE_URL,
        json.dumps(
            [resource_type, resource_key, text_value],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
)
```

差量计算把“文档载荷变化”和“需要重新生成向量”分开。只有正文或 `embedding_revision` 变化才调用 Embedding；业务 payload、`meta_version` 或 `payload_hash` 变化时复用原向量并更新文档：

```python
needs_embedding = (
    existing.text != target.text
    or existing.embedding_revision != target.embedding_revision
)
changed = needs_embedding or any(
    (
        existing.resource_key != target.resource_key,
        existing.text_type != target.text_type,
        existing.meta_version != target.meta_version,
        existing.payload_hash != target.payload_hash,
    )
)
```

`embedding_revision` 包含嵌入模型、向量维度和预处理版本。模型切换会自然触发重嵌入，无需另建迁移流程。

## 5. 同步字段取值索引

**实现目的**

让 Explorer 能够根据业务实体名称、状态值等实际数据定位字段，同时用 generation 和游标保证大表同步可恢复、旧索引在失败时仍可使用。

**使用者与使用方式**

- 管理员只为适合公开检索的受控字段启用 `index_values`。
- 首次建立、周期校准或源数据大量变化时使用全量同步。
- 配置游标字段的表可以使用增量同步补充新值。
- Explorer 通过字段值全文检索使用这些索引，不直接读取全表 distinct value。

**具体实现**

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


### 设计细节：字段取值同步由 run_id、generation 和元数据快照共同保护

一次取值同步分为三个阶段：短事务登记运行、事务外执行 Doris/ES I/O、短事务提交状态。`active_run_id` 表示当前任务所有权；`generation` 表示对外可见的一整代取值文档。

```python
async def _sync_column_value_index(
    self,
    t_name: str,
    c_name: str,
    *,
    requested_mode: RequestedValueIndexSyncMode,
) -> ValueIndexSyncResult:
    run = await self._begin_value_index_run(
        t_name,
        c_name,
        requested_mode=requested_mode,
    )
    try:
        result = await self._execute_value_index_run(run)
        await self._complete_value_index_run(run, result)
        return result
    except Exception as exc:
        await self._fail_value_index_run(run, exc)
        raise
```

提交时重新读取字段和表，并校验运行所有权、两类元数据版本、游标字段和 `index_values` 开关：

```python
if state is None or state.active_run_id != run.run_id:
    raise RuntimeError("字段取值索引同步运行所有权已失效")
if (
    column_info.meta_version != run.column_meta_version
    or table_info.meta_version != run.table_meta_version
    or table_info.value_index_cursor_column != run.cursor_column
    or column_info.index_values != (run.mode != "clear")
):
    raise RuntimeError("字段取值索引同步配置已变化")
```

全量同步先把新文档写入新 generation，刷新索引后再删除其他 generation。失败时当前 generation 保持不变，召回仍可使用上一份完整索引。增量同步沿用当前 generation，并使用固定上界和重叠回看窗口，避免同步过程中持续写入的数据让水位永远追不完。

## 6. 召回语义资源

**实现目的**

把用户问题中的多个业务词转换为当前用户有权访问的表、字段、指标、字段值和表关系，为 Explorer 生成 SQL 提供最小且完整的上下文。

**使用者与使用方式**

- Explorer 通过 `recall_context` 间接调用召回服务。
- 调用方可以指定多个 `terms`、资源类型和每类结果上限。
- `identity` 提供的资产策略决定候选资源是否可见。
- 单个检索通道故障时，Explorer 仍会收到其他通道的可用结果和局部失败说明。

**具体实现**

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


### 设计细节：召回先按权限收窄目录，再向 Elasticsearch 发查询

召回服务先加载 PostgreSQL 目录，使用当前 `AssetAccessPolicy` 生成允许字段、可见表和允许指标集合，然后把允许资源键传给索引 Repository。这样无权限资源不会先进入候选集再依赖最终展示层过滤。

```python
allowed_column_keys = self._authorization_filter.allowed_column_keys(
    column_infos
)
allowed_columns = {
    (item.t_name, item.name): item
    for item in self._authorization_filter.filter_columns(
        column_infos,
        allowed_column_keys,
    )
}
visible_tables = {
    item.name: item
    for item in self._authorization_filter.filter_tables(
        table_infos,
        allowed_column_keys,
    )
}
```

每个 term、资源类型和全文/向量通道独立执行，索引查询由 Semaphore 限制并发。可用通道通过 RRF 合并名次，融合后再在资源类型内部归一化：

```python
@staticmethod
def _rrf_score(rank: int) -> float:
    return 1 / (_RRF_K + rank)

ordered = sorted(
    scores.items(),
    key=lambda item: (-item[1].score, str(item[0])),
)
max_score = ordered[0][1].score
ranked = [
    (key, round(candidate.score / max_score, 6), candidate.reasons)
    for key, candidate in ordered[:limit]
]
```

稳定业务键作为同分排序项，使相同输入在索引返回顺序略有波动时仍尽量产生确定结果。单路失败只记录 `SemanticRecallFailure`，其余结果以 `partial` 返回。

## 7. 持续构建 query 上下文

**实现目的**

支持 Explorer 围绕同一个分析意图多轮补充搜索词，并将分散召回结果稳定合并，避免每次模型调用都从头检索或丢失已经确认的表关系。

**使用者与使用方式**

- Explorer 为一个稳定分析意图选择 `query` 名称。
- 首次调用创建上下文，后续使用同一 `query` 和新 `terms` 追加资源。
- 查询经验由 `query` 模块检索，并与语义资源共同写入快照。
- Agent 后续模型调用通过轻量引用读取最新授权投影。

**具体实现**

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


### 设计细节：持续上下文采用追加快照和固定锁顺序

同一 query 的追加操作先取得 query 级 advisory lock，再读取最新快照、合并并写一条新快照。创建时间沿用第一版，更新时间标识最新版，旧快照可用于诊断历史变化。

```python
await self._repo.acquire_query_lock(user_id, conversation_id, query)
previous = await self._repo.get_latest_by_query(
    user_id,
    conversation_id,
    query,
)
if previous is not None:
    previous = self._authorize_record(previous)
    response = _merge_semantic_recall_responses(
        response.recall_id,
        [previous.response, response],
        refresh_request=request,
    )
```

合并两个 query 时按 query 文本排序后依次加锁，所有并发调用使用相同锁顺序，避免 `A→B` 与 `B→A` 相互等待。来源 query 的语义资源进入目标，查询经验仍以目标 query 为准，因为经验的检索意图与目标 query 绑定。

## 8. 查询、合并和删除 query 上下文

**实现目的**

让 Explorer 能够检查已经积累的上下文、合并重复分析意图，并删除误召回或已经不需要的资源，控制模型上下文规模和数据相关性。

**使用者与使用方式**

- `list_recalls` 列出当前 Conversation 中已有的 query。
- `get_recall` 读取指定 query 的最新结构。
- `merge_recalls` 将来源 query 的语义资源并入目标 query。
- `delete_recalls` 删除整个上下文或精确删除表、字段、字段值、指标和查询经验。

**具体实现**

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


### 设计细节：任何快照读取都会重新应用当前授权

召回快照保存的是历史结果，不承担永久授权。读取、列出、合并和追加旧快照时都会调用 `_authorize_record()`；查询经验还必须同时匹配当前角色和 `authorization_epoch`。

```python
def _authorize_record(
    self,
    record: SemanticRecallRecord,
) -> SemanticRecallRecord:
    response = self._authorization_filter.filter_recall_response(record.response)
    query_experiences = (
        self._filter_query_experiences(record.query_experiences)
        if self._matches_query_experience_scope(record)
        else []
    )
    return record.model_copy(
        update={
            "response": response,
            "query_experiences": query_experiences,
        }
    )
```

管理员收窄权限后，已有 Conversation 中保存的表、字段、值和查询经验会在下一次读取时被过滤。`SemanticRecallRecord` 内部保留排名、失败、版本和缓存字段；模型投影只保留生成 SQL 需要的目录结构。

## 9. 提供给模型的信息

**实现目的**

只向模型暴露生成 SQL 和解释业务所需的授权信息，隐藏检索评分、内部版本、失败状态和缓存控制字段，降低上下文噪声并保持内部实现可演进。

**使用者与使用方式**

- Explorer 在模型调用前通过中间件展开语义召回引用。
- 模型读取表、字段、值、指标、关系和查询经验。
- 服务端每次展开时重新应用当前权限，因此旧快照不会绕过最新授权。

**具体实现**

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

### 设计细节：模型投影在消费边界重新组装目录

中间件读取已重新授权的 `SemanticRecallRecord` 后，把独立的值命中挂回所属字段，并显式挑选模型可见属性：

```python
values_by_column: dict[tuple[str, str], list[str]] = {}
for item in response.values:
    values_by_column.setdefault((item.t_name, item.c_name), []).append(item.value)

tables = {
    item.name: {
        "role": item.role,
        "description": item.description,
        "primary_key_columns": item.primary_key_columns,
        "columns": {},
    }
    for item in response.tables
}
for item in response.columns:
    table = tables.get(item.t_name)
    if table is None:
        continue
    column = {
        "type": item.type,
        "description": item.description,
        "alias": item.alias,
        "examples": item.examples,
        "reference_t_name": item.reference_t_name,
        "reference_c_name": item.reference_c_name,
    }
    if values := values_by_column.get((item.t_name, item.name)):
        column["values"] = values
    table["columns"][item.name] = column
```

这段投影位于 Assistant 的模型调用边界，因为只有该边界知道模型协议。输入记录及其中的授权目录由 Metadata 定义。白名单式构造保证新增内部字段不会自动进入提示词，孤立字段也不会脱离授权表单独暴露。

## 接口与任务

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
```
