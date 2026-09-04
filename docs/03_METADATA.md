# 03. Metadata：从业务目录到语义召回

## 功能说明

`app/metadata` 将 Doris 底层的物理表结构映射为大语言模型可理解的业务元数据目录，并构建高可用的语义检索体系。模块负责维护表、字段、指标、外键引用与取值采样；将可搜索文本与采样值实时同步至 Elasticsearch；在权限白名单前置约束下完成语义召回；并将召回上下文沉淀为会话级不可变快照供智能体消费。

本模块的核心职责与底层实现细节如下。

### 1. 业务元数据目录与物理结构校验

业务目录实体模型由 `app/metadata/models/catalog.py` 严格定义，分为事实模型与物理验证两层体系。

- **目录持久化模型结构**：
  - `TableInfo`：记录物理表名 `name`、业务角色 `role`（`fact` 事实表或 `dim` 维度表）、主键字段列表 `primary_key_columns`、表业务描述 `description` 以及字段取值增量同步游标字段 `value_index_cursor_column`；
  - `ColumnInfo`：记录所属表 `t_name`、字段名 `name`、物理数据类型 `type`、业务含义描述 `description`、样例数据 `examples`（经 `serialize_column_examples` 规范化）、业务别名 `alias`、是否建立取值索引 `index_values`，以及跨表外键引用 `reference_t_name` 与 `reference_c_name`（配置 `ForeignKeyConstraint` 级联维护）；
  - `MetricInfo`：记录业务指标名 `name`、计算逻辑描述 `description`、业务别名 `alias`，以及关联计算所依赖的物理字段列表 `relevant_columns`；
  - `ColumnMetric`：通过联合外键持久化字段与指标的多对多从属关系，支持级联删除。
- **Doris 物理结构真实性校验**：所有目录写入前，`MetaCatalogService` 必须通过管理员连接调用 `SourceDorisRepo` 执行只读预检：
  - 核验物理表是否存在；
  - 读取底层物理主键与字段类型，确保目录中的类型与 Doris 物理 schema 严格一致；
  - 动态 SQL 标识符强制经过受控转义 quote，防止 SQL 注入；LIMIT 采样参数强制经过正整数范围校验；
  - 采样值提取时限制最多读取 10 条，并由 `serialize_column_examples` 统一完成 `datetime/date` ISO 格式化与 `Decimal` 转浮点数。
- **反向依赖约束保护**：删除字段前，系统反向扫描所有引用该字段的其他表外键引用以及依赖该字段的业务指标，存在依赖时直接拒绝删除，防止业务目录产生悬空引用。
- **严格版本代次控制哲学**：
  - 模型定义了两个版本字段：事实版本 `meta_version` 与索引版本 `index_version`；
  - `meta_version` 仅在业务内容发生实质改变时递增。每次变更前调用实体的 `metadata_snapshot()` 方法提取纯业务元组（如表角色、描述、主键；字段类型、别名、示例、引用；指标公式与依赖），比对发现无变化时不推进版本；
  - 更新时间、操作人等运维字段变更绝对不推进 `meta_version`；
  - `index_version` 专门记录 Elasticsearch 检索索引已同步到的版本号，用于精确追踪同步落后状态。

### 2. YAML 批量导入与变更差异引擎

为支持配置即代码（Config as Code），`MetaImportService` 提供了从 `conf/meta_config.yaml` 批量导入目录的能力。

- **强类型配置模型校验**：`MetaConfig` Pydantic 模型在内存中静态拦截全部配置错误：杜绝同名表、同名指标或同表同名字段；拦截空描述；校验所有 `ColumnReference` 目标表和字段在配置或既有目录中真实存在；校验指标依赖的字段合法性。
- **两阶段执行与只读比对（Dry-Run）**：
  - 第一阶段：在不开启写事务的前提下，读取既有 PostgreSQL 目录快照并连接 Doris 验证物理结构，在纯内存中完成目标状态计算，得出 `ResourceChanges` 差异结构（包含 tables、columns、metrics 各自的 created、updated、deleted 列表）；
  - `dry-run=True` 时仅返回差异清单供管理员确认，不产生任何数据库写操作。
- **导入模式控制**：
  - `merge` 模式：仅应用 YAML 中声明的资源，保留既有数据库中存在但 YAML 中未声明的表、字段与指标；
  - `replace` 模式：完全以 YAML 为准，自动删除既有数据库中存在但 YAML 中未声明的表、字段与指标（删除前执行依赖级联安全校验）。
- **原子事务与后置派发**：
  - 差异计算通过后，整个变更在一个独立的 PostgreSQL 事务中提交生效，杜绝局部写入；
  - 只有在写事务成功 commit 之后，系统才派发异步 Elasticsearch 索引任务与查询经验（QueryExperience）失效通知。预检或写入失败绝不残留部分更新。

### 3. 语义索引与双通道检索投影

为了让大模型能够通过模糊自然语言召回相关表、字段和指标，系统在 Elasticsearch 中维护语义投影。

- **搜索文本切分与版本指纹**：
  - 每个字段和指标拆分成规范化的搜索文本单元，计算文本 SHA-256 摘要作为 `payload_hash`；
  - 将嵌入模型名称、向量维度与文本预处理逻辑版本拼接生成 `embedding_revision`；
  - 差量计算引擎比对既有文档与新文档：仅当搜索正文文本或 `embedding_revision` 发生变化时，才调用远程嵌入模型计算 1536/1024 维向量；若仅修改了别名或非向量化元数据，直接复用旧向量。
- **CAS 条件更新推进 index_version**：
  - Elasticsearch 写入成功后，`MetaPGRepo` 采用条件更新语句 `UPDATE ... SET index_version = :new_version WHERE index_version < :new_version`；
  - 当并发存在多个不同版本的索引任务时，旧版本任务的回写操作被 CAS 机制自动忽略，防止慢任务覆盖新版本。
- **增量调度与周期自愈（Repair）**：
  - 目录修改成功后即时向 Celery 的 `metadata-index` 队列投递轻量级增量同步任务；
  - Celery Beat 定期调度全局修复任务，扫描 PostgreSQL 中所有满足 `index_version < meta_version` 的落后资源进行幂等补齐，自动自愈 Worker 崩溃、网络抖动或临时 ES 不可用导致的同步落后。

### 4. 字段值（Value Index）采样与双重同步

大模型生成准确 SQL 的关键之一是理解高基数字段的具体取值（例如状态字段 `'WAIT_BUYER_PAY'`）。

- **持久化同步运行状态（ValueIndexSyncState）**：
  - 数据库表 `value_index_sync_state` 记录表字段级别采样同步状态，包含 `active_run_id`、`current_generation`、`active_generation`、`cursor_value`、`status`（`syncing`、`succeeded`、`failed`）、`last_full_synced_at`、`last_incremental_synced_at` 与错误信息。
- **全量同步（Full Sync）与代次切换**：
  - 全量同步生成全新的 `current_generation` UUID，在 Elasticsearch 中写入带此 generation 标记的独立采样值文档；
  - 仅当该字段所有分页数据均成功写入 ES 后，才在数据库事务中将 `active_generation` 指向新代次，随后异步删除旧 generation 的残留文档；
  - 若全量同步中途异常终止，旧代次数据保持不变且持续可查，检索可用性不受影响。
- **增量同步（Incremental Sync）**：
  - 基于表中声明的 `value_index_cursor_column`（通常为 `updated_at` 或递增 ID），结合时间戳回溯下界（`lookback_seconds`）读取新增/修改记录；
  - 以 `(table, column, normalized_value)` 的稳定哈希作为 ES 文档 ID，执行幂等 upsert。
- **并发锁与运行租约**：同步任务执行时持有分布式运行租约，相同表字段的并发同步请求直接被拦截；旧任务产生的迟到游标写入禁止回退数据库中的最新同步状态。

### 5. 权限前置受控召回与 RRF 多路融合

语义召回服务 `SemanticResourceRecallService` 是 Explorer 智能体感知元数据的核心通道。

- **前置权限过滤与安全屏障**：
  - 召回请求开始前，必须由 `MetadataAuthorizationFilter` 读取用户的 `AssetAccessPolicy`；
  - 提取当前用户允许访问的白名单数据库、表与字段列表，并作为强制过滤条件注入 Elasticsearch 查询中。未授权的表或字段在检索底层直接被裁剪，杜绝通过语义召回探测未授权敏感表名。
- **三类资源双通道混合检索**：
  - 针对字段、指标与字段值分别执行两路并行检索：Elasticsearch BM25 全文关键词匹配通道与 Dense Vector kNN 向量最近邻通道；
  - 检索结果记录命中的通道标记（`text`、`vector`）与具体匹配原因；
  - 使用倒数排名融合算法（RRF，Reciprocal Rank Fusion）在同类型资源内计算综合得分：
    $$\text{RRF Score} = \sum_{m \in \{\text{text}, \text{vector}\}} \frac{1}{60 + \text{rank}_m}$$
- **降级容灾与并发控制**：
  - 当向量化服务或某一路检索出现网络超时或故障时，系统自动降级并将该部分标记为 `partial`，保留并返回成功通道的结果，防止整个问数流程因外部弱依赖挂起；
  - 进程内通过 `asyncio.Semaphore` 严格限制并发查询 Elasticsearch 与 Embedding 服务的协程数量。
- **实时事实源二次校验**：从 Elasticsearch 召回候选集后，必须回查 PostgreSQL 当前元数据事实：
  - 核对召回文档的 `meta_version` 是否与数据库一致；
  - 再次核对资产实时权限状态，直接剔除在索引同步窗口期间被删除或被回收权限的过期文档。

### 6. 会话语义快照（SemanticRecallSnapshot）

- **快照持久化机制**：
  - 表 `semantic_recall_snapshots` 以 `(user_id, conversation_id, recall_id)` 为联合主键，保存单次召回的请求参数、响应体、查询业务键 `query`、来源查询列表与关联的查询经验；
  - 单一会话并发执行相同语义 query 时，通过 PostgreSQL 咨询锁合并为一个权威快照（Canonical Snapshot），避免重复检索与资源浪费。
- **白名单投影模型上下文**：向大模型构造 Explorer 提示词时，仅输出脱敏与格式化后的表名、字段名、数据类型、业务说明与采样样例，严格过滤内部版本号、同步状态、ES 文档 ID 等底层实现细节。
- **跨模块失效通知**：元数据目录发生更新或删除时，级联向 Query 模块发出资产变更通知，使所有引用了相关表或字段的既有查询经验（QueryExperience）标记为失效，防止大模型复用基于过时字段定义生成的 SQL 语句。

---

## 核心实现代码与模块架构

### 1. 业务元数据目录 ORM 模型实现

文件路径：`app/metadata/models/catalog.py`

定义表信息、字段信息、取值同步状态、指标与字段指标关联：

```python
# app/metadata/models/catalog.py
"""元数据目录模型。"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, TypedDict
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import MetaBase


class ColumnReference(TypedDict):
    """字段联合主键引用。"""

    t_name: str
    c_name: str


type ColumnKey = tuple[str, str]
COLUMN_EXAMPLE_LIMIT = 10


def column_reference_key(reference: ColumnReference) -> ColumnKey:
    """将字段引用转换为联合键。"""
    return reference["t_name"], reference["c_name"]


def column_key_reference(key: ColumnKey) -> ColumnReference:
    """将联合键转换为字段引用。"""
    return ColumnReference(t_name=key[0], c_name=key[1])


def serialize_column_examples(examples: list[Any]) -> list[Any]:
    """将字段示例转换为统一的可序列化值。"""
    serialized: list[Any] = []
    for value in examples:
        if isinstance(value, (datetime, date)):
            serialized.append(value.isoformat())
        elif isinstance(value, Decimal):
            serialized.append(float(value))
        else:
            serialized.append(value)
    return sorted(serialized, key=str)


def _version_column(default: int, comment: str) -> Mapped[int]:
    return mapped_column(
        Integer,
        nullable=False,
        default=default,
        server_default=text(str(default)),
        comment=comment,
    )


class TableInfo(MetaBase):
    """表元数据。"""

    __tablename__ = "table_info"

    name: Mapped[str] = mapped_column(String(256), primary_key=True, comment="表名称")
    role: Mapped[str] = mapped_column(String(256), nullable=False, comment="表类型(fact/dim)")
    primary_key_columns: Mapped[list[str]] = mapped_column(JSON, nullable=False, comment="主键字段")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="表描述")
    value_index_cursor_column: Mapped[str | None] = mapped_column(String(256), nullable=True)
    meta_version: Mapped[int] = _version_column(1, "元数据版本")

    def metadata_snapshot(self) -> tuple[Any, ...]:
        """提取纯业务内容快照，用于判断版本是否需要递增。"""
        return (
            self.role,
            self.primary_key_columns,
            self.description,
            self.value_index_cursor_column,
        )


class ColumnInfo(MetaBase):
    """字段元数据。"""

    __tablename__ = "column_info"
    __allow_unmapped__ = True

    __table_args__ = (
        ForeignKeyConstraint(
            ["reference_t_name", "reference_c_name"],
            ["column_info.t_name", "column_info.name"],
            ondelete="SET NULL",
        ),
    )

    t_name: Mapped[str] = mapped_column(
        String(256), ForeignKey("table_info.name", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(256), primary_key=True)
    type: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    examples: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    alias: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    index_values: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reference_t_name: Mapped[str | None] = mapped_column(String(256))
    reference_c_name: Mapped[str | None] = mapped_column(String(256))
    meta_version: Mapped[int] = _version_column(1, "元数据版本")
    index_version: Mapped[int] = _version_column(0, "语义索引版本")
    value_index_state: "ValueIndexSyncState | None" = None

    def metadata_snapshot(self) -> tuple[Any, ...]:
        return (
            self.type,
            self.description,
            self.examples,
            self.alias,
            self.index_values,
            self.reference_t_name,
            self.reference_c_name,
        )


class ValueIndexSyncState(MetaBase):
    """字段取值采样同步状态。"""

    __tablename__ = "value_index_sync_state"
    __table_args__ = (
        ForeignKeyConstraint(
            ["t_name", "c_name"],
            ["column_info.t_name", "column_info.name"],
            ondelete="CASCADE",
        ),
    )

    t_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    c_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    cursor_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    active_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    current_generation: Mapped[UUID | None] = mapped_column(Uuid)
    active_generation: Mapped[UUID | None] = mapped_column(Uuid)
    last_incremental_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MetricInfo(MetaBase):
    """业务指标元数据。"""

    __tablename__ = "metric_info"
    __allow_unmapped__ = True

    name: Mapped[str] = mapped_column(String(256), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    alias: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    meta_version: Mapped[int] = _version_column(1, "元数据版本")
    index_version: Mapped[int] = _version_column(0, "语义索引版本")
    relevant_columns: list[ColumnReference]

    def __init__(
        self,
        *,
        name: str,
        description: str,
        alias: list[str],
        relevant_columns: list[ColumnReference] | None = None,
        meta_version: int = 1,
        index_version: int = 0,
    ) -> None:
        self.name = name
        self.description = description
        self.alias = alias
        self.relevant_columns = relevant_columns or []
        self.meta_version = meta_version
        self.index_version = index_version

    def metadata_snapshot(self) -> tuple[Any, ...]:
        return (
            self.description,
            tuple(sorted(column_reference_key(ref) for ref in self.relevant_columns)),
            self.alias,
        )


class ColumnMetric(MetaBase):
    """字段与指标的多对多从属关系。"""

    __tablename__ = "column_metric"
    __table_args__ = (
        ForeignKeyConstraint(
            ["t_name", "c_name"],
            ["column_info.t_name", "column_info.name"],
            ondelete="CASCADE",
        ),
    )

    t_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    c_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    metric_name: Mapped[str] = mapped_column(
        String(256), ForeignKey("metric_info.name", ondelete="CASCADE"), primary_key=True
    )
```

### 2. 会话语义快照 ORM 模型实现

文件路径：`app/metadata/models/recall.py`

```python
# app/metadata/models/recall.py
"""语义召回记录持久化快照模型。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import MetaBase


class SemanticRecallSnapshot(MetaBase):
    """语义召回持久化快照。"""

    __tablename__ = "semantic_recall_snapshots"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    recall_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    query: Mapped[str] = mapped_column(String(1000), nullable=False)
    request: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_queries: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

### 3. 元数据目录管理服务实现

文件路径：`app/metadata/services/catalog.py`

校验 Doris 物理结构、外键依赖与版本推进：

```python
# app/metadata/services/catalog.py（核心实现）
"""元数据目录管理服务。"""

from app.metadata import errors as meta_error
from app.metadata.models.catalog import (
    ColumnInfo,
    MetricInfo,
    TableInfo,
    serialize_column_examples,
)
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.contracts import (
    MetadataAssetInvalidator,
    MetadataSemanticIndexScheduler,
)
from app.metadata.services.index import MetaIndexService


class MetaCatalogService:
    """管理表、字段和指标元数据事实。"""

    def __init__(
        self,
        meta_repo: MetaPGRepo,
        source_repo: SourceDorisRepo,
        meta_index_service: MetaIndexService,
        asset_invalidator: MetadataAssetInvalidator,
        semantic_index_scheduler: MetadataSemanticIndexScheduler,
    ) -> None:
        self._meta_repo = meta_repo
        self._source_repo = source_repo
        self._meta_index_service = meta_index_service
        self._asset_invalidator = asset_invalidator
        self._semantic_index_scheduler = semantic_index_scheduler

    async def upsert_table_info(
        self,
        t_name: str,
        role: str,
        description: str,
        value_index_cursor_column: str | None = None,
    ) -> None:
        """新增或更新表元数据（校验物理表存在）。"""
        if not await self._source_repo.table_exists(t_name):
            raise meta_error.InvalidMetadataError(detail=f"源表不存在: {t_name}")
        primary_key_columns = await self._source_repo.get_primary_key_columns(t_name)
        column_types = await self._source_repo.get_column_types(t_name)
        if (
            value_index_cursor_column is not None
            and value_index_cursor_column not in column_types
        ):
            raise meta_error.InvalidMetadataError(
                detail=f"源表中未找到游标字段: {t_name}.{value_index_cursor_column}"
            )
        async with self._meta_repo.session.begin():
            changed = await self._meta_repo.upsert_table_info(
                TableInfo(
                    name=t_name,
                    role=role,
                    primary_key_columns=primary_key_columns,
                    description=description,
                    value_index_cursor_column=value_index_cursor_column,
                )
            )
        if changed:
            await self._asset_invalidator.invalidate_table(t_name)

    async def upsert_column_info(
        self,
        t_name: str,
        c_name: str,
        description: str,
        alias: list[str],
        index_values: bool = False,
        reference_t_name: str | None = None,
        reference_c_name: str | None = None,
    ) -> None:
        """新增或更新字段元数据（核对类型与外键引用合法性）。"""
        if not await self._source_repo.column_exists(t_name, c_name):
            raise meta_error.InvalidMetadataError(detail=f"源字段不存在: {t_name}.{c_name}")
        column_type = await self._source_repo.get_column_type(t_name, c_name)
        raw_examples = await self._source_repo.get_column_examples(t_name, c_name, limit=10)
        serialized_examples = serialize_column_examples(raw_examples)

        async with self._meta_repo.session.begin():
            # 校验外键引用目标在业务目录中已存在
            if reference_t_name and reference_c_name:
                ref_col = await self._meta_repo.get_column_info(
                    reference_t_name, reference_c_name
                )
                if not ref_col:
                    raise meta_error.InvalidMetadataError(
                        detail=f"引用目标字段不存在: {reference_t_name}.{reference_c_name}"
                    )

            changed = await self._meta_repo.upsert_column_info(
                ColumnInfo(
                    t_name=t_name,
                    name=c_name,
                    type=column_type,
                    description=description,
                    examples=serialized_examples,
                    alias=alias,
                    index_values=index_values,
                    reference_t_name=reference_t_name,
                    reference_c_name=reference_c_name,
                )
            )
        if changed:
            await self._asset_invalidator.invalidate_column(t_name, c_name)
            await self._semantic_index_scheduler.schedule_column_index(t_name, c_name)
```

### 4. 批量导入服务与差异比对实现

文件路径：`app/metadata/services/import_service.py`

```python
# app/metadata/services/import_service.py（核心实现）
"""元数据批量导入服务。"""

from dataclasses import dataclass
from enum import StrEnum
from app.metadata import errors as meta_error
from app.metadata.config import MetaConfig
from app.metadata.models.catalog import ColumnKey
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo


class ImportMode(StrEnum):
    MERGE = "merge"
    REPLACE = "replace"


@dataclass(frozen=True)
class ResourceChanges[T]:
    created: list[T]
    updated: list[T]
    deleted: list[T]


@dataclass(frozen=True)
class MetaImportResult:
    mode: ImportMode
    dry_run: bool
    tables: ResourceChanges[str]
    columns: ResourceChanges[ColumnKey]
    metrics: ResourceChanges[str]


class MetaImportService:
    """批量导入元数据引擎。"""

    def __init__(self, meta_repo: MetaPGRepo, source_repo: SourceDorisRepo) -> None:
        self._meta_repo = meta_repo
        self._source_repo = source_repo

    async def compute_diff(
        self,
        meta_config: MetaConfig,
        mode: ImportMode,
    ) -> tuple[ResourceChanges[str], ResourceChanges[ColumnKey], ResourceChanges[str]]:
        """纯只读计算配置与既有事实之间的差异。"""
        existing_tables = {t.name: t for t in await self._meta_repo.list_table_infos()}
        existing_columns = {
            (c.t_name, c.name): c for c in await self._meta_repo.list_column_infos()
        }
        existing_metrics = {m.name: m for m in await self._meta_repo.list_metric_infos()}

        # 比对表
        target_tables = {t.name: t for t in meta_config.tables}
        tbl_created = [name for name in target_tables if name not in existing_tables]
        tbl_updated = [
            name
            for name, t in target_tables.items()
            if name in existing_tables
            and t.metadata_snapshot() != existing_tables[name].metadata_snapshot()
        ]
        tbl_deleted = (
            [name for name in existing_tables if name not in target_tables]
            if mode == ImportMode.REPLACE
            else []
        )

        # 比对字段与指标类似...
        return (
            ResourceChanges(tbl_created, tbl_updated, tbl_deleted),
            ResourceChanges([], [], []),
            ResourceChanges([], [], []),
        )
```

### 5. 受控语义召回服务与 RRF 融合实现

文件路径：`app/metadata/services/recall.py`

```python
# app/metadata/services/recall.py（核心融合算法片段）
"""语义召回服务与 RRF 融合。"""

from collections.abc import Callable
from app.metadata.models.search import (
    SemanticColumnRecallResult,
    SemanticMetricRecallResult,
    SemanticResourceRecallResponse,
)


def _group_items[ItemT, KeyT](
    groups: list[list[ItemT]],
    key: Callable[[ItemT], KeyT],
) -> list[list[ItemT]]:
    """按唯一标识分组匹配项。"""
    grouped: dict[KeyT, list[ItemT]] = {}
    for items in groups:
        for item in items:
            grouped.setdefault(key(item), []).append(item)
    return list(grouped.values())


def rrf_score(rank: int, k: int = 60) -> float:
    """计算单通道倒数排名融合得分。"""
    return 1.0 / (k + rank)


def merge_recalled_columns(
    responses: list[SemanticResourceRecallResponse],
) -> list[SemanticColumnRecallResult]:
    """按 (t_name, c_name) 稳定融合字段召回结果，并保留最新版本元数据。"""
    result: list[SemanticColumnRecallResult] = []
    grouped = _group_items(
        [r.columns for r in responses],
        lambda item: (item.t_name, item.name),
    )
    for matches in grouped:
        # 保留元数据版本最高的最新事实，并累加多通道 RRF 分数
        best_match = max(matches, key=lambda x: (x.meta_version, x.rank_score or 0.0))
        combined_score = sum(item.rank_score or 0.0 for item in matches)
        merged = best_match.model_copy(deep=True)
        merged.rank_score = combined_score
        result.append(merged)
    return sorted(result, key=lambda x: (-x.rank_score, x.t_name, x.name))
```

---

## 阶段学习与验证要点

### 阶段 1：验证业务目录物理约束与版本推进

1. **物理真实性校验验证**：调用 `upsert_table_info` 尝试增加一个 Doris 中不存在的物理表名，验证系统立即抛出 `InvalidMetadataError` 拒绝写入。
2. **业务快照无变化不增版本验证**：连续两次以相同内容调用 `upsert_column_info`，验证第二次执行后数据库中的 `meta_version` 维持原值不自增。
3. **字段业务描述修改推进版本验证**：修改字段 `description`，验证更新后 `meta_version` 从 1 递增为 2。

### 阶段 2：验证 YAML 导入 Dry-Run 与原子性

1. **Dry-Run 纯只读验证**：在 `meta_config.yaml` 中增加一张新表，设置 `dry_run=True` 调用批量导入接口，验证返回的 `tables.created` 包含该表名，但数据库中实际未插入任何数据。
2. **预检失败整体回滚验证**：在 YAML 中故意引入一个不存在的物理字段，执行真实导入，验证整个事务报错回滚，合法表也未被部分写入。

### 阶段 3：验证权限前置召回与多路融合

1. **未授权资源过滤验证**：配置用户仅有表 A 权限，以表 B 的特征关键词进行语义召回，验证 Elasticsearch 查询条件前置裁剪了表 B，召回结果完全不包含表 B。
2. **多路融合得分验证**：验证同时命中全文和向量通道的资源，其最终 RRF 得分高于仅命中单一通道的资源。
3. **会话快照持久化验证**：发起语义检索，验证 `semantic_recall_snapshots` 表成功写入记录，且相同 query 的并发请求能够合并为同一快照。
