"""元数据目录管理服务"""

from typing import cast

from app.conf.meta_config import (
    ColumnConfig,
    ColumnReferenceConfig,
    MetaConfig,
    MetricConfig,
    TableConfig,
    TableRole,
)
from app.entities.meta import (
    COLUMN_EXAMPLE_LIMIT,
    ColumnInfo,
    ColumnReference,
    MetricInfo,
    TableInfo,
    serialize_column_examples,
)
from app.errors import meta_error
from app.repositories.meta_pg_repo import MetaPGRepo
from app.repositories.source_doris_repo import SourceDorisRepo
from app.services.meta_index_service import MetaIndexService


class MetaCatalogService:
    """管理表、字段和指标元数据"""

    def __init__(
        self,
        meta_repo: MetaPGRepo,
        source_repo: SourceDorisRepo,
        meta_index_service: MetaIndexService,
    ) -> None:
        """初始化元数据目录管理服务"""
        self._meta_repo = meta_repo
        self._source_repo = source_repo
        self._meta_index_service = meta_index_service

    async def list_table_infos(self) -> list[TableInfo]:
        """查询全部表元数据"""
        return await self._meta_repo.list_table_infos()

    async def list_column_infos(self, t_name: str) -> list[ColumnInfo]:
        """查询表下全部字段元数据"""
        await self._meta_repo.get_table_info(t_name)
        return [
            column_info
            for column_info in await self._meta_repo.list_column_infos()
            if column_info.t_name == t_name
        ]

    async def list_metric_infos(self) -> list[MetricInfo]:
        """查询全部指标元数据"""
        return await self._meta_repo.list_metric_infos()

    async def upsert_table_info(
        self,
        t_name: str,
        role: TableRole,
        description: str,
    ) -> None:
        """新增或更新表元数据"""
        if not await self._source_repo.table_exists(t_name):
            raise meta_error.InvalidMetadataError(
                detail=f"Source table not found: {t_name}"
            )
        primary_key_columns = await self._source_repo.get_primary_key_columns(t_name)
        async with self._meta_repo.transaction():
            await self._meta_repo.upsert_table_info(
                TableInfo(
                    name=t_name,
                    role=role,
                    primary_key_columns=primary_key_columns,
                    description=description,
                )
            )

    async def upsert_column_info(
        self,
        t_name: str,
        c_name: str,
        description: str,
        alias: list[str],
        index_values: bool,
        reference_t_name: str | None = None,
        reference_c_name: str | None = None,
    ) -> None:
        """新增或更新字段元数据"""
        if not await self._source_repo.table_exists(t_name):
            raise meta_error.InvalidMetadataError(
                detail=f"Source table not found: {t_name}"
            )
        column_types = await self._source_repo.get_column_types(t_name)
        if c_name not in column_types:
            raise meta_error.InvalidMetadataError(
                detail=f"Column not found in source table: {t_name}.{c_name}"
            )
        column_values = await self._source_repo.get_column_values(
            t_name,
            c_name,
            COLUMN_EXAMPLE_LIMIT,
        )

        async with self._meta_repo.transaction():
            try:
                await self._meta_repo.get_table_info(t_name)
            except meta_error.MetadataNotFoundError as exc:
                raise meta_error.InvalidMetadataError(
                    detail=f"Table info not found for column: {t_name}.{c_name}"
                ) from exc
            if (reference_t_name is None) != (reference_c_name is None):
                raise meta_error.InvalidMetadataError(
                    detail=(
                        "Reference table name and column name must be provided together"
                    )
                )
            if reference_t_name and reference_c_name:
                if (reference_t_name, reference_c_name) == (
                    t_name,
                    c_name,
                ):
                    raise meta_error.InvalidMetadataError(
                        detail=(f"Column cannot reference itself: {t_name}.{c_name}")
                    )
                try:
                    await self._meta_repo.get_column_info(
                        reference_t_name,
                        reference_c_name,
                    )
                except meta_error.MetadataNotFoundError as exc:
                    raise meta_error.InvalidMetadataError(
                        detail=(
                            "Reference column not found: "
                            f"{reference_t_name}.{reference_c_name}"
                        )
                    ) from exc
            await self._meta_repo.upsert_column_info(
                ColumnInfo(
                    t_name=t_name,
                    name=c_name,
                    type=column_types[c_name],
                    examples=serialize_column_examples(column_values),
                    description=description,
                    alias=list(dict.fromkeys(alias)),
                    index_values=index_values,
                    reference_t_name=reference_t_name,
                    reference_c_name=reference_c_name,
                )
            )

    async def upsert_metric_info(self, metric_info: MetricInfo) -> None:
        """新增或更新指标元数据"""
        async with self._meta_repo.transaction():
            relevant_column_keys = sorted(
                dict.fromkeys(
                    (
                        reference["t_name"],
                        reference["c_name"],
                    )
                    for reference in metric_info.relevant_columns
                )
            )
            for t_name, c_name in relevant_column_keys:
                try:
                    await self._meta_repo.get_column_info(t_name, c_name)
                except meta_error.MetadataNotFoundError as exc:
                    raise meta_error.InvalidMetadataError(
                        detail=f"Relevant column not found: {t_name}.{c_name}"
                    ) from exc
            metric_info.relevant_columns = [
                ColumnReference(t_name=t_name, c_name=c_name)
                for t_name, c_name in relevant_column_keys
            ]
            metric_info.alias = list(dict.fromkeys(metric_info.alias))
            await self._meta_repo.upsert_metric_info(metric_info)

    async def delete_table_info(self, t_name: str) -> None:
        """删除表及其字段元数据和索引"""
        async with self._meta_repo.transaction():
            await self._meta_repo.get_table_info(t_name)
            column_infos = await self._meta_repo.list_column_infos()
            column_keys = [
                (column_info.t_name, column_info.name)
                for column_info in column_infos
                if column_info.t_name == t_name
            ]
            await self._validate_column_deletion(column_keys, column_infos)
        await self._meta_index_service.delete_column_indexes(column_keys)
        async with self._meta_repo.transaction():
            await self._meta_repo.delete_column_infos(column_keys)
            await self._meta_repo.delete_table_infos([t_name])

    async def delete_column_info(self, t_name: str, c_name: str) -> None:
        """删除字段元数据和索引"""
        async with self._meta_repo.transaction():
            await self._meta_repo.get_column_info(t_name, c_name)
            column_infos = await self._meta_repo.list_column_infos()
            column_keys = [(t_name, c_name)]
            await self._validate_column_deletion(column_keys, column_infos)
        await self._meta_index_service.delete_column_indexes(column_keys)
        async with self._meta_repo.transaction():
            await self._meta_repo.delete_column_infos(column_keys)

    async def delete_metric_info(self, metric_name: str) -> None:
        """删除指标元数据和索引"""
        async with self._meta_repo.transaction():
            await self._meta_repo.get_metric_info(metric_name)
        await self._meta_index_service.delete_metric_indexes([metric_name])
        async with self._meta_repo.transaction():
            await self._meta_repo.delete_metric_infos([metric_name])

    async def _validate_column_deletion(
        self,
        column_keys: list[tuple[str, str]],
        column_infos: list[ColumnInfo],
    ) -> None:
        """校验待删除字段未被保留元数据引用"""
        deleted_keys = set(column_keys)
        dependent_columns = sorted(
            (column_info.t_name, column_info.name)
            for column_info in column_infos
            if (column_info.t_name, column_info.name) not in deleted_keys
            and (
                column_info.reference_t_name,
                column_info.reference_c_name,
            )
            in deleted_keys
        )
        dependent_metrics = sorted(
            metric_info.name
            for metric_info in await self._meta_repo.list_metric_infos()
            if any(
                (reference["t_name"], reference["c_name"]) in deleted_keys
                for reference in metric_info.relevant_columns
            )
        )
        conflicts: list[str] = []
        if dependent_columns:
            conflicts.append(
                "referenced by columns: "
                + ", ".join(
                    f"{t_name}.{c_name}" for t_name, c_name in dependent_columns
                )
            )
        if dependent_metrics:
            conflicts.append("used by metrics: " + ", ".join(dependent_metrics))
        if conflicts:
            raise meta_error.MetadataConflictError(
                detail="Cannot delete metadata columns; " + "; ".join(conflicts)
            )

    async def export_metadata(self) -> MetaConfig:
        """导出可重新导入的元数据配置"""
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
                            name=column_info.name,
                            description=column_info.description,
                            alias=column_info.alias,
                            index_values=column_info.index_values,
                            reference_t_name=column_info.reference_t_name,
                            reference_c_name=column_info.reference_c_name,
                        )
                        for column_info in sorted(
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
