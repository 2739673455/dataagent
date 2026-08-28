# 项目代码审查记录

审查日期：2026-08-28

审查范围：`app/` 全模块，重点覆盖语义召回、查询经验、Agent 运行时、身份认证、元数据索引和沙箱管理。

当前状态：第一项已实施；其余问题仅记录调整建议，尚未实施。

## 一、优先处理的功能问题

### 1. [已处理] 语义召回的部分失败信息会丢失

位置：

- `app/metadata/services/authorization_filter.py:129-186`
- `app/metadata/services/recall.py:149-170`
- `app/metadata/services/recall.py:208-220`

处理结果：

- 响应新增结构化 `failures`，以“资源类型 + 检索通道 + term”标识失败范围；`status` 由该字段派生。
- 同一 query 的后续检索成功覆盖相同失败范围时会清除旧失败；未覆盖的 term 继续保留失败状态。
- 权限过滤保留通用告警和 `failures`，只移除指向未授权字段、指标或字段值的索引状态告警。

### 2. 查询经验的全文和向量检索仍共用失败边界

位置：

- `app/query/services/experience.py:361-394`
- `app/analytics/agents/explorer/tools/semantic_recall.py:118-147`
- `app/metadata/services/recall.py:223-246`

现状：

- 向量生成、全文检索和向量检索处于同一个 `try` 中。
- `asyncio.gather()` 中任意一路异常都会丢弃其他有效结果。
- `_semantic_ranks()` 捕获异常后返回空字典，上层无法区分“没有匹配结果”和“检索执行失败”。
- 上层会将空结果和当前时间写入快照，之后 24 小时内把失败产生的空结果当作新鲜缓存。

建议：

- 全文和向量分别执行、分别降级，融合仍然可用的通道。
- 返回明确的成功、部分成功、全部失败状态。
- 全部失败时按现有产品要求返回空经验并正常返回语义资源。
- 全部失败时不推进有效缓存时间，确保下次调用能够重试。可以保留上一次成功检索时间；首次检索失败时使用已过期的时间值或重新设计缓存状态字段。

### 3. 持续上下文可能保留旧元数据快照

位置：

- `app/metadata/services/recall.py:74-117`
- `app/metadata/services/recall.py:138-146`

现状：

- 指标和字段按 `rank_score` 选择用于保留的资源快照。
- 新召回结果的 `meta_version` 更高但分数更低时，旧描述、旧引用关系和旧索引状态会继续保留。
- 表上下文已经按 `meta_version` 选择最新快照，三类资源的合并规则不一致。

处理方案：

- 沿用元数据表当前主键识别同一资源：字段使用 `(t_name, name)`，指标使用 `name`，表使用 `name`。
- 合并同一主键的字段或指标时，选择 `meta_version` 最大的完整召回结果；版本相同时按 `rank_score` 选择，保持结果稳定。
- 被选中的结果完整替换旧结果，其中包括描述、类型、别名、示例、外键引用、指标依赖、排名、命中依据和索引状态；不跨版本合并这些字段，避免过期元数据残留。
- 表继续使用现有的“同名表选择最大 `meta_version`”规则；字段值没有 `meta_version`，暂时维持现有按 `(t_name, c_name, value)` 和 `rank_score` 的合并规则。
- 字段或指标改名会改变当前主键，系统将其识别为新资源；本次不引入不可变 ID 来关联改名前后的实体。

### 4. 同一个 query 并发召回可能丢失增量

位置：

- `app/metadata/services/recall.py:185-221`
- `app/metadata/services/recall.py:287-354`
- `app/metadata/repositories/recall.py:90-110`

现状：

- `record()` 使用“读取最新快照、内存合并、插入新快照”的流程。
- 同一个 query 的两个并发调用可能读取相同旧版本，并分别写入互不包含对方结果的新快照。
- `merge()` 与目标或来源 query 的并发 `record()` 也可能互相覆盖或删除刚写入的数据。

建议：

- 为 `(user_id, conversation_id, query)` 建立 PostgreSQL advisory lock。
- 合并两个 query 时按照稳定排序获取两把锁，避免死锁。
- 保留快照版本设计时，在锁内完成最新版本读取、合并、保存和来源删除。

### 5. 查询经验仍可能返回弱相关的三个结果

位置：

- `app/query/repositories/experience_index.py:126-152`
- `app/query/services/experience.py:390-394`

现状：

- 查询经验向量检索没有最低相似度阈值。
- RRF 只使用候选排名，不使用 Elasticsearch 返回的原始相似度。
- 只要索引中存在经验，向量检索就可能提供弱相关候选，最终仍可能填满三条。

建议：

- 在进入 RRF 前按 cosine score 过滤向量候选。
- 相似度阈值应由查询经验检索配置统一管理。
- 过滤、权限检查和有效性检查后允许返回少于三条经验。

## 二、同类架构和冗余问题

### 6. 元数据 Elasticsearch 仓储仍包含历史兼容查询

位置：

- `app/metadata/repositories/column_index.py:90-115`
- `app/metadata/repositories/column_index.py:121-146`
- `app/metadata/repositories/metric_index.py:85-100`
- `app/metadata/repositories/metric_index.py:106-120`
- `app/metadata/repositories/metric_index.py:236-250`
- `app/metadata/repositories/value_index.py:187-205`

现状：

- 字段索引同时按当前 `resource_key` 和旧的 `t_name + name` 匹配。
- 指标索引同时按当前 `resource_key` 和旧的 `name` 匹配。
- 字段值索引同时按当前 `resource_key` 和旧的 `t_name + c_name` 匹配。

建议：

- 开发阶段直接删除旧文档识别分支。
- 统一只按 `resource_key` 查询、过滤和删除。
- 同步收紧只为旧匹配逻辑服务的方法参数。

### 7. 语义召回快照的 `updated_at` 与 `created_at` 重复

位置：

- `app/metadata/models/recall.py:47-54`
- `app/metadata/models/recall.py:89-91`
- `app/metadata/services/recall.py:196-219`
- `app/metadata/services/recall.py:323-351`
- `app/metadata/repositories/recall.py:53-54`
- `app/metadata/repositories/recall.py:84-85`

现状：

- 每条召回快照只执行插入，没有更新操作。
- `created_at` 和 `updated_at` 每次都赋为同一个时间。
- `updated_at` 没有提供额外业务信息。

建议：删除 ORM、领域记录、仓储转换和模型展开中的 `updated_at`。

### 8. Agent 运行时保存了未使用的并发状态引用

位置：

- `app/analytics/agents/contracts.py:90-100`
- `app/analytics/agents/session_service.py:99-107`
- `app/analytics/agents/manager.py:237-251`

现状：

- `ConversationAgentRuntime.session_locks` 和 `parallelism` 只在构造时赋值，后续没有读取。
- `AgentSessionService.session_locks` 和 `parallelism` 两个 property 只用于完成上述赋值。
- 实际并发控制始终在 `AgentSessionService` 内部执行。

建议：删除两个运行时字段及其配套 property。

### 9. 查询执行记录存在重复身份来源

位置：

- `app/query/services/experience.py:41-49`
- `app/query/services/executor.py:138-149`
- `app/query/services/experience.py:120-147`
- `app/query/services/experience.py:152-183`

现状：

- `QueryExecutionContext` 保存 `user_id`。
- `SuccessfulQueryExecution` 和失败记录调用同时提供 `AgentSessionKey`，其中也包含 `user_id`。
- 持久化记录从两处分别读取用户和会话信息，没有校验它们是否一致。

建议：

- 查询记录上下文只保留一个完整的 `AgentSessionKey`。
- 角色、查询目的和 `tool_call_id` 作为该上下文的其他字段。
- 成功和失败记录统一从同一上下文获取身份信息。

### 10. 访问令牌保存了未使用且可能过期的用户状态

位置：

- `app/identity/services/auth.py:73-92`
- `app/identity/services/auth.py:155-177`
- `app/identity/services/auth.py:201-220`
- `app/identity/services/auth.py:282-299`

现状：

- 访问令牌写入 `is_admin` 和 `doris_role`。
- 访问认证会重新从 PostgreSQL 加载用户，并忽略这两个 claim。
- 访问令牌的 `jti` 当前没有撤销、审计或跟踪用途。
- 解析后的 `issued_at`、`expires_at` 没有业务消费者；JWT 解码阶段已经负责有效期校验。

建议：

- 访问令牌只保留认证所需的最小 claim：用户标识、认证版本、签发和过期标准声明。
- 删除无消费者的用户状态副本。
- 时间声明继续在解码时校验，无需保存在返回的 claims 对象中。
- 评估访问令牌 `jti` 是否有近期明确用途，没有用途时删除。

### 11. 语义召回结果向模型暴露了内部版本信息

位置：

- `app/metadata/models/search.py:117-147`
- `app/metadata/services/search.py:50-56`

现状：

- 字段和指标的召回结果包含 `meta_version`、`index_version` 和 `index_status`，表上下文包含 `meta_version`。
- 这些字段随召回快照持久化，并在 `resources`、`record` 两种视图中直接展开给模型。
- 查询经验的资产快照也包含 `meta_version`。

处理方案：

- `meta_version` 和 `index_version` 继续保存在内部召回快照，用于按元数据版本合并持续上下文和判断索引是否过期。
- 在 `SemanticRecallExpansionMiddleware` 中增加模型视图投影；`resources` 和 `record` 视图都移除字段、指标和表的版本字段，以及查询经验资产的 `meta_version`。
- `index_status` 保留给模型，作为无需了解版本号的索引可用性状态。
- 补充中间件测试，断言模型展开内容不含 `meta_version` 或 `index_version`，持久化记录仍保留这些内部字段。

### 12. Specialist 和 Delegation 结果协议重复

位置：

- `app/analytics/agents/contracts.py:165-197`
- `app/analytics/agents/contracts.py:200-235`

现状：

- `SpecialistResult` 与 `DelegationResult` 重复声明状态、摘要、结论、产物、修补请求、置信度和限制字段。
- 两个类包含相同的状态载荷校验逻辑。
- `DelegationResult` 仅额外增加 `analysis_id`、`agent_type`、`session_id`。

建议：抽取共同结果基类，由 `SpecialistResult` 和 `DelegationResult` 在其上增加各自字段。

### 13. 查询经验数量限制存在重复定义

位置：

- `app/analytics/agents/explorer/tools/semantic_recall.py:42`
- `app/metadata/models/recall.py:93-104`

现状：

- 工具通过 `_QUERY_EXPERIENCE_LIMIT = 3` 控制查询数量。
- 持久化领域模型再次硬编码“最多三条”。
- 修改查询策略时需要同步修改两个位置。
- 领域模型验证器还会在 PostgreSQL 快照加载时重新执行服务构造约束。

建议：

- 查询数量策略保留在查询经验召回入口或共享领域常量中。
- 评估删除 `validate_context_payload()` 中由服务构造流程已经保证的约束。
- 如果保留领域不变量，所有调用方使用同一底层定义，避免重复常量。

### 14. 同一召回业务层仍混用 `search` 和 `recall`

位置：

- `app/shared/contracts/query_experience.py:22`
- `app/query/services/experience.py:215-279`
- `app/query/services/experience.py:396-440`
- `app/metadata/services/authorization_filter.py:129`
- `app/metadata/services/search.py:105`

现状：

- 模型可见结果仍名为 `QueryExperienceSearchResult`。
- 查询经验领域服务仍使用 `search()` 和 `_to_search_result()`。
- 权限过滤器仍使用 `filter_semantic_response()`。
- 语义召回服务内部上下文仍名为 `_SearchContext`。

建议：

- 领域层和模型协议统一使用 `recall`：例如 `QueryExperienceRecallResult`、`recall()`、`filter_recall_response()`、`_RecallContext`。
- Elasticsearch 仓储中的 `search_text()`、`search_vector()`、`SearchHit` 保留 `search`，因为它们表达索引检索原语。

## 三、确认未使用的遗留代码

以下符号在全仓库只有定义，没有调用：

- `app/identity/errors.py:102`：`AssetAccessDeniedError`
- `app/identity/errors.py:173`：`UserDeletionPendingError`
- `app/identity/services/authorization.py:110`：`AssetIdentity.as_dict`
- `app/identity/repositories/auth.py:198`：`AuthPGRepo.revoke_refresh_token`
- `app/sandbox/capacity.py:178`：`FairCapacityLimiter.reconcile`
- `app/analytics/agents/manager.py:136`：`AgentManager.get_active_model`
- `app/analytics/agents/manager.py:362`：`AgentManager.reset`
- `app/shared/config/app_config.py:376`：`reload_config`

建议：直接删除定义及配套测试、导入和注释，不保留兼容别名或转发方法。

## 四、建议实施顺序

1. 修复告警丢失与历史 `partial` 状态无法恢复的问题。
2. 拆分查询经验全文、向量检索的失败边界并修正缓存时间语义。
3. 修复持续上下文的元数据版本合并规则。
4. 为 query 持续上下文增加并发写入保护。
5. 为查询经验增加相关度阈值。
6. 删除 Elasticsearch 历史兼容查询。
7. 删除明确未使用的代码和重复状态字段。
8. 统一查询执行身份上下文、召回命名和结果协议。
9. 收紧访问令牌载荷并合并重复协议模型。

每个步骤应从底层模型或服务抽象开始修改，并同步更新所有上层调用方和测试，不添加旧接口别名或兼容转发层。

## 五、验证结果

- `ruff check app`：通过。
- `pyright app`：`0 errors, 0 warnings, 0 informations`。
- 非沙箱测试：`296 passed, 1 skipped, 56 subtests passed`。
- `tests/sandbox/test_docker_sandbox_manager.py`：`28 passed, 18 skipped, 10 subtests passed`。
- `tests/sandbox/test_sandbox_tombstones.py`：`2 passed`。
- `tests/sandbox/test_sandbox_ownership.py` 在当前 Python 3.14 环境中无法完整执行。最小脚本可以复现连续第三次 `asyncio.to_thread()` 卡住，因此该现象未计为项目代码问题。
