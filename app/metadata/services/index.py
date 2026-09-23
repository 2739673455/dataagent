"""元数据索引构建与字段取值增量导入。"""

import json
import unicodedata
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from loguru import logger

from app.metadata.models.catalog import (
    ColumnInfo,
    ColumnKey,
    MetricInfo,
    ValueIndexSyncState,
    ValueInfo,
    column_resource_key,
    serialize_column_examples,
)
from app.metadata.models.search import (
    SemanticIndexDocument,
    SemanticTextType,
    ValueIndexSyncMode,
    ValueIndexSyncResult,
)
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.shared.clients.embedding_client_manager import EmbeddingClient


@dataclass(frozen=True, slots=True)
class _ValueIndexRun:
    """一次字段取值索引运行在事务外执行所需的不可变快照。"""

    run_id: uuid.UUID
    t_name: str
    c_name: str
    mode: ValueIndexSyncMode
    cursor_column: str | None
    cursor_value: dict[str, Any] | None
    generation: uuid.UUID | None


class MetaIndexService:
    """同步字段、字段值和指标检索索引。"""

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

    async def reset_indexes(self) -> None:
        """删除并重建三个元数据索引。"""
        for repo in (self._column_repo, self._metric_repo, self._value_repo):
            await repo.reset_index()

    async def import_incremental_values(self) -> None:
        """检查已配置水位的表，仅同步启用取值索引的字段。"""
        async with self._meta_repo.session.begin():
            tables = await self._meta_repo.list_table_infos()
            columns = await self._meta_repo.list_column_infos()
        if not tables:
            raise RuntimeError("元数据目录为空，请先执行全量导入")
        eligible = set()
        for table in tables:
            if table.value_index_cursor_column is None:
                logger.info("跳过未配置水位的表 table={}", table.name)
            else:
                eligible.add(table.name)
        await self.sync_column_values(
            [
                (item.t_name, item.name)
                for item in columns
                if item.index_values and item.t_name in eligible
            ],
            mode="incremental",
        )
        logger.info("字段取值增量导入完成")

    async def _value_upper_bound(self, table: str, cursor: str) -> Any:
        """同次导入中每张表只读取一次固定水位上界。"""
        key = (table, cursor)
        if key not in self._value_upper_bounds:
            self._value_upper_bounds[
                key
            ] = await self._source_repo.get_value_sync_upper_bound(table, cursor)
        return self._value_upper_bounds[key]

    async def build_column_indexes(self, column_keys: list[ColumnKey]) -> None:
        """构建字段的名称、说明与别名索引。"""
        for t_name, c_name in dict.fromkeys(column_keys):
            async with self._meta_repo.session.begin():
                column = await self._meta_repo.get_column_info(t_name, c_name)
            documents = await self._build_semantic_documents(
                "column",
                column_resource_key(t_name, c_name),
                self._column_payload(column),
                column.name,
                column.description,
                column.alias,
            )
            await self._column_repo.write_documents(documents)
            async with self._meta_repo.session.begin():
                await self._meta_repo.mark_column_indexed(t_name, c_name)

    async def build_metric_indexes(self, metric_names: list[str]) -> None:
        """构建指标的名称、说明与别名索引。"""
        for name in dict.fromkeys(metric_names):
            async with self._meta_repo.session.begin():
                metric = await self._meta_repo.get_metric_info(name)
            documents = await self._build_semantic_documents(
                "metric",
                name,
                self._metric_payload(metric),
                metric.name,
                metric.description,
                metric.alias,
            )
            await self._metric_repo.write_documents(documents)
            async with self._meta_repo.session.begin():
                await self._meta_repo.mark_metric_indexed(name)

    async def sync_column_values(
        self,
        column_keys: list[ColumnKey],
        *,
        mode: ValueIndexSyncMode,
    ) -> dict[ColumnKey, ValueIndexSyncResult]:
        """按水位或全量校准模式同步多个字段取值。"""
        self._value_upper_bounds.clear()
        results: dict[ColumnKey, ValueIndexSyncResult] = {}
        for column_key in dict.fromkeys(column_keys):
            results[column_key] = await self._sync_column_value_index(
                *column_key,
                requested_mode=mode,
            )
        return results

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
        embeddings = await self._embed_texts(texts)
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

    async def _sync_column_value_index(
        self,
        t_name: str,
        c_name: str,
        *,
        requested_mode: ValueIndexSyncMode,
    ) -> ValueIndexSyncResult:
        """执行单字段取值索引状态机。"""
        run = await self._begin_value_index_run(
            t_name,
            c_name,
            requested_mode=requested_mode,
        )
        try:
            result = await self._execute_value_index_run(run)
            await self._complete_value_index_run(run, result)
            logger.info(
                "字段取值导入完成 table={} column={} values={} watermark={}",
                t_name,
                c_name,
                result.upserted_count,
                result.cursor_value,
            )
            return result
        except Exception as exc:
            await self._fail_value_index_run(run, exc)
            raise

    async def _begin_value_index_run(
        self,
        t_name: str,
        c_name: str,
        *,
        requested_mode: ValueIndexSyncMode,
    ) -> _ValueIndexRun:
        """在短事务中校验配置并登记运行所有权。"""
        run_id = uuid.uuid4()
        started_at = datetime.now(UTC)
        async with self._meta_repo.session.begin():
            await self._meta_repo.acquire_index_lock(
                "value",
                column_resource_key(t_name, c_name),
            )
            column_info = await self._meta_repo.get_column_info(t_name, c_name)
            table_info = await self._meta_repo.get_table_info(t_name)
            cursor_column = table_info.value_index_cursor_column
            state = column_info.value_index_state
            if not column_info.index_values:
                raise RuntimeError("字段未启用取值索引")
            mode = self._select_value_sync_mode(
                cursor_column, state, requested_mode=requested_mode
            )
            generation = (
                uuid.uuid4()
                if mode == "full"
                else state.current_generation
                if state is not None
                else None
            )
            if generation is None:
                raise RuntimeError("字段取值增量同步缺少全量同步状态")
            await self._meta_repo.begin_value_index_sync(
                t_name,
                c_name,
                run_id=run_id,
                generation=generation,
                started_at=started_at,
            )
            return _ValueIndexRun(
                run_id=run_id,
                t_name=t_name,
                c_name=c_name,
                mode=mode,
                cursor_column=cursor_column,
                cursor_value=(
                    dict(state.cursor_value)
                    if state is not None and state.cursor_value is not None
                    else None
                ),
                generation=generation,
            )

    async def _execute_value_index_run(
        self,
        run: _ValueIndexRun,
    ) -> ValueIndexSyncResult:
        """在 PostgreSQL 事务外执行 Doris 和 Elasticsearch I/O。"""
        await self._value_repo.ensure_index()
        if run.mode == "full":
            return await self._run_full_value_sync(run)
        return await self._run_incremental_value_sync(run)

    async def _complete_value_index_run(
        self,
        run: _ValueIndexRun,
        result: ValueIndexSyncResult,
    ) -> None:
        """在短事务中校验运行快照并提交成功状态。"""
        async with self._meta_repo.session.begin():
            await self._meta_repo.acquire_index_lock(
                "value",
                column_resource_key(run.t_name, run.c_name),
            )
            column_info, table_info = await self._meta_repo.reload_value_index_context(
                run.t_name,
                run.c_name,
            )
            state = column_info.value_index_state
            if state is None or state.active_run_id != run.run_id:
                raise RuntimeError("字段取值索引同步运行所有权已失效")
            if (
                table_info.value_index_cursor_column != run.cursor_column
                or not column_info.index_values
            ):
                raise RuntimeError("字段取值索引同步配置已变化")
            if run.generation is None:
                raise RuntimeError("字段取值索引同步缺少代次")
            committed = await self._meta_repo.complete_value_index_sync(
                run.t_name,
                run.c_name,
                run_id=run.run_id,
                cursor_value=(
                    result.cursor_value
                    if isinstance(result.cursor_value, dict)
                    else run.cursor_value
                ),
                generation=run.generation,
                completed_at=datetime.now(UTC),
            )
            if not committed:
                raise RuntimeError("字段取值索引同步状态提交冲突")

    async def _fail_value_index_run(
        self,
        run: _ValueIndexRun,
        error: Exception,
    ) -> None:
        """在独立短事务中按 run_id 记录失败状态。"""
        async with self._meta_repo.session.begin():
            await self._meta_repo.acquire_index_lock(
                "value",
                column_resource_key(run.t_name, run.c_name),
            )
            await self._meta_repo.fail_value_index_sync(
                run.t_name,
                run.c_name,
                run_id=run.run_id,
                error=f"{type(error).__name__}: {error}",
                failed_at=datetime.now(UTC),
            )

    async def _run_full_value_sync(
        self,
        run: _ValueIndexRun,
    ) -> ValueIndexSyncResult:
        """执行字段取值索引全量替换。"""
        if run.generation is None:
            raise RuntimeError("字段取值索引全量同步缺少代次")
        upper_bound = (
            await self._value_upper_bound(
                run.t_name,
                run.cursor_column,
            )
            if run.cursor_column is not None
            else None
        )
        read_count = await self._upsert_value_batches(
            self._source_repo.iter_column_value_batches(
                run.t_name,
                run.c_name,
            ),
            run.t_name,
            run.c_name,
            run.generation,
        )
        if read_count:
            await self._value_repo.refresh()
        cursor_value = (
            self._serialize_cursor(upper_bound)
            if upper_bound is not None
            else run.cursor_value
        )
        return ValueIndexSyncResult(
            mode="full",
            read_value_count=read_count,
            upserted_count=read_count,
            cursor_value=cursor_value,
            sync_generation=str(run.generation),
        )

    async def _run_incremental_value_sync(
        self,
        run: _ValueIndexRun,
    ) -> ValueIndexSyncResult:
        """只读取已提交水位之后、固定上界以内的数据。"""
        if run.cursor_column is None or run.generation is None:
            raise RuntimeError("字段取值增量同步缺少已提交水位")
        upper_bound = await self._value_upper_bound(
            run.t_name,
            run.cursor_column,
        )
        previous_cursor = (
            self._deserialize_cursor(run.cursor_value)
            if run.cursor_value is not None
            else None
        )
        if upper_bound is None or (
            previous_cursor is not None and upper_bound <= previous_cursor
        ):
            logger.info("水位未推进，跳过 table={} column={}", run.t_name, run.c_name)
            return ValueIndexSyncResult(
                mode="incremental",
                read_value_count=0,
                upserted_count=0,
                cursor_value=run.cursor_value,
                sync_generation=str(run.generation),
            )
        read_count = await self._upsert_value_batches(
            self._source_repo.iter_changed_column_value_batches(
                run.t_name,
                run.c_name,
                run.cursor_column,
                previous_cursor,
                upper_bound,
            ),
            run.t_name,
            run.c_name,
            run.generation,
        )
        if read_count:
            await self._value_repo.refresh()
        return ValueIndexSyncResult(
            mode="incremental",
            read_value_count=read_count,
            upserted_count=read_count,
            cursor_value=self._serialize_cursor(upper_bound),
            sync_generation=str(run.generation),
        )

    async def _upsert_value_batches(
        self,
        batches: AsyncIterator[list[Any]],
        t_name: str,
        c_name: str,
        generation: uuid.UUID,
    ) -> int:
        """序列化并写入 Doris 返回的分批去重取值。"""
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
                await self._value_repo.upsert(value_infos, str(generation))
                count += len(value_infos)
        return count

    @staticmethod
    def _select_value_sync_mode(
        cursor_column: str | None,
        state: ValueIndexSyncState | None,
        *,
        requested_mode: ValueIndexSyncMode,
    ) -> ValueIndexSyncMode:
        """校验请求模式所需状态并选择同步模式。"""
        if requested_mode == "full":
            return "full"
        if state is None or state.current_generation is None:
            raise RuntimeError("字段取值增量同步缺少全量同步状态")
        if cursor_column is None:
            raise RuntimeError("字段取值增量同步缺少游标配置或已提交水位")
        return "incremental"

    @staticmethod
    def _column_payload(column_info: ColumnInfo) -> dict[str, Any]:
        """构造顺序稳定的字段语义索引载荷。"""
        return {
            "t_name": column_info.t_name,
            "name": column_info.name,
            "type": column_info.type,
            "examples": serialize_column_examples(column_info.examples),
            "description": column_info.description,
            "alias": sorted(dict.fromkeys(column_info.alias)),
            "index_values": column_info.index_values,
            "reference_t_name": column_info.reference_t_name,
            "reference_c_name": column_info.reference_c_name,
            "index_ready": True,
        }

    @staticmethod
    def _metric_payload(metric_info: MetricInfo) -> dict[str, Any]:
        """构造顺序稳定的指标语义索引载荷。"""
        return {
            "name": metric_info.name,
            "index_ready": True,
            "description": metric_info.description,
            "relevant_columns": sorted(
                metric_info.relevant_columns,
                key=lambda item: (item["t_name"], item["c_name"]),
            ),
            "alias": sorted(dict.fromkeys(metric_info.alias)),
        }

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """分批生成文本向量。"""
        embeddings: list[list[float]] = []
        for index in range(0, len(texts), self._embedding_batch_size):
            batch = texts[index : index + self._embedding_batch_size]
            embeddings.extend(await self._embedding_client.aembed_documents(batch))
        return embeddings

    @staticmethod
    def _serialize_cursor(value: Any) -> dict[str, object]:
        """将 Doris 类型化游标转换为 JSON 状态。"""
        if isinstance(value, datetime):
            return {"type": "datetime", "value": value.isoformat()}
        if isinstance(value, date):
            return {"type": "date", "value": value.isoformat()}
        if isinstance(value, Decimal):
            return {"type": "decimal", "value": str(value)}
        if isinstance(value, bool):
            return {"type": "bool", "value": value}
        if isinstance(value, int):
            return {"type": "int", "value": value}
        if isinstance(value, float):
            return {"type": "float", "value": value}
        if isinstance(value, str):
            return {"type": "str", "value": value}
        raise TypeError(f"不支持的取值索引游标类型: {type(value).__name__}")

    @staticmethod
    def _deserialize_cursor(payload: dict[str, Any]) -> Any:
        """恢复 JSON 状态中的 Doris 类型化游标。"""
        cursor_type = payload.get("type")
        value = payload.get("value")
        if cursor_type == "datetime" and isinstance(value, str):
            return datetime.fromisoformat(value)
        if cursor_type == "date" and isinstance(value, str):
            return date.fromisoformat(value)
        if cursor_type == "decimal" and isinstance(value, str):
            return Decimal(value)
        if cursor_type == "bool" and isinstance(value, bool):
            return value
        if cursor_type == "int" and isinstance(value, int):
            return value
        if cursor_type == "float" and isinstance(value, (int, float)):
            return float(value)
        if cursor_type == "str" and isinstance(value, str):
            return value
        raise ValueError("取值索引游标状态格式无效")

    @staticmethod
    def _serialize_value(value: Any) -> str:
        """将字段取值转换为索引文本。"""
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value)
