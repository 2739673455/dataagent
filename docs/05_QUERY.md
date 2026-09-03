# 05. Query 模块职责与实现

`query` 负责安全执行分析 SQL、保存查询结果和执行历史，并把成功 SQL 沉淀为可召回的角色级查询经验。

## 模块职责与边界

`query` 位于 Explorer 与 Doris 之间，统一完成查询身份解析、SQL 静态校验、资产权限校验、受限执行、结果落盘、执行审计和查询经验生命周期。所有分析 SQL 都通过同一条用例链进入 Doris。

Explorer 通过 `execute_sql` 使用查询能力；`metadata` 在召回上下文中使用查询经验，并在资产变化时通知经验失效；管理员通过查询经验管理接口查看、禁用和删除经验。`identity` 提供查询凭据与资产策略，`metadata` 提供目录事实，`sandbox` 保存完整查询结果。

该模块不生成业务问题的最终回答，也不维护业务元数据和真实授权。SQL 生成由 Explorer 完成，目录与权限分别由 `metadata` 和 `identity` 管理，Doris 保留最终执行边界。

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

**实现目的**

确保模型生成的 SQL 只能使用当前用户有权访问的目录和只读语法，并在明确的资源限制内执行，最终提供可供后续分析复用的完整数据文件。

**使用者与使用方式**

- Explorer 调用 `execute_sql`，提交 SQL 和本次查询目的 `purpose`。
- Analyst 和 Reviewer 不直接连接 Doris，通过 Explorer 生成的 CSV 使用数据。
- 调用方从工具响应读取文件路径、字段、行数、时间范围和样例。
- SQL 被拒绝或执行失败时，Explorer 根据结构化错误修正 SQL 或调整查询策略。

**具体实现**

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
  → 数据发现阶段允许受限 SHOW TABLES
  → 数据发现阶段允许限定当前数据库的 information_schema.tables 和 information_schema.columns
  → 拒绝 DDL、DML、命令和不安全函数
  → 从 metadata 加载表和字段目录
  → 解析 CTE、子查询、表别名和字段限定
  → 部分字段授权的表拒绝不安全星号查询
  → 禁止 CROSS JOIN
  → 要求普通 JOIN 提供 ON 或 USING
  → 要求 ON 同时关联当前右表和前置数据源
  → AND 至少包含一个跨来源比较，OR/XOR 每个分支都包含跨来源比较
  → 校验重复输出列名
  → 校验实际读取的每张表和每个字段
→ 生成 normalized_sql 和结构化校验结果
→ 关闭 metadata PostgreSQL Session
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
→ 将 purpose 规范化为可读文件名并追加短唯一后缀
→ 原子保存到当前 Explorer Session
→ 返回 path、columns、row_count、time_range 和 sample
```

查询受 Doris 查询超时和内存限制。完整结果只保存在沙箱 CSV 中，工具响应只返回路径和有限摘要。

执行失败时，工具会区分 SQL 校验拒绝、无权限、查询超时、Doris 故障和结果结构异常，并尽量返回具体原因。


### 设计细节：Guard 按“纯语法 → 授权目录 → 规范化 → 血缘”分阶段执行

Guard 先用 Doris 方言解析单条语句，再执行不需要访问数据库的只读语法检查。禁止语句在这个阶段直接结束，避免恶意 SQL 触发额外目录访问。只有语法安全后才加载按当前用户权限收窄的元数据目录，并让 sqlglot 补全字段、展开星号和解析 CTE。

```python
expression, issues = self._parse_single_query(sql)
if expression is None:
    return self._result(None, issues)

if isinstance(expression, exp.Show):
    return self._check_show_tables(expression)
if self._references_information_schema(expression):
    return self._check_information_schema_query(expression)

issues.extend(self._check_readonly(expression))
if issues:
    return self._result(None, issues)

catalog = await self._load_catalog(policy)
raw_tables, star_tables, table_issues = self._resolve_tables(
    expression,
    catalog,
)
issues.extend(table_issues)
issues.extend(self._check_restricted_stars(catalog, raw_tables, star_tables))
if issues:
    return self._result(None, issues, tables=raw_tables)

qualified = self._qualify(expression, catalog)
columns = self._collect_physical_columns(qualified, catalog)
issues.extend(self._check_joins(qualified))
```

执行器只接收 `validation.normalized_sql`，不会执行用户原始 SQL。`QueryValidationResult` 通过模型校验保证 `valid`、`issues` 和 `normalized_sql` 三者状态一致。

目录发现有两个受控例外：只允许 `SHOW TABLES`；`information_schema` 只允许单层查询 `tables` 或 `columns`，且 WHERE 必须以 AND 分支明确限定 `table_schema = DATABASE()` 或配置数据库名。它们标记为 `query_kind="catalog"`，成功执行后只记历史，不沉淀查询经验。


### 设计细节：JOIN 防护检查每次连接是否真正关联左右数据源

笛卡尔积防护位于 `_check_joins()`。显式 `CROSS JOIN` 直接拒绝；普通 JOIN 必须有 `ON` 或 `USING`。对于 `ON`，每处理一张右表都会维护此前已经形成的左侧别名集合，连接条件必须同时引用当前右表和任一前置数据源。

```python
for join in joins:
    # 每处理一个 JOIN 就把右表加入左侧集合，后续连接必须关联已经形成的
    # 数据源集合，不能只引用自身或无关别名。
    right_alias = join.this.alias_or_name.casefold()
    kind = str(join.args.get("kind") or "").casefold()
    on = join.args.get("on")
    using = join.args.get("using") or []
    if kind == "cross":
        issues.append(
            QueryValidationIssue(
                code="cross_join_forbidden",
                message=f"不允许使用笛卡尔积 CROSS JOIN: {right_alias}",
                table=right_alias,
            )
        )
        left_aliases.add(right_alias)
        continue
    if on is None and not using:
        issues.append(
            QueryValidationIssue(
                code="join_condition_required",
                message=f"JOIN 连接必须提供 ON 或 USING 关联条件: {right_alias}",
                table=right_alias,
            )
        )
        left_aliases.add(right_alias)
        continue
    if on is not None and (
        not cls._join_condition_links_sources(
            on,
            left_aliases,
            right_alias,
        )
    ):
        issues.append(
            QueryValidationIssue(
                code="invalid_join_condition",
                message=(
                    "JOIN 条件必须同时关联当前连接源与前置数据源: "
                    f"{right_alias}"
                ),
                table=right_alias,
            )
        )
    left_aliases.add(right_alias)
```

布尔条件的判定有意区分 AND 与 OR/XOR。AND 中存在一个跨来源比较即可，其他项可以是过滤条件；OR 或 XOR 的每个分支都必须关联左右来源，否则某一分支为真时仍可能产生笛卡尔积。

```python
if isinstance(condition, (exp.Or, exp.Xor)):
    return all(
        cls._join_condition_links_sources(child, left_aliases, right_alias)
        for child in (condition.this, condition.expression)
    )
if isinstance(condition, exp.And):
    return any(
        cls._join_condition_links_sources(child, left_aliases, right_alias)
        for child in (condition.this, condition.expression)
    )
```

原子比较只有在一侧只引用前置别名、另一侧只引用当前右表时才成立。`ON 1=1`、`ON right.id=right.parent_id`、只引用两个旧表的条件都会失败。该规则针对静态可证明的连接关系，不能估算业务数据是否唯一或连接后实际行数。


### 设计细节：字段授权在星号展开前后各承担一层检查

加载目录时先按 `AssetAccessPolicy` 去掉无权字段。若一张表只授权部分字段，该表会加入 `restricted_star_tables`。Guard 在 sqlglot 展开 `*` 前记录星号涉及的物理表，并明确拒绝对受限表使用星号：

```python
if policy is not None:
    allowed_column_keys = authorization_filter.allowed_column_keys(column_infos)
    restricted_star_tables = frozenset(
        table_name.casefold()
        for table_name in visible_table_names
        if any(
            column.t_name == table_name
            and (column.t_name, column.name) not in allowed_column_keys
            for column in column_infos
        )
    )
```

随后 `_collect_physical_columns()` 从已限定表达式收集真实字段，`_check_asset_policy()` 再按表和列逐项确认。Doris 使用角色专属 `query_user` 执行，构成最终权限边界。应用侧检查用于提前拒绝、保护查询经验血缘并给出具体错误，数据库侧权限用于抵御绕过应用逻辑的访问。


### 设计细节：查询结果按批次写临时文件，内存只保存固定摘要

执行 Repository 根据配置设置超时、内存和 Workload Group，并以批次返回行。Service 首批确定列结构，后续批次必须保持同名同序；完整结果持续写入临时文件，内存只累计字段类型、空值标记、时间范围、行数和少量样例。

```python
async for batch in batches:
    if column_names is None:
        column_names = batch.column_names
        self._validate_column_names(column_names)
        column_stats = [_ColumnStats() for _ in column_names]
        writer.writerow(_csv_value(name) for name in column_names)
    elif batch.column_names != column_names:
        raise QueryResultShapeError("流式查询各批次返回的列结构不一致")

    for row in batch.rows:
        for stats, value in zip(column_stats, row, strict=True):
            stats.observe(value)
        writer.writerow(_csv_value(value) for value in row)
        if len(sample) < self._options.sample_rows:
            sample.append(
                {
                    name: _summary_value(value)
                    for name, value in zip(column_names, row, strict=True)
                }
            )
        row_count += 1
```

写完后临时文件从头流入 Sandbox，避免把大结果整体放入 Python 内存。输出文件名由 purpose 规范化并加随机后缀，不会覆盖同 Session 的旧查询结果。

字符串写入 CSV 前还会防止公式注入。忽略前导空白和控制字符后，如果第一个有效字符是 `= + - @`，就在原字符串前加单引号：

```python
def _escape_csv_formula(value: str) -> str:
    for character in value:
        if character.isspace() or unicodedata.category(character).startswith("C"):
            continue
        return f"'{value}" if character in "=+-@" else value
    return value
```

## 2. 记录查询执行历史

**实现目的**

为每次 SQL 调用保存可审计事实，使管理员和开发人员能够追溯查询来源、授权环境、校验结果、输出摘要和失败原因，并为查询经验提供可信来源。

**使用者与使用方式**

- 查询执行链自动记录成功、拒绝和失败，无需 Explorer 额外调用。
- 查询经验详情接口向管理员展示关联的来源执行记录。
- 排障人员可以按 Conversation、Analysis、Session 和 Tool Call 追踪具体执行。

**具体实现**

```text
每次 execute_sql 开始
→ 准备 QueryExecutionContext
  → user_id、role_name、authorization_epoch
  → conversation_id、analysis_id、session_id、tool_call_id
  → purpose 和 raw_sql

SQL 在 Guard 阶段被拒绝
→ 保存 status=rejected
→ 保存 error_code、error_detail 和 validation

SQL 在执行、结果处理或文件写入阶段失败
→ 保存 status=failed
→ 保存已得到的 normalized_sql 和 validation
→ 保存具体错误

SQL 成功并提交 CSV
→ 保存 status=succeeded
→ 保存 normalized_sql、validation 和 result_summary
→ 保存 SQL 模板和 fingerprint
→ 关联生成或更新的 QueryExperience
```

执行历史写入失败只记录日志，不会把成功查询改成失败，也不会覆盖原始查询错误。

### 设计细节：三个执行分支共享同一份调用上下文

`QueryExecutionContext` 由 SQL 工具在进入执行链时构造。成功、Guard 拒绝和运行失败都通过 `_new_execution()` 固化相同的用户、授权和 Agent 调用标识：

```python
@staticmethod
def _new_execution(
    context: QueryExecutionContext,
    raw_sql: str,
    status: QueryExecutionStatus,
) -> QueryExecution:
    return QueryExecution(
        user_id=context.session_key.user_id,
        role_name=context.role_name,
        authorization_epoch=context.authorization_epoch,
        conversation_id=context.session_key.conversation_id,
        analysis_id=context.session_key.analysis_id,
        session_id=context.session_key.session_id,
        tool_call_id=context.tool_call_id,
        purpose=context.purpose,
        raw_sql=raw_sql,
        status=status,
    )
```

失败记录保留当时已经完成的校验信息，并限制外部错误详情长度，防止不可控异常撑大审计行：

```python
execution = self._new_execution(context, raw_sql, status)
execution.error_code = error_code
execution.error_detail = error_detail[:4000]
if validation is not None:
    execution.normalized_sql = validation.normalized_sql
    execution.validation = validation.model_dump(mode="json")
async with self._experience_repo.session.begin():
    await self._execution_repo.record(execution)
```

成功记录只保存结果路径、字段、行数和时间范围，不把完整结果再次写入 PostgreSQL。业务数据留在用户 Sandbox CSV 中，审计表保存足以定位和解释本次执行的摘要。

## 3. 沉淀查询经验

**实现目的**

把经过权限检查并成功执行的 SQL 转换为可复用模板，让后续相似问题能够参考已验证的表关系、字段用法和查询结构。

**使用者与使用方式**

- 成功查询自动创建或更新经验，Agent 无需手工保存。
- 同一 Doris 角色的 Explorer 可以在后续 `recall_context` 中召回经验。
- 管理员可以禁用质量不佳或不再适用的经验。

**具体实现**

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


### 设计细节：历史记录是每次执行事实，经验按角色和 SQL 结构聚合

所有已进入角色执行上下文的成功、拒绝和失败都会写 `QueryExecution`。成功业务查询先把 SQL 字面量替换为占位符，再对模板计算 SHA-256 指纹：

```python
def _build_sql_template(sql: str) -> tuple[str, str]:
    expression = parse_one(sql, read="doris")
    parameter_index = 0
    for node in list(expression.walk()):
        if not isinstance(node, exp.Literal):
            continue
        parameter_index += 1
        node.replace(exp.Placeholder(this=f"p{parameter_index}"))
    template = expression.sql(dialect="doris", pretty=False)
    fingerprint = hashlib.sha256(template.encode()).hexdigest()
    return template, fingerprint
```

数据库唯一约束 `(role_name, fingerprint)` 保证同一角色的相同 SQL 结构只有一条经验。每次成功执行更新最近五个 purpose、SQL 模板、授权代次和资产版本快照，并增加 revision。管理员禁用的经验保持禁用；因元数据变化自动禁用的经验在相同结构重新成功执行后可以恢复 active。

查询历史写入属于旁路审计：记录失败不会覆盖原始 SQL 执行结果或异常。执行 Handler 对成功与失败记录都使用安全包装并单独写日志。

## 4. 检索查询经验

查询经验没有独立 Agent 工具，由 `recall_context` 使用 query 内置检索，最多返回 3 条。

**实现目的**

按业务问题语义找回当前角色、当前授权代次和当前元数据版本仍然有效的 SQL 模板，避免旧权限或旧目录产生的经验进入模型上下文。

**使用者与使用方式**

- Explorer 调用 `recall_context` 时自动检索，无需单独选择工具。
- `metadata` 将有效经验保存到 query 上下文并投影给模型。
- 单个检索通道失败时继续使用另一通道，全部失败时只放弃经验结果。

**具体实现**

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


### 设计细节：经验召回在索引前置过滤后仍回查事实和版本

全文和向量检索都限定 `role_name + authorization_epoch`，两路可独立失败并使用 RRF 融合。ES 返回候选 ID 后，Service 回到 PostgreSQL 读取当前 active 聚合，比较每个资产保存的 `meta_version` 与当前版本，并再次应用资产策略：

```python
current_versions = await self._repo.current_asset_versions(experiences)
stale_ids = {
    experience.id
    for experience in experiences
    if experience.status == "active"
    and any(
        current_versions.get(asset.resource_key) != asset.meta_version
        for asset in experience.assets
    )
}
invalid_revisions.update(
    await self._repo.disable_for_metadata_change(stale_ids)
)
```

过期经验在同一事务中改为 `disabled/metadata_changed` 并安排删除索引。最终结果还要求所有表和字段都被当前 `MetadataAuthorizationFilter` 允许。这样旧 ES 文档、延迟索引任务或刚发生的权限变化都不能直接把旧 SQL 模板暴露给当前用户。

## 5. 失效和修复查询经验

**实现目的**

让元数据、资产权限和索引状态变化后，旧经验能够立即停止召回，并让任务丢失或临时故障产生的索引差异最终收敛。

**使用者与使用方式**

- `metadata` 在表或字段变化时按稳定资源键触发失效。
- `identity` 通过轮换 `authorization_epoch` 隔离权限变化前的经验。
- 检索服务在读取候选时再次校验资产版本和权限。
- Celery Beat 周期提交索引修复任务，运维人员无需逐条补偿。

**具体实现**

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


### 设计细节：Elasticsearch 是按 revision 收敛的可重建投影

索引任务收到的 revision 只表示“至少有这个版本需要处理”。任务重新读取 PostgreSQL 当前事实，并以当前 revision 决定写入、删除或最终物理删除：

```python
async def sync(self, experience_id: UUID, requested_revision: int) -> int:
    async with self._repo.session.begin():
        experience = await self._repo.get(experience_id)
    if experience is None:
        await self._index_repo.delete(
            experience_id,
            revision=requested_revision,
        )
        return requested_revision
    if experience.indexed_revision >= experience.revision:
        return experience.indexed_revision

    revision = experience.revision
    if experience.status == "deleting":
        await self._index_repo.delete(experience.id, revision=revision)
        async with self._repo.session.begin():
            await self._repo.finalize_deletion(experience.id, revision)
        return revision
```

ES Repository 也使用 revision 防止旧任务覆盖新文档。active 经验写文本和向量，disabled 经验删除索引文档，deleting 经验先确认索引删除成功再删除 PostgreSQL 聚合；来源 `QueryExecution` 继续保留，外键通过 `SET NULL` 解除关联。周期修复任务比较 `revision` 与 `indexed_revision`，重提任何未收敛记录。

## 6. 管理查询经验

查询经验管理入口位于管理员中心，所有后端接口均要求平台管理员身份。列表和详情读取 PostgreSQL 事实数据，因此可以查看有效、禁用、删除中和索引待同步记录。
每条经验保留最近 5 个去重查询目的。列表搜索会对这些查询目的逐项解码并执行不区分大小写的子串匹配，同时搜索 SQL 模板和指纹。管理列表只展示最新查询目的。
管理列表默认隐藏已提交删除的墓碑记录；需要检查后台清理状态时，可以主动筛选“删除中”。

**实现目的**

为自动沉淀的查询经验提供人工治理入口，使管理员可以审计来源、停止召回错误经验，并以可恢复方式删除经验及其检索投影。

**使用者与使用方式**

- 管理员按 Doris 角色、状态和关键词查询经验列表。
- 管理员查看 SQL 模板、资产和来源执行记录。
- 管理员可以单条或批量禁用、删除经验。
- 管理接口不提供手工创建、修改 SQL 或直接重新启用。

**具体实现**

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

### 设计细节：管理操作先提交事实状态，再驱动索引收敛

管理员禁用经验时，仓储对目标行加锁并幂等更新状态；事务提交后才按新的 `revision` 投递索引任务：

```python
async with self._repo.session.begin():
    experience, changed = await self._repo.disable_manually(
        experience_id,
        operator_id,
    )
    if experience is None:
        raise query_error.QueryExperienceNotFoundError
    if experience.status == "deleting":
        raise query_error.QueryExperienceStateConflictError(
            detail="删除中的查询经验不能禁用"
        )
    revision = experience.revision
if changed:
    self._index_scheduler.enqueue(experience_id, revision)
```

删除请求采用相同模式，但先把 PostgreSQL 状态写成 `deleting`，让经验立即退出召回：

```python
async with self._repo.session.begin():
    experience, changed = await self._repo.request_deletion(
        experience_id,
        operator_id,
    )
    if experience is None:
        raise query_error.QueryExperienceNotFoundError
    revision = experience.revision
    requested_at = experience.deletion_requested_at
if changed:
    self._index_scheduler.enqueue(experience_id, revision)
```

任务先按 `revision` 删除 Elasticsearch 文档，成功后再物理删除 PostgreSQL 聚合。重复禁用或删除返回当前状态且不重复投递；批量操作先在一个事务中收集所有新 revision，再逐个发布任务，避免消费者读取到未提交状态。

## 数据与任务

```text
元数据 PostgreSQL
→ QueryExecution
→ QueryExperience
→ QueryExperienceAsset

Elasticsearch
→ 查询经验语义文档

Doris
→ 受限只读 SQL

Sandbox
→ <规范化 purpose>_<短唯一后缀>.csv

Celery
→ dataagent.query.sync_index
→ dataagent.query.repair_indexes
→ 路由 metadata-index 队列
```
