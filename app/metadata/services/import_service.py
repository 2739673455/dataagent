"""元数据批量导入服务。"""

import yaml
from loguru import logger
from pydantic import ValidationError as PydanticValidationError
from yaml import YAMLError

from app.metadata import errors as meta_error
from app.metadata.config import MetaConfig
from app.metadata.models.catalog import (
    COLUMN_EXAMPLE_LIMIT,
    ColumnInfo,
    MetricInfo,
    TableInfo,
    column_key_reference,
    serialize_column_examples,
)
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.index import MetaIndexService


def parse_metadata_yaml(content: bytes) -> MetaConfig:
    """解析并校验 UTF-8 YAML 元数据文档。"""
    if not content:
        raise meta_error.InvalidMetadataError(detail="元数据 YAML 文件不能为空")

    try:
        raw_config = yaml.safe_load(content.decode("utf-8"))
        config = MetaConfig.model_validate(raw_config)
        validate_metadata_config(config)
        return config
    except UnicodeDecodeError as exc:
        raise meta_error.InvalidMetadataError(
            detail="元数据 YAML 文件必须使用 UTF-8 编码",
        ) from exc
    except YAMLError as exc:
        raise meta_error.InvalidMetadataError(
            detail=f"元数据 YAML 格式解析失败: {exc}",
        ) from exc
    except PydanticValidationError as exc:
        errors = exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        raise meta_error.InvalidMetadataError(
            detail="元数据 YAML 结构不符合规范要求",
            extensions={"errors": errors},
        ) from exc


def validate_metadata_config(config: MetaConfig) -> None:
    """校验名称唯一性及 YAML 内部引用，不依赖数据库现状。"""
    table_names = [item.name for item in config.tables]
    metric_names = [item.name for item in config.metrics]
    if len(set(table_names)) != len(table_names):
        raise meta_error.InvalidMetadataError(detail="元数据 YAML 存在重复表名")
    if len(set(metric_names)) != len(metric_names):
        raise meta_error.InvalidMetadataError(detail="元数据 YAML 存在重复指标名")
    keys = {
        (table.name, column.name) for table in config.tables for column in table.columns
    }
    for table in config.tables:
        if len({column.name for column in table.columns}) != len(table.columns):
            raise meta_error.InvalidMetadataError(detail=f"存在重复字段: {table.name}")
        for column in table.columns:
            if column.reference_t_name is not None:
                reference = (column.reference_t_name, column.reference_c_name)
                if reference not in keys:
                    raise meta_error.InvalidMetadataError(
                        detail=f"字段引用无效: {table.name}.{column.name}"
                    )
    for metric in config.metrics:
        if any((ref.t_name, ref.c_name) not in keys for ref in metric.relevant_columns):
            raise meta_error.InvalidMetadataError(
                detail=f"指标引用不存在的字段: {metric.name}"
            )


class MetaImportService:
    """从配置批量导入元数据。"""

    def __init__(
        self,
        meta_repo: MetaPGRepo,
        source_repo: SourceDorisRepo,
        meta_index_service: MetaIndexService,
    ) -> None:
        """初始化元数据批量导入服务。"""
        self._meta_repo = meta_repo
        self._source_repo = source_repo
        self._meta_index_service = meta_index_service

    async def import_full(self, meta_config: MetaConfig) -> None:
        """接收已校验的配置，校验源表后替换目录并构建全部索引。"""
        table_infos, column_infos, metric_infos = await self._build_metadata(
            meta_config
        )
        logger.info("元数据配置和源表校验通过")
        await self._meta_index_service.reset_indexes()
        async with self._meta_repo.session.begin():
            await self._meta_repo.replace_catalog(
                table_infos, column_infos, metric_infos
            )
        logger.info(
            "元数据目录写入完成 tables={} columns={} metrics={}",
            len(table_infos),
            len(column_infos),
            len(metric_infos),
        )
        await self._meta_index_service.build_column_indexes(
            [(item.t_name, item.name) for item in column_infos]
        )
        logger.info("字段语义索引构建完成")
        await self._meta_index_service.build_metric_indexes(
            [item.name for item in metric_infos]
        )
        logger.info("指标语义索引构建完成")
        await self._meta_index_service.sync_column_values(
            [(item.t_name, item.name) for item in column_infos if item.index_values],
            mode="full",
        )
        logger.info("元数据全量导入完成")

    async def _build_metadata(
        self,
        meta_config: MetaConfig,
    ) -> tuple[list[TableInfo], list[ColumnInfo], list[MetricInfo]]:
        """校验业务数据并构造元数据实体。"""
        table_infos: list[TableInfo] = []
        column_infos: list[ColumnInfo] = []

        for table_config in meta_config.tables:
            if not await self._source_repo.table_exists(table_config.name):
                raise meta_error.InvalidMetadataError(
                    detail=f"数仓源表不存在: {table_config.name}"
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
                    value_index_cursor_column=table_config.value_index_cursor_column,
                )
            )

            column_types = await self._source_repo.get_column_types(table_config.name)
            cursor_column = table_config.value_index_cursor_column
            if cursor_column is not None and cursor_column not in column_types:
                raise meta_error.InvalidMetadataError(
                    detail=(
                        "源表中未找到取值索引增量游标字段: "
                        f"{table_config.name}.{cursor_column}"
                    )
                )
            for column_config in table_config.columns:
                if column_config.name not in column_types:
                    raise meta_error.InvalidMetadataError(
                        detail=(
                            "源表中未找到指定字段: "
                            f"{table_config.name}.{column_config.name}"
                        )
                    )

            target_column_names = [col.name for col in table_config.columns]
            # 同一张表一次取齐所有示例值，避免逐字段查询产生 N+1 开销和不同采样快照。
            table_column_samples = (
                await self._source_repo.get_table_columns_sample_values(
                    table_config.name,
                    target_column_names,
                    COLUMN_EXAMPLE_LIMIT,
                )
            )

            for column_config in table_config.columns:
                column_values = table_column_samples.get(column_config.name, [])
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
                    column_key_reference(key)
                    for key in sorted(
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
