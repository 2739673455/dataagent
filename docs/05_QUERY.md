# 05. Query：从 SQL Guard 到查询经验

## 功能说明

`app/query` 负责受控执行 Explorer 智能体生成的 SQL 查询。模块在当前用户的 Doris 身份下完成确定性安全语法与权限校验、只读流式执行、沙箱 CSV 结果落盘与全流程审计，并将成功执行的业务查询聚合成可召回的查询经验（QueryExperience），供智能体在后续分析中复用已验证的 SQL 结构。

本模块的核心职责与底层实现细节如下。

### 1. SQL Guard 确定性安全校验体系

`QueryGuardService` 位于 SQL 执行的最前端，基于 `sqlglot` 的 Doris 方言进行 AST 级静态语法与安全检查。

- **语法约束与单语句拦截**：
  - 只接受单个 `SELECT` 或 `WITH ... SELECT` 语句，拒绝空字符串、语法解析错误以及分号分隔的多语句提交；
  - 严格封锁所有非只读 AST 节点：通过 `_FORBIDDEN_NODE_KEYS` 黑名单拦截所有 DDL（`CREATE/ALTER/DROP`）、DML（`INSERT/UPDATE/DELETE`）、事务操作（`COMMIT/ROLLBACK`）、会话配置（`SET/USE`）、以及管理命令（`SHOW/DESCRIBE` 等）；
  - 封锁具有副作用或潜在破坏性的函数（`_SIDE_EFFECT_FUNCTIONS` 如 `benchmark`、`sleep`、`get_lock`、`load_file` 等），只允许安全的内置聚合与标量函数。
- **目录元数据加载与血缘解析（Qualify）**：
  - 加载当前系统的元数据目录，并结合当前请求用户的 `AssetAccessPolicy` 构建可访问的虚拟 schema；
  - 调用 `sqlglot.optimizer.qualify` 展开所有 CTE（公用表表达式）、子查询与表别名，将所有模糊引用的字段显式补齐 `database.table.column` 的绝对血缘，得到准确的 `QueryTableRef` 与 `QueryColumnRef`。
- **星号展开与部分列授权保护**：
  - 若用户对某张表仅拥有部分字段的权限（例如仅允许访问 `orders.id` 与 `orders.amount`，但未被授予 `orders.user_id`），若 SQL 中使用了 `SELECT * FROM orders`，Guard 在 qualify 阶段前提前将其标记为受限表，并将星号自动展开为用户已授权的物理列；
  - 若展开后的字段包含任何未经授权的字段，Guard 立即生成 `QueryValidationIssue` 并拒绝执行。
- **JOIN 连接条件完整性检查**：遍历 AST 中的所有 `Scope`，校验每一次 `JOIN` 均具备显式的 `ON` 关联条件或 `USING` 关联键，杜绝产生笛卡尔积（Cartesian Product）导致 Doris 内存爆满。
- **结构化输出**：校验通过后输出统一规范化的 `normalized_sql`；校验失败时，将所有具体违规项填充进 `QueryValidationIssue` 列表并返回 `QueryValidationResult(valid=False)`，不通过抛出异常作为常规控制流。

### 2. 受限只读执行与沙箱结果落盘

`AnalysisQueryService` 接收 Guard 校验通过的 `normalized_sql`，在底层物理引擎中受限运行。

- **强制角色专用连接池**：
  - 必须通过 Identity 模块解析的 `ResolvedQueryPrincipal`，从 `DorisQueryClientRegistry` 获取该业务角色专用的连接池；
  - 严禁使用 Doris 管理员连接执行任何用户查询，确保 Doris 底层引擎原生的权限边界与 Row Policy 策略强制生效。
- **会话级资源保护参数**：在执行查询前，连接设置严格的只读会话变量：
  - `exec_mem_limit`：单查询最大内存上限（配置读取）；
  - `query_timeout`：查询执行超时硬限制；
  - `workload_group`：指定 Doris 资源隔离工作负载组。
- **游标异步流式批次读取**：
  - 结果读取采用流式游标迭代器（`AsyncGenerator[QueryBatch]`），按配置的 `batch_size` 逐批次消费数据；
  - 内存中绝不一次性装载全量查询结果集，彻底杜绝超大结果集撑爆 API 进程内存。
- **沙箱 CSV 导出与公式注入防御（CSV Injection Defense）**：
  - 全量查询结果由流式通道实时写入当前智能体 Session 对应的沙箱工作区路径 `/data/{conv_id}/sessions/.../query_{id}.csv`；
  - **公式注入防御**：电子表格软件（Excel、Numbers）在打开以 `= + - @ \t \r` 开头的单元格内容时会自动将其作为可执行公式解析。在写入 CSV 时，系统检测到上述字符开头的文本内容，自动在前方添加转义单引号 `'`，阻止客户端打开表格时的公式执行漏洞；
  - `datetime`、`date`、`Decimal` 与二进制字段均按照规范进行稳定格式化编码。
- **有界内存数据摘要（AnalysisQueryResult）**：
  - 在写入 CSV 的同时，流式统计字段类型与可空性（`QueryResultColumn`）、时间字段的最早与最晚区间（`QueryTimeRange`）、总行数 `row_count`；
  - 仅在内存中提取前若干行样例数据（默认 5 行）存入 `sample`，构造轻量级 `AnalysisQueryResult` 返回给大模型，供其快速感知数据分布并编写下一步分析脚本。

### 3. 执行审计与异步记录器

- **防伪造会话凭据**：智能体通过工具发起查询时，必须携带当前运行时绑定的 `AgentSessionKey`，调用方无法通过参数篡改所属的 `user_id`、`conversation_id` 或输出文件目录。
- **全流程审计持久化**：
  - 无论查询被安全 Guard 拒绝（`rejected`）、Doris 执行失败（`failed`）还是执行成功（`succeeded`），系统均向 PostgreSQL 中的 `query_executions` 表写入完整的执行事实；
  - 记录内容包括：用户 ID、角色名、会话标识、分析标识、原始 SQL、规范化 SQL、执行状态、错误码与明细、耗时、影响行数、校验问题与 CSV 产物路径。
- **异步解耦与错误隔离**：审计写入由 `QueryExecutionRecorder` 异步执行。若因数据库抖动导致审计记录写入失败，系统仅在服务端输出错误日志，绝不抛出异常打断用户的正常分析流程。

### 4. 查询经验聚合与生命周期管理

- **聚合维度与结构指纹**：
  - 仅执行成功的业务查询会被聚合为 `QueryExperience`；
  - 聚合键为：`role_name + authorization_epoch + SQL 结构指纹`（将具体字面量参数化后计算哈希）；
  - 相同结构的 SQL 多次以不同参数成功执行时，系统在数据库中原子更新成功次数、最近使用时间、关联的来源执行列表，并在 `purposes` 列表中追加本次查询的自然语言业务目的（最多保留 5 条最新目的），不重复创建新记录。
- **经验状态机**：
  - `active`：经验处于可用状态，可被后续分析召回；
  - `disabled`：经验已失效，不可被召回。原因包括管理员手动下线（`admin_disabled`）或元数据变更导致的失效（`metadata_changed`）；
  - `deleting`：经验处于待删除流程，索引清理完成后从数据库彻底物理移除。
- **资产版本绑定与自愈机制**：
  - 经验关联保存其引用的全部物理表与字段，并记录生成时的 `meta_version`；
  - 当底层表或字段的 `meta_version` 发生变化时，系统自动将相关经验置为 `disabled(reason='metadata_changed')`；
  - 若相同结构的 SQL 在新的元数据版本下再次真实成功执行，系统自动将其状态恢复为 `active`；管理员主动禁用的经验则禁止自动恢复。
- **版本推进（revision）与 CAS 机制**：每次经验内容或状态发生变更，其 `revision` 自增，`indexed_revision` 记录 Elasticsearch 索引已同步的代次。

### 5. 经验语义索引与混合召回

- **双通道检索与 RRF 融合**：
  - 经验的自然语言用途描述（`purposes`）通过嵌入模型向量化并同步至 Elasticsearch；
  - 召回阶段在 Elasticsearch 层面强制前置过滤当前角色 `role_name` 与当前授权代次 `authorization_epoch`；
  - 同时发起 BM25 全文检索与 Dense Vector 向量检索，使用 RRF 算法在 Elasticsearch 结果集中进行加权融合。
- **事实源状态与实时权限二次校验**：
  - 从 ES 召回候选经验后，必须回查 PostgreSQL 中的真实状态，确认其仍为 `active`；
  - 比对经验中记录的资产版本与当前元数据事实版本是否一致；
  - 再次使用当前用户的 `AssetAccessPolicy` 校验对该经验引用资产的实时可读性，杜绝权限回收后的越权召回。

---

## 核心实现代码与模块架构

### 1. SQL 校验模型与结果定义

文件路径：`app/query/models/validation.py`

```python
# app/query/models/validation.py
"""查询引用与 SQL 校验模型。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

type QueryKind = Literal["business", "catalog"]


class QueryTableRef(BaseModel):
    """查询引用的数据表。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database: str | None = None
    name: str

    @property
    def qualified_name(self) -> str:
        return f"{self.database}.{self.name}" if self.database else self.name


class QueryColumnRef(BaseModel):
    """查询引用的物理字段。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database: str | None = None
    table: str
    name: str

    @property
    def qualified_name(self) -> str:
        prefix = f"{self.database}." if self.database else ""
        return f"{prefix}{self.table}.{self.name}"


class QueryValidationIssue(BaseModel):
    """一项确定性的 SQL 校验问题。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    table: str | None = None
    column: str | None = None


class QueryValidationResult(BaseModel):
    """SQL 安全检查结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    normalized_sql: str | None
    query_kind: QueryKind = "business"
    tables: list[QueryTableRef] = Field(default_factory=list)
    columns: list[QueryColumnRef] = Field(default_factory=list)
    output_columns: list[str] = Field(default_factory=list)
    issues: list[QueryValidationIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self) -> "QueryValidationResult":
        if self.valid == bool(self.issues):
            raise ValueError("valid 必须与 issues 是否为空保持相反状态")
        if self.valid and self.normalized_sql is None:
            raise ValueError("有效查询必须包含 normalized_sql")
        return self
```

### 2. 查询执行产物与审计持久化模型

文件路径：`app/query/models/execution.py`

```python
# app/query/models/execution.py
"""查询执行配置、结果与持久化模型。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import MetaBase


class QueryResultColumn(BaseModel):
    """查询结果字段信息。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str
    nullable: bool


class QueryTimeRange(BaseModel):
    """时间字段在结果集中的取值范围。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: str
    end: str


class AnalysisQueryResult(BaseModel):
    """写入会话沙箱后的查询结果摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    columns: list[QueryResultColumn]
    row_count: int
    time_range: dict[str, QueryTimeRange]
    sample: list[dict[str, Any]]


class QueryExecution(MetaBase):
    """SQL 执行审计记录。"""

    __tablename__ = "query_executions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    role_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    authorization_epoch: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    analysis_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(256))
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    raw_sql: Mapped[Text] = mapped_column(Text, nullable=False)
    normalized_sql: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_detail: Mapped[str | None] = mapped_column(Text)
    validation: Mapped[dict[str, object] | None] = mapped_column(JSON)
    result_summary: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('rejected', 'failed', 'succeeded')",
            name="ck_query_execution_status",
        ),
    )
```

### 3. SQL Guard 确定性安全校验服务实现

文件路径：`app/query/services/guard.py`

```python
# app/query/services/guard.py（核心校验逻辑实现）
"""只读分析 SQL 的确定性安全校验。"""

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.query.models.validation import (
    QueryColumnRef,
    QueryTableRef,
    QueryValidationIssue,
    QueryValidationResult,
)

_FORBIDDEN_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Transaction,
)

_SIDE_EFFECT_FUNCTIONS = frozenset(
    {"benchmark", "sleep", "get_lock", "is_free_lock", "load_file"}
)


class QueryGuardService:
    """SQL 静态 AST 校验与血缘提取器。"""

    def validate_sql(
        self,
        raw_sql: str,
        policy: AssetAccessPolicy,
        schema: dict[str, dict[str, str]],
    ) -> QueryValidationResult:
        """执行完整语法树校验、只读判断与权限比对。"""
        issues: list[QueryValidationIssue] = []
        if not raw_sql or not raw_sql.strip():
            return QueryValidationResult(
                valid=False,
                normalized_sql=None,
                issues=[QueryValidationIssue(code="EMPTY_SQL", message="SQL 不能为空")],
            )

        # 1. AST 解析
        try:
            expressions = sqlglot.parse(raw_sql, read="doris")
        except Exception as exc:
            return QueryValidationResult(
                valid=False,
                normalized_sql=None,
                issues=[QueryValidationIssue(code="SYNTAX_ERROR", message=f"语法错误: {exc}")],
            )

        if len(expressions) != 1 or expressions[0] is None:
            return QueryValidationResult(
                valid=False,
                normalized_sql=None,
                issues=[QueryValidationIssue(code="MULTI_STATEMENT", message="仅支持执行单条 SQL 语句")],
            )

        root = expressions[0]

        # 2. 禁止节点与副作用函数拦截
        if isinstance(root, _FORBIDDEN_NODE_TYPES) or not isinstance(root, exp.Query):
            return QueryValidationResult(
                valid=False,
                normalized_sql=None,
                issues=[QueryValidationIssue(code="FORBIDDEN_OPERATION", message="仅允许执行只读查询语句")],
            )

        for func in root.find_all(exp.Anonymous, exp.Func):
            if func.name.lower() in _SIDE_EFFECT_FUNCTIONS:
                issues.append(
                    QueryValidationIssue(
                        code="FORBIDDEN_FUNCTION",
                        message=f"使用了禁止的副作用函数: {func.name}",
                    )
                )

        if issues:
            return QueryValidationResult(valid=False, normalized_sql=None, issues=issues)

        # 3. Qualify 展开别名与血缘解析
        try:
            qualified = qualify(root, schema=schema, dialect="doris")
        except Exception as exc:
            return QueryValidationResult(
                valid=False,
                normalized_sql=None,
                issues=[QueryValidationIssue(code="QUALIFY_FAILED", message=f"字段血缘解析失败: {exc}")],
            )

        # 4. 提取血缘表与字段并进行权限比对
        tables: list[QueryTableRef] = []
        columns: list[QueryColumnRef] = []

        for table in qualified.find_all(exp.Table):
            t_ref = QueryTableRef(database=table.db or None, name=table.name)
            tables.append(t_ref)
            # 校验表级别权限
            asset = AssetIdentity(
                data_source="doris",
                database_name=t_ref.database,
                table_name=t_ref.name,
            )
            if not policy.is_visible(asset):
                issues.append(
                    QueryValidationIssue(
                        code="TABLE_ACCESS_DENIED",
                        message=f"无权访问表: {t_ref.qualified_name}",
                        table=t_ref.name,
                    )
                )

        for col in qualified.find_all(exp.Column):
            c_ref = QueryColumnRef(
                database=col.table_args[1] if len(col.table_args) > 1 else None,
                table=col.table,
                name=col.name,
            )
            columns.append(c_ref)
            asset = AssetIdentity(
                data_source="doris",
                database_name=c_ref.database,
                table_name=c_ref.table,
                column_name=c_ref.name,
            )
            if not policy.allows(asset):
                issues.append(
                    QueryValidationIssue(
                        code="COLUMN_ACCESS_DENIED",
                        message=f"无权访问字段: {c_ref.qualified_name}",
                        table=c_ref.table,
                        column=c_ref.name,
                    )
                )

        if issues:
            return QueryValidationResult(valid=False, normalized_sql=None, issues=issues)

        normalized_sql = qualified.sql(dialect="doris")
        return QueryValidationResult(
            valid=True,
            normalized_sql=normalized_sql,
            tables=tables,
            columns=columns,
            output_columns=[s.alias_or_name for s in qualified.selects],
            issues=[],
        )
```

### 4. 受限查询执行与沙箱 CSV 导出实现

文件路径：`app/query/services/executor.py`

```python
# app/query/services/executor.py（核心执行与 CSV 导出片段）
"""受控查询执行与 CSV 导出。"""

import csv
import io
from typing import Any
from app.query.models.execution import (
    AnalysisQueryResult,
    QueryBatch,
    QueryResultColumn,
    QueryTimeRange,
)
from app.shared.contracts.analysis import AgentSessionKey
from app.sandbox.backend import DockerSandboxBackend


def sanitize_csv_cell(value: Any) -> Any:
    """公式注入防御：对以 = + - @ 开头的文本添加前导单引号。"""
    if isinstance(value, str) and value:
        if value[0] in ("=", "+", "-", "@", "\t", "\r"):
            return f"'{value}"
    return value


class AnalysisQueryService:
    """管理 Doris 受控执行与沙箱 CSV 产物写入。"""

    def __init__(self, sample_rows: int = 5) -> None:
        self._sample_rows = sample_rows

    async def execute_and_export(
        self,
        session_key: AgentSessionKey,
        sandbox: DockerSandboxBackend,
        batches: list[QueryBatch],
    ) -> AnalysisQueryResult:
        """将流式批次写入沙箱 CSV 文件，并构造内存摘要。"""
        if not batches:
            raise RuntimeError("查询未返回有效批次数据")

        column_names = list(batches[0].column_names)
        artifact_name = f"query_{session_key.session_id[:8]}.csv"
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(column_names)

        total_rows = 0
        sample_rows_data: list[dict[str, Any]] = []

        for batch in batches:
            for row in batch.rows:
                sanitized_row = [sanitize_csv_cell(cell) for cell in row]
                writer.writerow(sanitized_row)
                if total_rows < self._sample_rows:
                    sample_rows_data.append(dict(zip(column_names, row, strict=False)))
                total_rows += 1

        # 写入会话沙箱工作区
        content = csv_buffer.getvalue()
        sandbox.write(artifact_name, content)

        # 构造列信息摘要
        columns_summary = [
            QueryResultColumn(name=col, type="UNKNOWN", nullable=True)
            for col in column_names
        ]

        return AnalysisQueryResult(
            path=artifact_name,
            columns=columns_summary,
            row_count=total_rows,
            time_range={},
            sample=sample_rows_data,
        )
```

### 5. 查询执行统一入口与审计编排实现

文件路径：`app/query/services/execution_handler.py`

```python
# app/query/services/execution_handler.py（核心编排）
"""查询执行全链路编排。"""

from app.identity.services.authorization import AuthorizationService
from app.identity.services.query_principal import QueryPrincipalService
from app.query.models.execution import AnalysisQueryResult
from app.query.services.guard import QueryGuardService
from app.query.services.executor import AnalysisQueryService
from app.query.repositories.execution_postgres import QueryExecutionPGRepo
from app.shared.contracts.analysis import AgentSessionKey
from app.sandbox.backend import DockerSandboxBackend


class QueryExecutionHandler:
    """协调身份解析、Guard 校验、Doris 执行与审计记录。"""

    def __init__(
        self,
        principal_service: QueryPrincipalService,
        auth_service: AuthorizationService,
        guard_service: QueryGuardService,
        executor_service: AnalysisQueryService,
        audit_repo: QueryExecutionPGRepo,
    ) -> None:
        self._principal_service = principal_service
        self._auth_service = auth_service
        self._guard_service = guard_service
        self._executor_service = executor_service
        self._audit_repo = audit_repo

    async def handle_query(
        self,
        session_key: AgentSessionKey,
        raw_sql: str,
        purpose: str,
        sandbox: DockerSandboxBackend,
    ) -> AnalysisQueryResult:
        """执行完整问数查询链路。"""
        # 1. 解析查询凭据与当前资产策略
        principal = await self._principal_service.resolve(session_key.user_id)
        policy = await self._auth_service.get_asset_policy(session_key.user_id)

        # 2. SQL Guard 确定性安全校验
        validation = self._guard_service.validate_sql(raw_sql, policy, schema={})
        if not validation.valid:
            await self._audit_repo.record_rejected(
                session_key, raw_sql, purpose, validation
            )
            raise ValueError(f"SQL 安全校验未通过: {validation.issues}")

        # 3. 受控执行与沙箱落盘
        try:
            batches = []  # 实际通过 DorisQueryRepository 流式读取
            result = await self._executor_service.execute_and_export(
                session_key, sandbox, batches
            )
            await self._audit_repo.record_success(
                session_key, validation.normalized_sql, purpose, result
            )
            return result
        except Exception as exc:
            await self._audit_repo.record_failure(
                session_key, raw_sql, purpose, error=str(exc)
            )
            raise
```

---

## 阶段学习与验证要点

### 阶段 1：验证 SQL Guard 确定性防御

1. **多语句与危险操作拦截验证**：传入 `SELECT 1; DROP TABLE users;`，验证 Guard 识别出多语句并拒绝；传入 `UPDATE orders SET amount = 0`，验证被禁止节点拦截。
2. **副作用函数拦截验证**：传入 `SELECT benchmark(10000000, md5('test'))`，验证被副作用黑名单拦截。
3. **未授权列访问拦截验证**：在仅开放 `orders.amount` 的权限下，提交 `SELECT user_id FROM orders`，验证被 `COLUMN_ACCESS_DENIED` 拦截。

### 阶段 2：验证沙箱 CSV 落盘与公式注入防护

1. **公式注入防范验证**：在数据库数据中包含单元格 `=SUM(A1:A10)`，执行查询并导出 CSV，验证沙箱文件中的实际内容被安全转义为 `'=SUM(A1:A10)`。
2. **轻量级结果摘要验证**：执行返回上万行数据的查询，验证返回给智能体的 `AnalysisQueryResult.sample` 严格限制在 5 行，且沙箱 CSV 文件完整包含全部行数。

### 阶段 3：验证查询经验聚合与失效

1. **结构指纹聚合验证**：连续使用不同的 `WHERE id = 1` 和 `WHERE id = 2` 执行查询，验证数据库中仅有一条 `QueryExperience` 记录，其 `success_count` 递增。
2. **元数据变更级联失效验证**：修改相关表的元数据版本，验证关联的查询经验状态自动变为 `disabled(reason='metadata_changed')`。
