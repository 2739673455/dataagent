"""PostgreSQL 元数据访问"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.metadata import errors as meta_error
from app.metadata.models import (
    ColumnInfo,
    ColumnMetric,
    ColumnReference,
    MetricInfo,
    TableInfo,
    ValueIndexSyncState,
    default_value_index_sync_config,
)
from app.shared.config.meta_config import ValueIndexSyncConfig


class MetaPGRepo:
    """PostgreSQL 元数据存储"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化元数据存储"""
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """返回当前存储绑定的数据库会话"""
        return self._session

    async def upsert_table_info(
        self,
        table_info: TableInfo,
        *,
        force_version_increment: bool = False,
    ) -> bool:
        """新增或更新表信息"""
        existing = await self._session.get(TableInfo, table_info.name)
        changed = force_version_increment or (
            table_info.metadata_snapshot() != existing.metadata_snapshot()
            if existing
            else True
        )
        value_sync_config_changed = existing is not None and (
            existing.value_index_sync or default_value_index_sync_config()
        ) != (table_info.value_index_sync or default_value_index_sync_config())
        self._set_versions(
            table_info,
            existing,
            changed,
        )
        await self._session.merge(table_info)
        if value_sync_config_changed:
            await self._session.execute(
                delete(ValueIndexSyncState).where(
                    ValueIndexSyncState.t_name == table_info.name
                )
            )
        return changed

    async def upsert_column_info(
        self,
        column_info: ColumnInfo,
        *,
        force_version_increment: bool = False,
    ) -> bool:
        """新增或更新字段信息"""
        changed = await self._prepare_column_versions(
            column_info,
            force_version_increment,
        )
        await self._session.merge(column_info)
        return changed

    async def upsert_column_infos(
        self,
        column_infos: list[ColumnInfo],
        force_version_increment_keys: set[tuple[str, str]] | None = None,
    ) -> None:
        """批量写入字段信息并在目标字段创建后设置引用"""
        if not column_infos:
            return
        force_version_increment_keys = force_version_increment_keys or set()
        for column_info in column_infos:
            await self._prepare_column_versions(
                column_info,
                (column_info.t_name, column_info.name) in force_version_increment_keys,
            )
        references = [
            (
                column_info,
                column_info.reference_t_name,
                column_info.reference_c_name,
            )
            for column_info in column_infos
        ]
        with self._session.no_autoflush:
            for column_info, _, _ in references:
                column_info.reference_t_name = None
                column_info.reference_c_name = None
                await self._session.merge(column_info)
        await self._session.flush()
        with self._session.no_autoflush:
            for column_info, reference_t_name, reference_c_name in references:
                column_info.reference_t_name = reference_t_name
                column_info.reference_c_name = reference_c_name
                await self._session.merge(column_info)

    async def upsert_metric_info(
        self,
        metric_info: MetricInfo,
        *,
        force_version_increment: bool = False,
    ) -> bool:
        """新增或更新指标信息及字段关联"""
        existing = await self._session.get(MetricInfo, metric_info.name)
        if existing:
            await self._load_metric_references([existing])
        changed = force_version_increment or (
            metric_info.metadata_snapshot() != existing.metadata_snapshot()
            if existing
            else True
        )
        self._set_versions(
            metric_info,
            existing,
            changed,
        )
        await self._session.merge(metric_info)
        await self._session.execute(
            delete(ColumnMetric).where(ColumnMetric.metric_name == metric_info.name)
        )
        self._session.add_all(
            [
                ColumnMetric(
                    t_name=reference["t_name"],
                    c_name=reference["c_name"],
                    metric_name=metric_info.name,
                )
                for reference in metric_info.relevant_columns
            ]
        )
        return changed

    async def acquire_index_lock(self, resource_type: str, resource_key: str) -> None:
        """在当前事务中获取索引资源级互斥锁"""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"metadata-index:{resource_type}:{resource_key}"},
        )

    async def mark_column_indexed_if_current(
        self,
        t_name: str,
        c_name: str,
        target_version: int,
    ) -> bool:
        """元数据版本未变化时确认字段语义索引版本"""
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

    async def mark_metric_indexed_if_current(
        self,
        metric_name: str,
        target_version: int,
    ) -> bool:
        """元数据版本未变化时确认指标语义索引版本"""
        result = await self._session.execute(
            update(MetricInfo)
            .where(
                MetricInfo.name == metric_name,
                MetricInfo.meta_version == target_version,
            )
            .values(index_version=target_version)
            .returning(MetricInfo.name)
        )
        return result.scalar_one_or_none() is not None

    async def list_table_infos(self) -> list[TableInfo]:
        """获取全部表信息"""
        result = await self._session.scalars(select(TableInfo).order_by(TableInfo.name))
        return list(result.all())

    async def list_column_infos(self) -> list[ColumnInfo]:
        """获取全部字段信息"""
        result = await self._session.scalars(
            select(ColumnInfo).order_by(ColumnInfo.t_name, ColumnInfo.name)
        )
        column_infos = list(result.all())
        await self._load_column_value_states(column_infos)
        return column_infos

    async def list_column_infos_by_table_names(
        self,
        table_names: list[str],
        *,
        index_values: bool | None = None,
    ) -> list[ColumnInfo]:
        """根据多个表名获取字段信息"""
        unique_table_names = list(dict.fromkeys(table_names))
        if not unique_table_names:
            return []
        statement = select(ColumnInfo).where(ColumnInfo.t_name.in_(unique_table_names))
        if index_values is not None:
            statement = statement.where(ColumnInfo.index_values.is_(index_values))
        result = await self._session.scalars(
            statement.order_by(ColumnInfo.t_name, ColumnInfo.name)
        )
        column_infos = list(result.all())
        await self._load_column_value_states(column_infos)
        return column_infos

    async def list_metric_infos(self) -> list[MetricInfo]:
        """获取全部指标信息"""
        result = await self._session.scalars(
            select(MetricInfo).order_by(MetricInfo.name)
        )
        metric_infos = list(result.all())
        await self._load_metric_references(metric_infos)
        return metric_infos

    async def claim_pending_value_index_keys(
        self,
        *,
        now: datetime,
        stale_before: datetime,
        limit: int,
    ) -> list[tuple[str, str]]:
        """领取每日增量同步或需要清理的取值索引字段"""
        await self.acquire_index_lock("scheduler", "value-index-dispatch")
        result = await self._session.execute(
            select(ColumnInfo, TableInfo, ValueIndexSyncState)
            .join(TableInfo, TableInfo.name == ColumnInfo.t_name)
            .outerjoin(
                ValueIndexSyncState,
                (ValueIndexSyncState.t_name == ColumnInfo.t_name)
                & (ValueIndexSyncState.c_name == ColumnInfo.name),
            )
            .order_by(
                ValueIndexSyncState.updated_at.asc().nulls_first(),
                ColumnInfo.t_name,
                ColumnInfo.name,
            )
        )
        pending: list[tuple[str, str]] = []
        for column_info, table_info, state in result.tuples():
            if not column_info.index_values:
                if state is not None and (
                    state.status != "syncing" or state.updated_at <= stale_before
                ):
                    pending.append((column_info.t_name, column_info.name))
                    state.status = "syncing"
                    state.active_run_id = None
                    state.active_generation = None
                    state.updated_at = now
                if len(pending) >= limit:
                    break
                continue
            config = ValueIndexSyncConfig.model_validate(table_info.value_index_sync)
            if (
                config.cursor_column is None
                or state is None
                or state.current_generation is None
                or state.cursor_value is None
            ):
                continue
            due = (state.status == "failed" and state.updated_at < now) or (
                state.status == "succeeded"
                and (
                    state.last_incremental_synced_at is None
                    or state.last_incremental_synced_at < now
                )
            ) or (
                state.status == "syncing"
                and state.updated_at <= stale_before
            )
            if due:
                pending.append((column_info.t_name, column_info.name))
                state.status = "syncing"
                state.active_run_id = None
                state.active_generation = None
                state.updated_at = now
            if len(pending) >= limit:
                break
        await self._session.flush()
        return pending

    async def fail_value_index_claims(
        self,
        column_keys: list[tuple[str, str]],
        *,
        error: str,
        failed_at: datetime,
    ) -> None:
        """释放发布失败且尚未开始运行的取值索引任务"""
        if not column_keys:
            return
        await self._session.execute(
            update(ValueIndexSyncState)
            .where(
                tuple_(
                    ValueIndexSyncState.t_name,
                    ValueIndexSyncState.c_name,
                ).in_(column_keys),
                ValueIndexSyncState.status == "syncing",
                ValueIndexSyncState.active_run_id.is_(None),
            )
            .values(
                status="failed",
                last_error=error[:4000],
                updated_at=failed_at,
            )
        )

    async def get_value_index_state(
        self,
        t_name: str,
        c_name: str,
    ) -> ValueIndexSyncState | None:
        """获取字段取值索引同步状态"""
        return await self._session.get(ValueIndexSyncState, (t_name, c_name))

    async def begin_value_index_sync(
        self,
        t_name: str,
        c_name: str,
        *,
        run_id: UUID,
        generation: UUID | None,
        started_at: datetime,
    ) -> ValueIndexSyncState:
        """登记当前字段取值索引运行所有权"""
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
        """由当前运行提交水位、代次和成功时间"""
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
        """由当前运行记录字段取值索引失败状态"""
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

    async def delete_value_index_state(self, t_name: str, c_name: str) -> None:
        """删除字段取值索引同步状态"""
        await self._session.execute(
            delete(ValueIndexSyncState).where(
                ValueIndexSyncState.t_name == t_name,
                ValueIndexSyncState.c_name == c_name,
            )
        )

    async def _load_column_value_states(
        self,
        column_infos: list[ColumnInfo],
    ) -> None:
        """批量加载字段取值索引同步状态"""
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
        """加载指标关联字段"""
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
                ColumnReference(
                    t_name=relation.t_name,
                    c_name=relation.c_name,
                )
            )
        for metric_info in metric_infos:
            metric_info.relevant_columns = references_by_metric[metric_info.name]

    async def delete_metric_infos(self, metric_names: list[str]) -> None:
        """删除指标信息及字段关联"""
        if not metric_names:
            return
        await self._session.execute(
            delete(ColumnMetric).where(ColumnMetric.metric_name.in_(metric_names))
        )
        await self._session.execute(
            delete(MetricInfo).where(MetricInfo.name.in_(metric_names))
        )

    async def delete_column_infos(self, column_keys: list[tuple[str, str]]) -> None:
        """删除字段信息及指标关联"""
        if not column_keys:
            return
        key_columns = tuple_(ColumnMetric.t_name, ColumnMetric.c_name)
        await self._session.execute(
            delete(ColumnMetric).where(key_columns.in_(column_keys))
        )
        info_key_columns = tuple_(ColumnInfo.t_name, ColumnInfo.name)
        await self._session.execute(
            delete(ColumnInfo).where(info_key_columns.in_(column_keys))
        )

    async def delete_table_infos(self, table_names: list[str]) -> None:
        """删除表信息"""
        if not table_names:
            return
        await self._session.execute(
            delete(TableInfo).where(TableInfo.name.in_(table_names))
        )

    async def get_column_info(self, t_name: str, c_name: str) -> ColumnInfo:
        """根据表名和字段名获取字段信息"""
        result = await self._session.get(ColumnInfo, (t_name, c_name))
        if result:
            result.value_index_state = await self.get_value_index_state(t_name, c_name)
            return result
        raise meta_error.MetadataNotFoundError(
            detail=f"未找到字段元数据: {t_name}.{c_name}"
        )

    async def get_table_info(self, t_name: str) -> TableInfo:
        """根据表名获取表信息"""
        result = await self._session.get(TableInfo, t_name)
        if result:
            return result
        raise meta_error.MetadataNotFoundError(detail=f"未找到表元数据: {t_name}")

    async def get_metric_info(self, metric_name: str) -> MetricInfo:
        """根据指标名获取指标信息"""
        result = await self._session.get(MetricInfo, metric_name)
        if result:
            await self._load_metric_references([result])
            return result
        raise meta_error.MetadataNotFoundError(
            detail=f"未找到指标元数据: {metric_name}"
        )

    async def _prepare_column_versions(
        self,
        column_info: ColumnInfo,
        force_version_increment: bool,
    ) -> bool:
        """根据字段元数据变化设置版本"""
        existing = await self._session.get(
            ColumnInfo,
            (column_info.t_name, column_info.name),
        )
        changed = force_version_increment or (
            column_info.metadata_snapshot() != existing.metadata_snapshot()
            if existing
            else True
        )
        self._set_versions(
            column_info,
            existing,
            changed,
        )
        return changed

    @staticmethod
    def _set_versions(
        item: TableInfo | ColumnInfo | MetricInfo,
        existing: TableInfo | ColumnInfo | MetricInfo | None,
        changed: bool,
    ) -> None:
        """设置元数据版本并保留已有索引版本"""
        item.meta_version = (
            1 if existing is None else existing.meta_version + int(changed)
        )
        if isinstance(item, TableInfo):
            return
        if existing is None:
            item.index_version = 0
        elif isinstance(existing, (ColumnInfo, MetricInfo)):
            item.index_version = existing.index_version
        else:
            raise TypeError("元数据实体类型不匹配")
