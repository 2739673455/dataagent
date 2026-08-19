"""元数据批量导入服务"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.conf.meta_config import MetaConfig
from app.errors import meta_error
from app.models.meta import (
    COLUMN_EXAMPLE_LIMIT,
    ColumnInfo,
    ColumnKey,
    ColumnReference,
    MetricInfo,
    TableInfo,
    serialize_column_examples,
)
from app.repositories.meta_pg_repo import MetaPGRepo
from app.repositories.source_doris_repo import SourceDorisRepo
from app.services.meta_index_service import MetaIndexService


class ImportMode(StrEnum):
    """元数据导入模式"""

    MERGE = "merge"
    REPLACE = "replace"


@dataclass(frozen=True)
class ResourceChanges[T]:
    """单类元数据变更"""

    created: list[T]
    updated: list[T]
    deleted: list[T]


@dataclass(frozen=True)
class MetaImportResult:
    """元数据导入结果"""

    mode: ImportMode
    dry_run: bool
    tables: ResourceChanges[str]
    columns: ResourceChanges[ColumnKey]
    metrics: ResourceChanges[str]


class MetaImportService:
    """从配置批量导入元数据"""

    def __init__(
        self,
        meta_repo: MetaPGRepo,
        source_repo: SourceDorisRepo,
        meta_index_service: MetaIndexService,
    ) -> None:
        """初始化元数据批量导入服务"""
        self._meta_repo = meta_repo
        self._source_repo = source_repo
        self._meta_index_service = meta_index_service

    async def import_metadata(
        self,
        meta_config: MetaConfig,
        mode: ImportMode,
        dry_run: bool,
    ) -> MetaImportResult:
        """校验并批量导入元数据"""
        if not meta_config.tables and not meta_config.metrics:
            raise meta_error.InvalidMetadataError(
                detail="Metadata import document cannot be empty"
            )

        async with self._meta_repo.transaction():
            existing_tables = {
                table_info.name: table_info
                for table_info in await self._meta_repo.list_table_infos()
            }
            existing_columns = {
                (column_info.t_name, column_info.name): column_info
                for column_info in await self._meta_repo.list_column_infos()
            }
            existing_metrics = {
                metric_info.name: metric_info
                for metric_info in await self._meta_repo.list_metric_infos()
            }

        try:
            table_infos, column_infos, metric_infos = await self._build_metadata(
                meta_config
            )
        except ValueError as exc:
            raise meta_error.InvalidMetadataError(detail=str(exc)) from exc
        imported_tables = self._index_tables(table_infos)
        imported_columns = self._index_columns(column_infos)
        imported_metrics = self._index_metrics(metric_infos)

        available_columns = set(imported_columns)
        if mode is ImportMode.MERGE:
            available_columns.update(existing_columns)
        self._validate_column_references(column_infos, available_columns)
        self._validate_metric_columns(metric_infos, available_columns)

        table_changes = self._get_changes(
            self._table_snapshots(existing_tables),
            self._table_snapshots(imported_tables),
            mode,
        )
        column_changes = self._get_changes(
            self._column_snapshots(existing_columns),
            self._column_snapshots(imported_columns),
            mode,
        )
        metric_changes = self._get_changes(
            self._metric_snapshots(existing_metrics),
            self._metric_snapshots(imported_metrics),
            mode,
        )

        result = MetaImportResult(
            mode=mode,
            dry_run=dry_run,
            tables=table_changes,
            columns=column_changes,
            metrics=metric_changes,
        )
        if dry_run:
            return result

        if mode is ImportMode.REPLACE:
            await self._meta_index_service.delete_metric_indexes(
                metric_changes.deleted
            )
            await self._meta_index_service.delete_column_indexes(
                column_changes.deleted
            )

        async with self._meta_repo.transaction():
            if mode is ImportMode.REPLACE:
                await self._meta_repo.delete_metric_infos(metric_changes.deleted)
                await self._meta_repo.delete_column_infos(column_changes.deleted)
                await self._meta_repo.delete_table_infos(table_changes.deleted)

            for t_name in table_changes.created + table_changes.updated:
                await self._meta_repo.upsert_table_info(
                    imported_tables[t_name],
                    force_version_increment=t_name in table_changes.updated,
                )
            changed_columns = [
                imported_columns[column_key]
                for column_key in column_changes.created + column_changes.updated
            ]
            await self._meta_repo.upsert_column_infos(
                changed_columns,
                force_version_increment_keys=set(column_changes.updated),
            )
            for metric_name in metric_changes.created + metric_changes.updated:
                await self._meta_repo.upsert_metric_info(
                    imported_metrics[metric_name],
                    force_version_increment=metric_name in metric_changes.updated,
                )

        return result

    async def _build_metadata(
        self,
        meta_config: MetaConfig,
    ) -> tuple[list[TableInfo], list[ColumnInfo], list[MetricInfo]]:
        """校验业务数据并构造元数据实体"""
        table_infos: list[TableInfo] = []
        column_infos: list[ColumnInfo] = []

        for table_config in meta_config.tables:
            if not await self._source_repo.table_exists(table_config.name):
                raise meta_error.InvalidMetadataError(
                    detail=f"Source table not found: {table_config.name}"
                )

            primary_key_columns = await self._source_repo.get_primary_key_columns(
                table_config.name
            )
            table_infos.append(
                TableInfo(
                    name=table_config.name,
                    role=table_config.role,
                    primary_key_columns=primary_key_columns,
                    description=table_config.description,
                )
            )

            column_types = await self._source_repo.get_column_types(table_config.name)
            for column_config in table_config.columns:
                if column_config.name not in column_types:
                    raise meta_error.InvalidMetadataError(
                        detail=(
                            "Column not found in source table: "
                            f"{table_config.name}.{column_config.name}"
                        )
                    )
                column_values = await self._source_repo.get_column_values(
                    table_config.name,
                    column_config.name,
                    COLUMN_EXAMPLE_LIMIT,
                )
                column_infos.append(
                    ColumnInfo(
                        t_name=table_config.name,
                        name=column_config.name,
                        type=column_types[column_config.name],
                        examples=serialize_column_examples(column_values),
                        description=column_config.description,
                        alias=list(dict.fromkeys(column_config.alias)),
                        index_values=column_config.index_values,
                        reference_t_name=column_config.reference_t_name,
                        reference_c_name=column_config.reference_c_name,
                    )
                )

        metric_infos = [
            MetricInfo(
                name=metric_config.name,
                description=metric_config.description,
                relevant_columns=[
                    ColumnReference(t_name=t_name, c_name=c_name)
                    for t_name, c_name in sorted(
                        dict.fromkeys(
                            (reference.t_name, reference.c_name)
                            for reference in metric_config.relevant_columns
                        )
                    )
                ],
                alias=list(dict.fromkeys(metric_config.alias)),
            )
            for metric_config in meta_config.metrics
        ]
        return table_infos, column_infos, metric_infos

    @staticmethod
    def _index_tables(items: list[TableInfo]) -> dict[str, TableInfo]:
        """按表名索引实体并校验重名"""
        indexed: dict[str, TableInfo] = {}
        for item in items:
            if item.name in indexed:
                raise meta_error.InvalidMetadataError(
                    detail=f"Duplicate table name in metadata import: {item.name}"
                )
            indexed[item.name] = item
        return indexed

    @staticmethod
    def _index_columns(items: list[ColumnInfo]) -> dict[ColumnKey, ColumnInfo]:
        """按表名和字段名索引实体并校验重名"""
        indexed: dict[ColumnKey, ColumnInfo] = {}
        for item in items:
            key = (item.t_name, item.name)
            if key in indexed:
                raise meta_error.InvalidMetadataError(
                    detail=(
                        "Duplicate column name in metadata import: "
                        f"{item.t_name}.{item.name}"
                    )
                )
            indexed[key] = item
        return indexed

    @staticmethod
    def _index_metrics(items: list[MetricInfo]) -> dict[str, MetricInfo]:
        """按指标名索引实体并校验重名"""
        indexed: dict[str, MetricInfo] = {}
        for item in items:
            if item.name in indexed:
                raise meta_error.InvalidMetadataError(
                    detail=f"Duplicate metric name in metadata import: {item.name}"
                )
            indexed[item.name] = item
        return indexed

    @staticmethod
    def _validate_column_references(
        column_infos: list[ColumnInfo],
        available_columns: set[ColumnKey],
    ) -> None:
        """校验外键字段引用的目标字段"""
        for column_info in column_infos:
            if not column_info.reference_t_name:
                continue
            column_key = (column_info.t_name, column_info.name)
            reference_key = (
                column_info.reference_t_name,
                column_info.reference_c_name,
            )
            if reference_key == column_key:
                raise meta_error.InvalidMetadataError(
                    detail=(
                        "Column cannot reference itself: "
                        f"{column_info.t_name}.{column_info.name}"
                    )
                )
            if reference_key not in available_columns:
                raise meta_error.InvalidMetadataError(
                    detail=(
                        f"Column {column_info.t_name}.{column_info.name} "
                        "references missing column: "
                        f"{reference_key[0]}.{reference_key[1]}"
                    )
                )

    @staticmethod
    def _validate_metric_columns(
        metric_infos: list[MetricInfo],
        available_columns: set[ColumnKey],
    ) -> None:
        """校验指标关联的字段"""
        for metric_info in metric_infos:
            relevant_columns = {
                (reference["t_name"], reference["c_name"])
                for reference in metric_info.relevant_columns
            }
            missing_columns = sorted(relevant_columns - available_columns)
            if missing_columns:
                raise meta_error.InvalidMetadataError(
                    detail=(
                        f"Metric {metric_info.name} references missing columns: "
                        f"{', '.join(f'{table}.{column}' for table, column in missing_columns)}"
                    )
                )

    @staticmethod
    def _get_changes[T: (str, tuple[str, str])](
        existing: dict[T, tuple[Any, ...]],
        imported: dict[T, tuple[Any, ...]],
        mode: ImportMode,
    ) -> ResourceChanges[T]:
        """计算单类元数据的新增、更新和删除主键"""
        existing_keys = set(existing)
        imported_keys = set(imported)
        return ResourceChanges(
            created=sorted(imported_keys - existing_keys),
            updated=sorted(
                item_key
                for item_key in existing_keys & imported_keys
                if existing[item_key] != imported[item_key]
            ),
            deleted=(
                sorted(existing_keys - imported_keys)
                if mode is ImportMode.REPLACE
                else []
            ),
        )

    @staticmethod
    def _table_snapshots(
        table_infos: dict[str, TableInfo],
    ) -> dict[str, tuple[Any, ...]]:
        """生成表元数据比较快照"""
        return {
            t_name: item.metadata_snapshot() for t_name, item in table_infos.items()
        }

    @staticmethod
    def _column_snapshots(
        column_infos: dict[ColumnKey, ColumnInfo],
    ) -> dict[ColumnKey, tuple[Any, ...]]:
        """生成字段元数据比较快照"""
        return {
            column_key: item.metadata_snapshot()
            for column_key, item in column_infos.items()
        }

    @staticmethod
    def _metric_snapshots(
        metric_infos: dict[str, MetricInfo],
    ) -> dict[str, tuple[Any, ...]]:
        """生成指标元数据比较快照"""
        return {
            metric_name: item.metadata_snapshot()
            for metric_name, item in metric_infos.items()
        }
