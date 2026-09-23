"""PostgreSQL 元数据访问。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.metadata import errors as meta_error
from app.metadata.models.catalog import (
    ColumnInfo,
    ColumnMetric,
    ColumnReference,
    MetricInfo,
    TableInfo,
    ValueIndexSyncState,
    column_key_reference,
)
from app.metadata.models.recall import SemanticRecallSnapshot


class MetaPGRepo:
    """PostgreSQL 元数据存储。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化元数据存储。"""
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """返回当前存储绑定的数据库会话。"""
        return self._session

    async def replace_catalog(
        self,
        tables: list[TableInfo],
        columns: list[ColumnInfo],
        metrics: list[MetricInfo],
    ) -> None:
        """在调用方事务内清空元数据、同步状态和召回快照，写入完整新目录。"""
        for model in (
            SemanticRecallSnapshot,
            ValueIndexSyncState,
            ColumnMetric,
            ColumnInfo,
            MetricInfo,
            TableInfo,
        ):
            await self._session.execute(delete(model))
        self._session.add_all(tables)
        await self._session.flush()
        references = [
            (item, item.reference_t_name, item.reference_c_name) for item in columns
        ]
        for item, _, _ in references:
            item.reference_t_name = None
            item.reference_c_name = None
        self._session.add_all(columns)
        await self._session.flush()
        for item, table_name, column_name in references:
            item.reference_t_name = table_name
            item.reference_c_name = column_name
        self._session.add_all(metrics)
        await self._session.flush()
        self._session.add_all(
            [
                ColumnMetric(
                    metric_name=metric.name,
                    t_name=reference["t_name"],
                    c_name=reference["c_name"],
                )
                for metric in metrics
                for reference in metric.relevant_columns
            ]
        )
        await self._session.flush()

    async def acquire_index_lock(self, resource_type: str, resource_key: str) -> None:
        """在当前事务中获取索引资源级互斥锁。"""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"metadata-index:{resource_type}:{resource_key}"},
        )

    async def mark_column_indexed(self, t_name: str, c_name: str) -> None:
        """完整索引写入成功后标记字段索引就绪。"""
        await self._session.execute(
            update(ColumnInfo)
            .where(ColumnInfo.t_name == t_name, ColumnInfo.name == c_name)
            .values(index_version=ColumnInfo.meta_version)
        )

    async def mark_metric_indexed(self, metric_name: str) -> None:
        """完整索引写入成功后标记指标索引就绪。"""
        await self._session.execute(
            update(MetricInfo)
            .where(MetricInfo.name == metric_name)
            .values(index_version=MetricInfo.meta_version)
        )

    async def list_table_infos(self) -> list[TableInfo]:
        """获取全部表信息。"""
        result = await self._session.scalars(select(TableInfo).order_by(TableInfo.name))
        return list(result.all())

    async def list_column_infos(self) -> list[ColumnInfo]:
        """获取全部字段信息。"""
        result = await self._session.scalars(
            select(ColumnInfo).order_by(ColumnInfo.t_name, ColumnInfo.name)
        )
        column_infos = list(result.all())
        await self._load_column_value_states(column_infos)
        return column_infos

    async def list_metric_infos(self) -> list[MetricInfo]:
        """获取全部指标信息。"""
        result = await self._session.scalars(
            select(MetricInfo).order_by(MetricInfo.name)
        )
        metric_infos = list(result.all())
        await self._load_metric_references(metric_infos)
        return metric_infos

    async def get_value_index_state(
        self,
        t_name: str,
        c_name: str,
    ) -> ValueIndexSyncState | None:
        """获取字段取值索引同步状态。"""
        return await self._session.get(ValueIndexSyncState, (t_name, c_name))

    async def reload_value_index_context(
        self,
        t_name: str,
        c_name: str,
    ) -> tuple[ColumnInfo, TableInfo]:
        """绕过会话缓存，重新读取取值索引配置与运行状态。"""
        result = await self._session.execute(
            select(ColumnInfo, TableInfo, ValueIndexSyncState)
            .join(TableInfo, TableInfo.name == ColumnInfo.t_name)
            .outerjoin(
                ValueIndexSyncState,
                (ValueIndexSyncState.t_name == ColumnInfo.t_name)
                & (ValueIndexSyncState.c_name == ColumnInfo.name),
            )
            .where(
                ColumnInfo.t_name == t_name,
                ColumnInfo.name == c_name,
            )
            # 会话禁用了 expire_on_commit；终态校验必须覆盖第一阶段的缓存实体。
            .execution_options(populate_existing=True)
        )
        row = result.one_or_none()
        if row is None:
            raise meta_error.MetadataNotFoundError(
                detail=f"未找到字段元数据: {t_name}.{c_name}"
            )
        column_info, table_info, state = row
        column_info.value_index_state = state
        return column_info, table_info

    async def begin_value_index_sync(
        self,
        t_name: str,
        c_name: str,
        *,
        run_id: UUID,
        generation: UUID | None,
        started_at: datetime,
    ) -> ValueIndexSyncState:
        """登记当前字段取值索引运行所有权。"""
        state = await self.get_value_index_state(t_name, c_name)
        if state is None:
            state = ValueIndexSyncState(
                t_name=t_name,
                c_name=c_name,
                cursor_value=None,
                status="syncing",
                active_run_id=run_id,
                current_generation=None,
                active_generation=generation,
                last_incremental_synced_at=None,
                last_full_synced_at=None,
                last_error=None,
                updated_at=started_at,
            )
            self._session.add(state)
        else:
            state.status = "syncing"
            state.active_run_id = run_id
            state.active_generation = generation
            state.last_error = None
            state.updated_at = started_at
        await self._session.flush()
        return state

    async def complete_value_index_sync(
        self,
        t_name: str,
        c_name: str,
        *,
        run_id: UUID,
        cursor_value: dict[str, object] | None,
        generation: UUID,
        completed_at: datetime,
        full_sync: bool,
        incremental_sync: bool,
    ) -> bool:
        """由当前运行提交水位、代次和成功时间。"""
        values: dict[str, object] = {
            "cursor_value": cursor_value,
            "status": "succeeded",
            "active_run_id": None,
            "current_generation": generation,
            "active_generation": None,
            "last_error": None,
            "updated_at": completed_at,
        }
        if full_sync:
            values["last_full_synced_at"] = completed_at
        if incremental_sync:
            values["last_incremental_synced_at"] = completed_at
        result = await self._session.execute(
            update(ValueIndexSyncState)
            .where(
                ValueIndexSyncState.t_name == t_name,
                ValueIndexSyncState.c_name == c_name,
                ValueIndexSyncState.active_run_id == run_id,
            )
            .values(**values)
            .returning(ValueIndexSyncState.c_name)
        )
        return result.scalar_one_or_none() is not None

    async def fail_value_index_sync(
        self,
        t_name: str,
        c_name: str,
        *,
        run_id: UUID,
        error: str,
        failed_at: datetime,
    ) -> bool:
        """由当前运行记录字段取值索引失败状态。"""
        result = await self._session.execute(
            update(ValueIndexSyncState)
            .where(
                ValueIndexSyncState.t_name == t_name,
                ValueIndexSyncState.c_name == c_name,
                ValueIndexSyncState.active_run_id == run_id,
            )
            .values(
                status="failed",
                active_run_id=None,
                active_generation=None,
                last_error=error[:4000],
                updated_at=failed_at,
            )
            .returning(ValueIndexSyncState.c_name)
        )
        return result.scalar_one_or_none() is not None

    async def _load_column_value_states(
        self,
        column_infos: list[ColumnInfo],
    ) -> None:
        """批量加载字段取值索引同步状态。"""
        if not column_infos:
            return
        keys = [(item.t_name, item.name) for item in column_infos]
        result = await self._session.scalars(
            select(ValueIndexSyncState).where(
                tuple_(ValueIndexSyncState.t_name, ValueIndexSyncState.c_name).in_(keys)
            )
        )
        states = {(item.t_name, item.c_name): item for item in result.all()}
        for column_info in column_infos:
            column_info.value_index_state = states.get(
                (column_info.t_name, column_info.name)
            )

    async def _load_metric_references(self, metric_infos: list[MetricInfo]) -> None:
        """加载指标关联字段。"""
        references_by_metric: dict[str, list[ColumnReference]] = {
            metric_info.name: [] for metric_info in metric_infos
        }
        if not references_by_metric:
            return
        result = await self._session.scalars(
            select(ColumnMetric)
            .where(ColumnMetric.metric_name.in_(references_by_metric))
            .order_by(
                ColumnMetric.metric_name,
                ColumnMetric.t_name,
                ColumnMetric.c_name,
            )
        )
        for relation in result:
            references_by_metric[relation.metric_name].append(
                column_key_reference((relation.t_name, relation.c_name))
            )
        for metric_info in metric_infos:
            metric_info.relevant_columns = references_by_metric[metric_info.name]

    async def get_column_info(self, t_name: str, c_name: str) -> ColumnInfo:
        """根据表名和字段名获取字段信息。"""
        result = await self._session.get(ColumnInfo, (t_name, c_name))
        if result:
            result.value_index_state = await self.get_value_index_state(t_name, c_name)
            return result
        raise meta_error.MetadataNotFoundError(
            detail=f"未找到字段元数据: {t_name}.{c_name}"
        )

    async def get_table_info(self, t_name: str) -> TableInfo:
        """根据表名获取表信息。"""
        result = await self._session.get(TableInfo, t_name)
        if result:
            return result
        raise meta_error.MetadataNotFoundError(detail=f"未找到表元数据: {t_name}")

    async def get_metric_info(self, metric_name: str) -> MetricInfo:
        """根据指标名获取指标信息。"""
        result = await self._session.get(MetricInfo, metric_name)
        if result:
            await self._load_metric_references([result])
            return result
        raise meta_error.MetadataNotFoundError(
            detail=f"未找到指标元数据: {metric_name}"
        )
