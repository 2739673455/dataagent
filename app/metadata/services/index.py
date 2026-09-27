"""元数据全量导入、索引构建与字段取值增量同步。"""

import json
import unicodedata
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import yaml
from loguru import logger
from pydantic import ValidationError as PydanticValidationError
from yaml import YAMLError

from app.metadata import errors as meta_error
from app.metadata.config import MetaConfig
from app.metadata.models.catalog import (
    COLUMN_EXAMPLE_LIMIT,
    ColumnInfo,
    ColumnKey,
    MetricInfo,
    TableInfo,
    ValueInfo,
    column_key_reference,
    column_resource_key,
    serialize_column_examples,
)
from app.metadata.models.search import (
    SemanticIndexDocument,
    SemanticTextType,
    ValueIndexSyncMode,
)
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.shared.clients.embedding_client_manager import EmbeddingClient


def parse_metadata_yaml(content: bytes) -> MetaConfig:
    """解析并校验 UTF-8 YAML 元数据文档。"""
    if not content:
        raise meta_error.InvalidMetadataError(detail="元数据 YAML 文件不能为空")

    try:
        raw_config = yaml.safe_load(content.decode("utf-8"))
        config = MetaConfig.model_validate(raw_config)
        _validate_metadata_config(config)
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


def _validate_metadata_config(config: MetaConfig) -> None:
    """校验名称唯一性及 YAML 内部引用。"""
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


class MetaIndexService:
    """导入元数据并同步字段、字段值和指标检索索引。"""

    _embedding_batch_size = 64

    def __init__(
        self,
        meta_repo: MetaPGRepo,
        source_repo: SourceDorisRepo,
        column_repo: ColumnESRepo,
        metric_repo: MetricESRepo,
        embedding_client: EmbeddingClient,
        value_repo: ValueESRepo,
    ) -> None:
        """初始化元数据检索索引同步服务。"""
        self._meta_repo = meta_repo
        self._source_repo = source_repo
        self._column_repo = column_repo
        self._metric_repo = metric_repo
        self._embedding_client = embedding_client
        self._value_repo = value_repo
        self._value_upper_bounds: dict[tuple[str, str], Any] = {}

    async def import_full(self, meta_config: MetaConfig) -> None:
        """接收已校验的配置，校验源表后替换目录并构建全部索引。"""
        table_infos, column_infos, metric_infos = await self._build_metadata(
            meta_config
        )
        logger.info("元数据配置和源表校验通过")
        for repo in (self._column_repo, self._metric_repo, self._value_repo):
            await repo.reset_index()
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
        await self._build_column_indexes(column_infos)
        logger.info("字段语义索引构建完成")
        await self._build_metric_indexes(metric_infos)
        logger.info("指标语义索引构建完成")
        await self._sync_column_values(
            [(item.t_name, item.name) for item in column_infos if item.index_values],
            mode="full",
        )
        logger.info("字段取值索引构建完成")
        logger.info("元数据全量导入完成")

    async def import_incremental_values(self) -> None:
        """检查已配置水位的表，仅同步启用取值索引的字段。"""
        async with self._meta_repo.session.begin():
            tables = await self._meta_repo.list_table_infos()
            columns = await self._meta_repo.list_column_infos()
        if not tables:
            raise RuntimeError("元数据目录为空，请先执行全量导入")
        eligible = {
            table.name
            for table in tables
            if table.value_index_cursor_column is not None
        }
        await self._sync_column_values(
            [
                (item.t_name, item.name)
                for item in columns
                if item.index_values and item.t_name in eligible
            ],
            mode="incremental",
        )
        logger.info("字段取值增量导入完成")

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
                        alias=sorted(set(column_config.alias)),
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
                alias=sorted(set(metric_config.alias)),
            )
            for metric_config in meta_config.metrics
        ]
        return table_infos, column_infos, metric_infos

    async def _build_column_indexes(self, columns: list[ColumnInfo]) -> None:
        """构建字段的名称、说明与别名索引。"""
        for column in columns:
            documents = await self._build_semantic_documents(
                "column",
                column_resource_key(column.t_name, column.name),
                {
                    "t_name": column.t_name,
                    "name": column.name,
                    "type": column.type,
                    "examples": column.examples,
                    "description": column.description,
                    "alias": column.alias,
                    "index_values": column.index_values,
                    "reference_t_name": column.reference_t_name,
                    "reference_c_name": column.reference_c_name,
                },
                column.name,
                column.description,
                column.alias,
            )
            await self._column_repo.write_documents(documents)

    async def _build_metric_indexes(self, metrics: list[MetricInfo]) -> None:
        """构建指标的名称、说明与别名索引。"""
        for metric in metrics:
            documents = await self._build_semantic_documents(
                "metric",
                metric.name,
                {
                    "name": metric.name,
                    "description": metric.description,
                    "relevant_columns": metric.relevant_columns,
                    "alias": metric.alias,
                },
                metric.name,
                metric.description,
                metric.alias,
            )
            await self._metric_repo.write_documents(documents)

    async def _build_semantic_documents(
        self,
        resource_type: str,
        resource_key: str,
        payload: dict[str, Any],
        name: str,
        description: str,
        aliases: list[str],
    ) -> list[SemanticIndexDocument]:
        """生成规范化、去重且编号稳定的目标文档。"""
        entries: dict[str, SemanticTextType] = {}
        source_texts: list[tuple[str, SemanticTextType]] = [
            (name, "name"),
            (description, "description"),
        ]
        source_texts.extend((alias, "alias") for alias in aliases)
        for text_value, text_type in source_texts:
            canonical = unicodedata.normalize("NFC", text_value).strip()
            if canonical:
                entries.setdefault(canonical, text_type)
        texts = sorted(entries)
        embeddings: list[list[float]] = []
        for index in range(0, len(texts), self._embedding_batch_size):
            batch = texts[index : index + self._embedding_batch_size]
            embeddings.extend(await self._embedding_client.aembed_documents(batch))
        return [
            SemanticIndexDocument(
                id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        json.dumps(
                            [resource_type, resource_key, text_value],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                ),
                resource_key=resource_key,
                text=text_value,
                text_type=entries[text_value],
                embedding=embedding,
                payload=payload,
            )
            for text_value, embedding in zip(texts, embeddings, strict=True)
        ]

    async def _sync_column_values(
        self,
        column_keys: list[ColumnKey],
        *,
        mode: ValueIndexSyncMode,
    ) -> None:
        """按指定模式同步字段取值；全量索引清理由导入入口负责。"""
        self._value_upper_bounds.clear()
        for column_key in dict.fromkeys(column_keys):
            await self._sync_column_value_index(
                *column_key,
                requested_mode=mode,
            )

    async def _sync_column_value_index(
        self,
        t_name: str,
        c_name: str,
        *,
        requested_mode: ValueIndexSyncMode,
    ) -> None:
        """读取配置和水位，索引写入成功后再提交新水位。"""
        async with self._meta_repo.session.begin():
            column_info = await self._meta_repo.get_column_info(t_name, c_name)
            table_info = await self._meta_repo.get_table_info(t_name)
            cursor_column = table_info.value_index_cursor_column
            cursor_value = column_info.value_index_cursor_value
        await self._value_repo.ensure_index()
        upserted_count, new_cursor = await self._run_value_sync(
            t_name, c_name, cursor_column, cursor_value, mode=requested_mode
        )
        if new_cursor is not None and new_cursor != cursor_value:
            async with self._meta_repo.session.begin():
                await self._meta_repo.update_value_index_cursor(
                    t_name, c_name, new_cursor
                )
        logger.info(
            "字段取值导入完成 table={} column={} values={} watermark={}",
            t_name,
            c_name,
            upserted_count,
            new_cursor,
        )

    async def _run_value_sync(
        self,
        t_name: str,
        c_name: str,
        cursor_column: str | None,
        cursor_value: dict[str, Any] | None,
        *,
        mode: ValueIndexSyncMode,
    ) -> tuple[int, dict[str, Any] | None]:
        """按模式选择扫描范围，共用取值写入和索引刷新。"""
        upper_bound = (
            await self._value_upper_bound(t_name, cursor_column)
            if cursor_column is not None
            else None
        )
        if mode == "full":
            batches = self._source_repo.iter_column_value_batches(t_name, c_name)
        else:
            if cursor_column is None:
                raise RuntimeError("字段取值增量同步缺少游标配置")
            previous_cursor = (
                self._deserialize_cursor(cursor_value)
                if cursor_value is not None
                else None
            )
            if upper_bound is None or (
                previous_cursor is not None and upper_bound <= previous_cursor
            ):
                logger.info("水位未推进，跳过 table={} column={}", t_name, c_name)
                return 0, cursor_value
            batches = self._source_repo.iter_changed_column_value_batches(
                t_name, c_name, cursor_column, previous_cursor, upper_bound
            )
        read_count = await self._upsert_value_batches(batches, t_name, c_name)
        if read_count:
            await self._value_repo.refresh()
        new_cursor = (
            self._serialize_cursor(upper_bound)
            if upper_bound is not None
            else cursor_value
        )
        return read_count, new_cursor

    async def _value_upper_bound(self, table: str, cursor: str) -> Any:
        """同次批量同步中，每个表及水位列组合只读取一次上界。"""
        key = (table, cursor)
        if key not in self._value_upper_bounds:
            self._value_upper_bounds[
                key
            ] = await self._source_repo.get_value_sync_upper_bound(table, cursor)
        return self._value_upper_bounds[key]

    async def _upsert_value_batches(
        self,
        batches: AsyncIterator[list[Any]],
        t_name: str,
        c_name: str,
    ) -> int:
        """过滤空值并分批写入，返回写入数量（含覆盖已有文档）。"""
        count = 0
        async for values in batches:
            value_infos = [
                ValueInfo(
                    value=self._serialize_value(value),
                    t_name=t_name,
                    c_name=c_name,
                )
                for value in values
                if value is not None
            ]
            if value_infos:
                await self._value_repo.upsert(value_infos)
                count += len(value_infos)
        return count

    @staticmethod
    def _serialize_cursor(value: Any) -> dict[str, object]:
        """将 Doris 水位编码为保留类型信息的 JSON 数据。"""
        if isinstance(value, datetime):
            return {"type": "datetime", "value": value.isoformat()}
        if isinstance(value, date):
            return {"type": "date", "value": value.isoformat()}
        if isinstance(value, Decimal):
            return {"type": "decimal", "value": str(value)}
        if type(value) in (bool, int, float, str):
            return {"type": type(value).__name__, "value": value}
        raise TypeError(f"不支持的取值索引游标类型: {type(value).__name__}")

    @staticmethod
    def _deserialize_cursor(payload: dict[str, Any]) -> Any:
        """从持久化 JSON 水位恢复 Doris 值的类型。"""
        cursor_type = payload.get("type")
        value = payload.get("value")
        if cursor_type == "datetime" and isinstance(value, str):
            return datetime.fromisoformat(value)
        if cursor_type == "date" and isinstance(value, str):
            return date.fromisoformat(value)
        if cursor_type == "decimal" and isinstance(value, str):
            return Decimal(value)
        if cursor_type == "float" and (type(value) is int or type(value) is float):
            return float(value)
        if isinstance(cursor_type, str) and type(value) is {
            "bool": bool,
            "int": int,
            "str": str,
        }.get(cursor_type):
            return value
        raise ValueError("取值索引游标状态格式无效")

    @staticmethod
    def _serialize_value(value: Any) -> str:
        """将字段取值转换为索引文本。"""
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value)
