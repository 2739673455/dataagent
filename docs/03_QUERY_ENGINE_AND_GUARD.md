# 模块三：安全查询引擎与执行守卫

## 1. 模块定位与职责

安全查询引擎与执行守卫模块是 DataAgent 面向底层数仓的安全防火墙与执行枢纽。该模块负责确保 Agent 生成的所有 SQL 在语法、权限、安全和资源消耗上完全受控，并利用用户绑定的隔离只读账号，在受限的资源配额下流式拉取分析数据集。

```mermaid
flowchart LR
    Agent[Explorer Agent / 工具] -->|提交待执行 SQL| Guard[QueryGuardService\nAST 语法与合规守卫]
    
    subgraph GuardChecks [安全审计项]
        AST[严格只读校验\n阻断 DDL / DML / SET]
        AssetAuth[资产授权校验\n校验表/列可见性]
        LimitInject[强制注入 LIMIT\n复杂度与行数约束]
    end
    
    Guard --> GuardChecks
    GuardChecks -->|审计通过| ExecSvc[AnalysisQueryService\n分析查询调度]
    
    Principal[QueryPrincipalService\n匹配用户只读代理账号] --> ExecSvc
    
    ExecSvc --> DorisRepo[DorisQueryRepository\n注入会话级资源限制]
    
    subgraph DorisSession [Doris 会话配额]
        WG[SET workload_group]
        Timeout[SET query_timeout]
        Mem[SET exec_mem_limit]
    end
    
    DorisRepo --> DorisSession
    DorisSession --> Doris[(Apache Doris\n服务端游标分批流式读取)]
    Doris --> Result[结构化批次 QueryBatch / CSV 数据集]
```

---

## 2. 核心架构与功能特性

### 2.1 动态只读代理身份调度
- [`QueryPrincipalService`](file:///home/kodey/dataagent/app/services/query_principal_service.py#L11) 根据当前登录用户的 `user_id` 与绑定的 Doris 角色，从凭据库中检索并解密对应的代理查询账号（如 `sales_query`），确保查询在数仓端具备天然的库表和行级权限隔离。
- **启动前只读权限校验**：系统启动或身份加载时，[`DorisQueryRepository.verify_readonly_access`](file:///home/kodey/dataagent/app/repositories/doris_query_repo.py#L61-L90) 自动执行 `SHOW GRANTS`，若发现账号具备写权限（如 `LOAD_PRIV`、`ALTER_PRIV`、`ADMIN_PRIV`），立即阻断并抛出 [`DorisReadonlyPrivilegeError`](file:///home/kodey/dataagent/app/repositories/doris_query_repo.py#L27-L29)。

### 2.2 AST 语法解析与多维安全守卫
[`QueryGuardService`](file:///home/kodey/dataagent/app/services/query_guard_service.py#L35) 在 SQL 进入执行引擎前进行确定性审计：
- **严格只读校验**：
  - 基于 SQL 语法树（AST）解析，仅允许纯 `SELECT` 及合法的 `WITH ... SELECT` 语句。
  - 严厉拦截所有数据定义语句（`CREATE`、`DROP`、`ALTER`、`TRUNCATE`）。
  - 严厉拦截所有数据变更语句（`INSERT`、`UPDATE`、`DELETE`）。
  - 严厉拦截所有管理与会话控制语句（`GRANT`、`REVOKE`、`SET`、`KILL`）。
- **资产范围与越权审计**：
  - 提取 SQL 涉及的所有物理表与字段，与用户当前的授权策略（[`AssetAccessPolicy`](file:///home/kodey/dataagent/app/services/authorization_service.py#L22-L36)）进行交集比对。
  - 若查询包含未授权表或未授权列，直接抛出拒绝访问异常。
- **强制 LIMIT 约束与行数截断**：
  - 自动检测顶层查询是否包含 `LIMIT`。若未包含，自动追加系统全局上限，防止 Agent 意外触发全表扫描导致内存溢出。

### 2.3 连接级资源配额控制
在建立 Doris 异步连接后，[`DorisQueryRepository._apply_session_limits`](file:///home/kodey/dataagent/app/repositories/doris_query_repo.py#L44-L60) 自动在当前会话执行参数注入：
```sql
SET workload_group = '{workload_group}';
SET query_timeout = {timeout_seconds};
SET exec_mem_limit = {memory_limit_bytes};
SET max_allowed_packet = {max_cell_bytes};
```
- 确保单个重型分析查询不会耗尽数仓全局计算资源或阻塞其他业务。

### 2.4 服务端游标分批流式传输
- [`DorisQueryRepository.execute_streaming`](file:///home/kodey/dataagent/app/repositories/doris_query_repo.py#L120-L180) 结合服务端游标（Server-side Cursor）按批次（[`QueryBatch`](file:///home/kodey/dataagent/app/models/query.py#L58-L69)）拉取数据，避免一次性将百万级结果集加载至 Web 服务内存。
- [`AnalysisQueryService`](file:///home/kodey/dataagent/app/services/analysis_query_service.py#L30) 提供面向上层 Agent 的高级封装，将结果集直接格式化并流式写入用户沙盒中的 CSV 文件。

### 2.5 查询经验记忆

每次 `execute_sql` 尝试都会在 Meta PostgreSQL 写入一条执行记录。成功执行会按 `用户 + Doris 角色 + SQL 结构指纹` 聚合为用户私有经验，Guard 提取的表和字段及其 `meta_version` 会作为经验资产快照保存。当前阶段不建立 SQL 与指标的结构化关联。

- SQL 结构指纹来自 Guard 规范化 SQL 的 AST，所有字面量会替换为 `:p1`、`:p2` 等占位符。Elasticsearch 只保存任务文本、表字段名称和向量，不保存原始 SQL、查询字面量或结果样本。
- 成功查询先进入 `candidate` 状态。Explorer 的最终 `SpecialistResult` 直接引用对应查询产物时，经验提升为 `promoted` 并累计采用次数。
- `search_query_experiences` 融合全文召回、向量召回、表字段重合度、成功次数、采用次数和新鲜度排序。
- 表或字段元数据更新、删除及批量导入完成后，所有关联经验会立即转为 `disabled` 并删除 Elasticsearch 文档。检索时还会再次比较当前元数据版本，补偿并发变更或索引删除失败，失效经验不会返回 Explorer。
- 已失效 SQL 后续重新通过完整 Guard 并成功执行时，会使用最新表字段版本恢复为新的 `candidate` 经验。
- PostgreSQL 保存完整事实和聚合状态，Elasticsearch 作为可重建的语义检索索引。索引更新失败不会改变 SQL 的成功结果，后续执行或采用会再次触发同步。

---

## 3. 核心接口与组件规范

### 核心服务方法
| 组件 | 核心方法 | 职责 |
| :--- | :--- | :--- |
| [`QueryGuardService`](file:///home/kodey/dataagent/app/services/query_guard_service.py#L35) | `validate_sql(sql, policy)` | 校验 SQL 语法只读性、提取依赖表列、比对用户资产白名单 |
| [`QueryPrincipalService`](file:///home/kodey/dataagent/app/services/query_principal_service.py#L11) | `get_principal(user_id)` | 获取并解密当前用户绑定的只读代理身份 |
| [`AnalysisQueryService`](file:///home/kodey/dataagent/app/services/analysis_query_service.py#L30) | `execute_to_file(sql, target_path)` | 结合安全守卫执行受控查询，并将结果安全落盘为 CSV 数据集 |
| [`DorisQueryRepository`](file:///home/kodey/dataagent/app/repositories/doris_query_repo.py#L37) | `execute_streaming(sql, limits)` | 会话级参数注入与服务端游标流式拉取 |
| `QueryExperienceService` | `record_success` / `record_failure` / `search` | 记录查询事实、聚合候选经验并执行权限感知的混合检索 |

---

## 4. 关键代码映射

- 查询安全守卫：[`app/services/query_guard_service.py`](file:///home/kodey/dataagent/app/services/query_guard_service.py)
- 分析查询调度服务：[`app/services/analysis_query_service.py`](file:///home/kodey/dataagent/app/services/analysis_query_service.py)
- 查询代理身份调度：[`app/services/query_principal_service.py`](file:///home/kodey/dataagent/app/services/query_principal_service.py)
- Doris 底层查询仓储：[`app/repositories/doris_query_repo.py`](file:///home/kodey/dataagent/app/repositories/doris_query_repo.py)
- 查询模型与限制协议：[`app/models/query.py`](file:///home/kodey/dataagent/app/models/query.py)
- 查询经验模型：`app/models/query_experience.py`
- 查询经验服务：`app/services/query_experience_service.py`
- 查询经验检索工具：`app/agents/explorer/tools/query_experience.py`
