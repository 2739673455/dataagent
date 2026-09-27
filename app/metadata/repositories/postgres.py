"""PostgreSQL 元数据访问。"""

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.metadata import errors as meta_error
from app.metadata.models.catalog import (
    ColumnInfo,
    ColumnMetric,
    ColumnReference,
    MetricInfo,
    TableInfo,
    column_key_reference,
)


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
        """在调用方事务内清空元数据和水位，写入完整新目录。"""
        for model in (
            ColumnMetric,
            ColumnInfo,
            MetricInfo,
            TableInfo,
        ):
            await self._session.execute(delete(model))
        self._session.add_all(tables)
        await self._session.flush()
        # 字段之间可能互相引用，先插入全部字段，再恢复外键引用。
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

    async def update_value_index_cursor(
        self,
        t_name: str,
        c_name: str,
        cursor_value: dict[str, object],
    ) -> None:
        """在调用方事务内保存成功写入索引后的水位。"""
        await self._session.execute(
            update(ColumnInfo)
            .where(ColumnInfo.t_name == t_name, ColumnInfo.name == c_name)
            .values(value_index_cursor_value=cursor_value)
        )

    async def list_table_infos(self) -> list[TableInfo]:
        """获取全部表信息。"""
        result = await self._session.scalars(select(TableInfo).order_by(TableInfo.name))
        return list(result.all())

    async def get_table_info(self, t_name: str) -> TableInfo:
        """根据表名获取表信息。"""
        result = await self._session.get(TableInfo, t_name)
        if result:
            return result
        raise meta_error.MetadataNotFoundError(detail=f"未找到表元数据: {t_name}")

    async def list_column_infos(self) -> list[ColumnInfo]:
        """获取全部字段信息。"""
        result = await self._session.scalars(
            select(ColumnInfo).order_by(ColumnInfo.t_name, ColumnInfo.name)
        )
        return list(result.all())

    async def get_column_info(self, t_name: str, c_name: str) -> ColumnInfo:
        """根据表名和字段名获取字段信息。"""
        result = await self._session.get(ColumnInfo, (t_name, c_name))
        if result:
            return result
        raise meta_error.MetadataNotFoundError(
            detail=f"未找到字段元数据: {t_name}.{c_name}"
        )

    async def list_metric_infos(self) -> list[MetricInfo]:
        """获取全部指标信息。"""
        result = await self._session.scalars(
            select(MetricInfo).order_by(MetricInfo.name)
        )
        metric_infos = list(result.all())
        await self._load_metric_references(metric_infos)
        return metric_infos

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
