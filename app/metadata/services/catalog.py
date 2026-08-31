"""元数据目录管理服务"""

from typing import cast

from app.metadata import errors as meta_error
from app.metadata.config import (
    ColumnConfig,
    ColumnReferenceConfig,
    MetaConfig,
    MetricConfig,
    TableConfig,
    TableRole,
)
from app.metadata.models.catalog import (
    COLUMN_EXAMPLE_LIMIT,
    ColumnInfo,
    ColumnReference,
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
from app.shared.tasks.submission import TaskSubmission


class MetaCatalogService:
    """管理表、字段和指标元数据"""

    def __init__(
        self,
        meta_repo: MetaPGRepo,
        source_repo: SourceDorisRepo,
        meta_index_service: MetaIndexService,
        asset_invalidator: MetadataAssetInvalidator,
        semantic_index_scheduler: MetadataSemanticIndexScheduler,
    ) -> None:
        """初始化元数据目录管理服务"""
        self._meta_repo = meta_repo
        self._source_repo = source_repo
        self._meta_index_service = meta_index_service
        self._asset_invalidator = asset_invalidator
        self._semantic_index_scheduler = semantic_index_scheduler

    async def list_table_infos(self) -> list[TableInfo]:
        """查询全部表元数据"""
        return await self._meta_repo.list_table_infos()

    async def list_source_tables(self) -> list[str]:
        """查询底层 Doris 数据库物理表清单"""
        return await self._source_repo.list_tables()

    async def list_column_infos(self, t_name: str) -> list[ColumnInfo]:
        """查询表下全部字段元数据"""
        await self._meta_repo.get_table_info(t_name)
        return await self._meta_repo.list_column_infos_by_table_names(
            table_names=[t_name]
        )

    async def list_metric_infos(self) -> list[MetricInfo]:
        """查询全部指标元数据"""
        return await self._meta_repo.list_metric_infos()

    async def upsert_table_info(
        self,
        t_name: str,
        role: TableRole,
        description: str,
        value_index_cursor_column: str | None = None,
    ) -> None:
        """新增或更新表元数据"""
        if not await self._source_repo.table_exists(t_name):
            raise meta_error.InvalidMetadataError(detail=f"源表不存在: {t_name}")
        primary_key_columns = await self._source_repo.get_primary_key_columns(t_name)
        column_types = await self._source_repo.get_column_types(t_name)
        if (
            value_index_cursor_column is not None
            and value_index_cursor_column not in column_types
        ):
            raise meta_error.InvalidMetadataError(
                detail=(
                    "源表中未找到取值索引增量游标字段: "
                    f"{t_name}.{value_index_cursor_column}"
                )
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
            await self._asset_invalidator.invalidate_assets(
                table_names={t_name},
                column_keys=set(),
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
    ) -> TaskSubmission | None:
        """新增或更新字段元数据"""
        if not await self._source_repo.table_exists(t_name):
            raise meta_error.InvalidMetadataError(detail=f"源表不存在: {t_name}")
        column_types = await self._source_repo.get_column_types(t_name)
        if c_name not in column_types:
            raise meta_error.InvalidMetadataError(
                detail=f"源表中未找到指定字段: {t_name}.{c_name}"
            )
        column_values = await self._source_repo.get_column_values(
            t_name,
            c_name,
            COLUMN_EXAMPLE_LIMIT,
        )

        async with self._meta_repo.session.begin():
            try:
                await self._meta_repo.get_table_info(t_name)
            except meta_error.MetadataNotFoundError as exc:
                raise meta_error.InvalidMetadataError(
                    detail=f"未找到字段所属表的元数据: {t_name}.{c_name}"
                ) from exc
            if (reference_t_name is None) != (reference_c_name is None):
                raise meta_error.InvalidMetadataError(
                    detail=("引用表名和引用列名必须同时提供")
                )
            if reference_t_name and reference_c_name:
                if (reference_t_name, reference_c_name) == (
                    t_name,
                    c_name,
                ):
                    raise meta_error.InvalidMetadataError(
                        detail=(f"字段不能引用自身: {t_name}.{c_name}")
                    )
                try:
                    await self._meta_repo.get_column_info(
                        reference_t_name,
                        reference_c_name,
                    )
                except meta_error.MetadataNotFoundError as exc:
                    raise meta_error.InvalidMetadataError(
                        detail=(
                            "未找到引用的目标字段: "
                            f"{reference_t_name}.{reference_c_name}"
                        )
                    ) from exc
            changed = await self._meta_repo.upsert_column_info(
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
        if changed:
            await self._asset_invalidator.invalidate_assets(
                table_names=set(),
                column_keys={(t_name, c_name)},
            )
            return self._semantic_index_scheduler.enqueue_columns([(t_name, c_name)])
        return None

    async def upsert_metric_info(
        self,
        metric_info: MetricInfo,
    ) -> TaskSubmission | None:
        """新增或更新指标元数据"""
        async with self._meta_repo.session.begin():
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
                        detail=f"未找到相关字段: {t_name}.{c_name}"
                    ) from exc
            metric_info.relevant_columns = [
                ColumnReference(t_name=t_name, c_name=c_name)
                for t_name, c_name in relevant_column_keys
            ]
            metric_info.alias = list(dict.fromkeys(metric_info.alias))
            changed = await self._meta_repo.upsert_metric_info(metric_info)
        if changed:
            return self._semantic_index_scheduler.enqueue_metrics([metric_info.name])
        return None

    async def delete_tables(self, table_names: list[str]) -> None:
        """删除多个表及其字段元数据和索引"""
        unique_table_names = list(dict.fromkeys(table_names))
        if not unique_table_names:
            return
        async with self._meta_repo.session.begin():
            for t_name in unique_table_names:
                await self._meta_repo.get_table_info(t_name)
            column_infos = await self._meta_repo.list_column_infos()
            column_keys = [
                (column_info.t_name, column_info.name)
                for column_info in column_infos
                if column_info.t_name in unique_table_names
            ]
            await self._validate_column_deletion(column_keys, column_infos)
        await self._meta_index_service.delete_column_indexes(column_keys)
        async with self._meta_repo.session.begin():
            await self._meta_repo.delete_column_infos(column_keys)
            await self._meta_repo.delete_table_infos(unique_table_names)
        await self._asset_invalidator.invalidate_assets(
            table_names=set(unique_table_names),
            column_keys=set(),
        )

    async def delete_columns(self, column_keys: list[tuple[str, str]]) -> None:
        """删除多个字段元数据和索引"""
        unique_keys = list(dict.fromkeys(column_keys))
        if not unique_keys:
            return
        async with self._meta_repo.session.begin():
            for t_name, c_name in unique_keys:
                await self._meta_repo.get_column_info(t_name, c_name)
            all_column_infos = await self._meta_repo.list_column_infos()
            await self._validate_column_deletion(unique_keys, all_column_infos)
        await self._meta_index_service.delete_column_indexes(unique_keys)
        async with self._meta_repo.session.begin():
            await self._meta_repo.delete_column_infos(unique_keys)
        await self._asset_invalidator.invalidate_assets(
            table_names=set(),
            column_keys=set(unique_keys),
        )

    async def delete_metrics(self, metric_names: list[str]) -> None:
        """删除多个指标元数据和索引"""
        unique_names = list(dict.fromkeys(metric_names))
        if not unique_names:
            return
        async with self._meta_repo.session.begin():
            for name in unique_names:
                await self._meta_repo.get_metric_info(name)
        await self._meta_index_service.delete_metric_indexes(unique_names)
        async with self._meta_repo.session.begin():
            await self._meta_repo.delete_metric_infos(unique_names)

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
                "被其他字段引用: "
                + ", ".join(
                    f"{t_name}.{c_name}" for t_name, c_name in dependent_columns
                )
            )
        if dependent_metrics:
            conflicts.append("被指标使用: " + ", ".join(dependent_metrics))
        if conflicts:
            raise meta_error.MetadataConflictError(
                detail="无法删除元数据字段，存在冲突引用: " + "; ".join(conflicts)
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
                    value_index_cursor_column=table_info.value_index_cursor_column,
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
