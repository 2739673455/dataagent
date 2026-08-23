"""PostgreSQL 元数据访问"""

from datetime import UTC, datetime

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.metadata import errors as meta_error
from app.metadata.models import (
    ColumnInfo,
    ColumnMetric,
    ColumnReference,
    MetricInfo,
    TableInfo,
)


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
        self._set_versions(
            table_info,
            existing,
            changed,
        )
        await self._session.merge(table_info)
        return existing is not None and changed

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
    ) -> None:
        """新增或更新指标信息及字段关联"""
        existing = await self._session.get(MetricInfo, metric_info.name)
        if existing:
            await self._load_metric_references([existing])
        self._set_versions(
            metric_info,
            existing,
            force_version_increment
            or (
                metric_info.metadata_snapshot() != existing.metadata_snapshot()
                if existing
                else True
            ),
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

    @staticmethod
    def mark_column_indexed(column_info: ColumnInfo) -> None:
        """记录字段向量同步版本"""
        column_info.index_version = column_info.meta_version

    @staticmethod
    def mark_column_values_syncing(column_info: ColumnInfo) -> None:
        """记录字段值索引正在同步"""
        column_info.value_index_sync_status = "syncing"

    @staticmethod
    def mark_column_values_succeeded(column_info: ColumnInfo) -> None:
        """记录字段值索引同步成功"""
        column_info.value_index_synced_at = datetime.now(UTC)
        column_info.value_index_sync_status = "succeeded"

    @staticmethod
    def mark_column_values_failed(column_info: ColumnInfo) -> None:
        """记录字段值索引同步失败"""
        column_info.value_index_sync_status = "failed"

    @staticmethod
    def mark_metric_indexed(metric_info: MetricInfo) -> None:
        """记录指标向量同步版本"""
        metric_info.index_version = metric_info.meta_version

    async def list_table_infos(self) -> list[TableInfo]:
        """获取全部表信息"""
        result = await self._session.scalars(select(TableInfo).order_by(TableInfo.name))
        return list(result.all())

    async def list_column_infos(self) -> list[ColumnInfo]:
        """获取全部字段信息"""
        result = await self._session.scalars(
            select(ColumnInfo).order_by(ColumnInfo.t_name, ColumnInfo.name)
        )
        return list(result.all())

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
        statement = select(ColumnInfo).where(
            ColumnInfo.t_name.in_(unique_table_names)
        )
        if index_values is not None:
            statement = statement.where(ColumnInfo.index_values.is_(index_values))
        result = await self._session.scalars(
            statement.order_by(ColumnInfo.t_name, ColumnInfo.name)
        )
        return list(result.all())

    async def list_metric_infos(self) -> list[MetricInfo]:
        """获取全部指标信息"""
        result = await self._session.scalars(
            select(MetricInfo).order_by(MetricInfo.name)
        )
        metric_infos = list(result.all())
        await self._load_metric_references(metric_infos)
        return metric_infos

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
        return existing is not None and changed

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
            if isinstance(item, ColumnInfo):
                item.value_index_synced_at = None
                item.value_index_sync_status = None
        elif isinstance(existing, (ColumnInfo, MetricInfo)):
            item.index_version = existing.index_version
            if isinstance(item, ColumnInfo) and isinstance(existing, ColumnInfo):
                item.value_index_synced_at = existing.value_index_synced_at
                item.value_index_sync_status = existing.value_index_sync_status
        else:
            raise TypeError("元数据实体类型不匹配")
