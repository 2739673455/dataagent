"""元数据语义索引差量同步与字段取值同步。"""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.metadata.contracts import (
    ColumnKey,
    RequestedValueIndexSyncMode,
    SemanticIndexDelta,
    SemanticIndexDocument,
    SemanticIndexSyncResult,
    SemanticTextType,
    ValueIndexSyncMode,
    ValueIndexSyncResult,
)
from app.metadata.models.catalog import (
    ColumnInfo,
    MetricInfo,
    ValueIndexSyncState,
    ValueInfo,
    column_resource_key,
    serialize_column_examples,
)
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.semantic_index import SemanticIndexRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.shared.config.app_config import cfg

if TYPE_CHECKING:
    from app.shared.clients.embedding_client import EmbeddingClient

_SEMANTIC_PREPROCESS_VERSION = "v1"


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
    column_meta_version: int
    table_meta_version: int


class MetaIndexService:
    """同步字段、字段值和指标检索索引。"""

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

    async def sync_table_indexes(
        self,
        table_names: list[str],
    ) -> dict[ColumnKey, SemanticIndexSyncResult]:
        """同步多个表下全部字段的语义索引。"""
        column_keys = await self._get_column_keys_by_table_names(table_names)
        return await self.sync_column_indexes(column_keys)

    async def sync_column_indexes(
        self,
        column_keys: list[ColumnKey],
    ) -> dict[ColumnKey, SemanticIndexSyncResult]:
        """按资源锁差量同步字段语义索引，并条件提交索引版本。"""
        return await self._sync_semantic_indexes(
            column_keys,
            resource_type="column",
            repo=self._column_repo,
            resource_key=lambda key: column_resource_key(*key),
            load=lambda key: self._meta_repo.get_column_info(*key),
            payload_of=self._column_payload,
            mark_indexed=lambda key, version: (
                self._meta_repo.mark_column_indexed_if_current(*key, version)
            ),
        )

    async def sync_metric_indexes(
        self,
        metric_names: list[str],
    ) -> dict[str, SemanticIndexSyncResult]:
        """按有界批次同步指标，持锁直到向量、ES 写入和版本检查完成。"""
        return await self._sync_semantic_indexes(
            metric_names,
            resource_type="metric",
            repo=self._metric_repo,
            resource_key=lambda key: key,
            load=self._meta_repo.get_metric_info,
            payload_of=self._metric_payload,
            mark_indexed=self._meta_repo.mark_metric_indexed_if_current,
        )

    async def sync_table_values(
        self,
        table_names: list[str],
        *,
        mode: RequestedValueIndexSyncMode,
    ) -> dict[ColumnKey, ValueIndexSyncResult]:
        """同步多个表下已开启字段的取值索引。"""
        column_keys = await self._get_column_keys_by_table_names(
            table_names,
            index_values=True,
        )
        return await self.sync_column_values(
            column_keys,
            mode=mode,
        )

    async def sync_column_values(
        self,
        column_keys: list[ColumnKey],
        *,
        mode: RequestedValueIndexSyncMode,
    ) -> dict[ColumnKey, ValueIndexSyncResult]:
        """按水位或全量校准模式同步多个字段取值。"""
        results: dict[ColumnKey, ValueIndexSyncResult] = {}
        for column_key in dict.fromkeys(column_keys):
            results[column_key] = await self._sync_column_value_index(
                *column_key,
                requested_mode=mode,
            )
        return results

    async def delete_column_indexes(self, column_keys: list[ColumnKey]) -> None:
        """删除多个字段的语义和取值索引。"""
        for t_name, c_name in dict.fromkeys(column_keys):
            await self._column_repo.delete(t_name, c_name)
            await self._value_repo.delete_by_column(t_name, c_name)

    async def delete_metric_indexes(self, metric_names: list[str]) -> None:
        """删除多个指标的语义索引。"""
        for metric_name in dict.fromkeys(metric_names):
            await self._metric_repo.delete(metric_name)

    async def _sync_semantic_indexes[KeyT, ItemT: (ColumnInfo, MetricInfo)](
        self,
        keys: list[KeyT],
        *,
        resource_type: str,
        repo: SemanticIndexRepo[ItemT, KeyT],
        resource_key: Callable[[KeyT], str],
        load: Callable[[KeyT], Awaitable[ItemT]],
        payload_of: Callable[[ItemT], dict[str, Any]],
        mark_indexed: Callable[[KeyT, int], Awaitable[bool]],
    ) -> dict[KeyT, SemanticIndexSyncResult]:
        """同类资源批量向量化与写入，失败批次不确认版本，先前批次保留提交。"""
        unique_keys = list(dict.fromkeys(keys))
        if not unique_keys:
            return {}
        await repo.ensure_index()
        results: dict[KeyT, SemanticIndexSyncResult] = {}
        batch_size = cfg.metadata_index.semantic_resource_batch_size
        for offset in range(0, len(unique_keys), batch_size):
            batch = unique_keys[offset : offset + batch_size]
            async with self._meta_repo.session.begin():
                # 所有批次采用相同锁顺序，避免重叠资源以不同输入顺序加锁时死锁。
                for key in sorted(batch, key=resource_key):
                    await self._meta_repo.acquire_index_lock(
                        resource_type, resource_key(key)
                    )
                prepared = []
                embedding_targets: list[
                    tuple[list[SemanticIndexDocument], int, SemanticIndexDocument]
                ] = []
                for key in batch:
                    info = await load(key)
                    targets = self._target_semantic_documents(
                        resource_type,
                        resource_key(key),
                        info.meta_version,
                        payload_of(info),
                        info.name,
                        info.description,
                        info.alias,
                    )
                    current = await repo.list_resource_documents(resource_key(key))
                    delta, pending = self._semantic_delta(targets, current)
                    prepared.append((key, info.meta_version, delta, len(pending)))
                    embedding_targets.extend(pending)
                embeddings = await self._embed_texts(
                    [target.text for _, _, target in embedding_targets]
                )
                for (documents, index, target), embedding in zip(
                    embedding_targets, embeddings, strict=True
                ):
                    documents[index] = replace(target, embedding=embedding)
                await repo.apply_delta(
                    SemanticIndexDelta(
                        create=[
                            document
                            for _, _, delta, _ in prepared
                            for document in delta.create
                        ],
                        update=[
                            document
                            for _, _, delta, _ in prepared
                            for document in delta.update
                        ],
                        delete_ids=[
                            document_id
                            for _, _, delta, _ in prepared
                            for document_id in delta.delete_ids
                        ],
                        unchanged_count=sum(
                            delta.unchanged_count for _, _, delta, _ in prepared
                        ),
                    )
                )
                batch_results = {}
                for key, version, delta, embedded_count in prepared:
                    committed = await mark_indexed(key, version)
                    batch_results[key] = replace(
                        self._semantic_result(delta, embedded_count, version),
                        version_committed=committed,
                    )
            results.update(batch_results)
        return results

    async def _get_column_keys_by_table_names(
        self,
        table_names: list[str],
        *,
        index_values: bool | None = None,
    ) -> list[ColumnKey]:
        """根据多个表名获取字段键。"""
        async with self._meta_repo.session.begin():
            column_infos = await self._meta_repo.list_column_infos_by_table_names(
                table_names,
                index_values=index_values,
            )
        return [(column_info.t_name, column_info.name) for column_info in column_infos]

    def _target_semantic_documents(
        self,
        resource_type: str,
        resource_key: str,
        meta_version: int,
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
        payload_hash = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        embedding_revision = self._embedding_revision()
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
                text_type=text_type,
                embedding=None,
                embedding_revision=embedding_revision,
                meta_version=meta_version,
                payload_hash=payload_hash,
                payload=payload,
            )
            for text_value, text_type in sorted(entries.items())
        ]

    @staticmethod
    def _semantic_delta(
        targets: list[SemanticIndexDocument],
        current: list[SemanticIndexDocument],
    ) -> tuple[
        SemanticIndexDelta,
        list[tuple[list[SemanticIndexDocument], int, SemanticIndexDocument]],
    ]:
        """计算差异并标记需向量化的文档，载荷更新保留原向量。"""
        current_by_id = {document.id: document for document in current}
        target_ids = {document.id for document in targets}
        create: list[SemanticIndexDocument] = []
        update: list[SemanticIndexDocument] = []
        unchanged_count = 0
        embedding_targets: list[
            tuple[list[SemanticIndexDocument], int, SemanticIndexDocument]
        ] = []
        for target in targets:
            existing = current_by_id.get(target.id)
            if existing is None:
                embedding_targets.append((create, len(create), target))
                create.append(target)
                continue
            needs_embedding = (
                existing.text != target.text
                or existing.embedding_revision != target.embedding_revision
            )
            changed = needs_embedding or any(
                (
                    existing.resource_key != target.resource_key,
                    existing.text_type != target.text_type,
                    existing.meta_version != target.meta_version,
                    existing.payload_hash != target.payload_hash,
                )
            )
            if not changed:
                unchanged_count += 1
                continue
            if needs_embedding:
                embedding_targets.append((update, len(update), target))
            update.append(target)

        return (
            SemanticIndexDelta(
                create=create,
                update=update,
                delete_ids=sorted(
                    document.id for document in current if document.id not in target_ids
                ),
                unchanged_count=unchanged_count,
            ),
            embedding_targets,
        )

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """分批生成文本向量。"""
        embeddings: list[list[float]] = []
        for index in range(0, len(texts), cfg.embedding.batch_size):
            batch = texts[index : index + cfg.embedding.batch_size]
            embeddings.extend(await self._embedding_client.aembed_documents(batch))
        return embeddings

    @staticmethod
    def _embedding_revision() -> str:
        """生成当前嵌入模型和预处理规则版本。"""
        return (
            f"openai-compatible:{cfg.embedding.model}:"
            f"{cfg.elasticsearch.embedding_size}:{_SEMANTIC_PREPROCESS_VERSION}"
        )

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
            "meta_version": column_info.meta_version,
            "index_version": column_info.meta_version,
        }

    @staticmethod
    def _metric_payload(metric_info: MetricInfo) -> dict[str, Any]:
        """构造顺序稳定的指标语义索引载荷。"""
        return {
            "name": metric_info.name,
            "description": metric_info.description,
            "relevant_columns": sorted(
                metric_info.relevant_columns,
                key=lambda item: (item["t_name"], item["c_name"]),
            ),
            "alias": sorted(dict.fromkeys(metric_info.alias)),
            "meta_version": metric_info.meta_version,
            "index_version": metric_info.meta_version,
        }

    @staticmethod
    def _semantic_result(
        delta: SemanticIndexDelta,
        embedded_count: int,
        target_version: int,
    ) -> SemanticIndexSyncResult:
        """汇总语义索引差量统计。"""
        return SemanticIndexSyncResult(
            created_count=len(delta.create),
            updated_count=len(delta.update),
            deleted_count=len(delta.delete_ids),
            embedded_count=embedded_count,
            unchanged_count=delta.unchanged_count,
            target_version=target_version,
            version_committed=False,
        )

    async def _sync_column_value_index(
        self,
        t_name: str,
        c_name: str,
        *,
        requested_mode: RequestedValueIndexSyncMode,
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
            return result
        except Exception as exc:
            await self._fail_value_index_run(run, exc)
            raise

    async def _begin_value_index_run(
        self,
        t_name: str,
        c_name: str,
        *,
        requested_mode: RequestedValueIndexSyncMode,
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
            if (
                state is not None
                and state.status == "syncing"
                and state.active_run_id is not None
            ):
                raise RuntimeError("字段取值索引已有运行中的同步任务")
            if column_info.index_values:
                mode: ValueIndexSyncMode = self._select_value_sync_mode(
                    cursor_column,
                    state,
                    requested_mode=requested_mode,
                )
                generation = (
                    uuid.uuid4()
                    if mode == "full"
                    else state.current_generation
                    if state is not None
                    else None
                )
                if generation is None:
                    mode = "full"
                    generation = uuid.uuid4()
            else:
                mode = "clear"
                generation = None
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
                column_meta_version=column_info.meta_version,
                table_meta_version=table_info.meta_version,
            )

    @staticmethod
    def _select_value_sync_mode(
        cursor_column: str | None,
        state: ValueIndexSyncState | None,
        *,
        requested_mode: RequestedValueIndexSyncMode,
    ) -> ValueIndexSyncMode:
        """校验请求模式所需状态并选择同步模式。"""
        if requested_mode == "full":
            return "full"
        if state is None or state.current_generation is None:
            raise RuntimeError("字段取值增量同步缺少全量同步状态")
        if cursor_column is None or state.cursor_value is None:
            raise RuntimeError("字段取值增量同步缺少游标配置或已提交水位")
        return "incremental"

    async def _execute_value_index_run(
        self,
        run: _ValueIndexRun,
    ) -> ValueIndexSyncResult:
        """在 PostgreSQL 事务外执行 Doris 和 Elasticsearch I/O。"""
        if run.mode == "clear":
            removed_count = await self._value_repo.delete_by_column(
                run.t_name,
                run.c_name,
            )
            return ValueIndexSyncResult(
                mode="clear",
                read_value_count=0,
                upserted_count=0,
                removed_count=removed_count,
                cursor_value=None,
                sync_generation=None,
            )
        await self._value_repo.ensure_index()
        if run.mode == "full":
            return await self._run_full_value_sync(run)
        return await self._run_incremental_value_sync(run)

    async def _run_full_value_sync(
        self,
        run: _ValueIndexRun,
    ) -> ValueIndexSyncResult:
        """全量写入本次代次的取值，再删除其他代次的文档。"""
        if run.generation is None:
            raise RuntimeError("字段取值索引全量同步缺少代次")
        upper_bound = (
            await self._source_repo.get_value_sync_upper_bound(
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
        removed_count = await self._value_repo.delete_other_generations(
            run.t_name,
            run.c_name,
            str(run.generation),
        )
        cursor_value = (
            self._serialize_cursor(upper_bound)
            if upper_bound is not None
            else run.cursor_value
        )
        return ValueIndexSyncResult(
            mode="full",
            read_value_count=read_count,
            upserted_count=read_count,
            removed_count=removed_count,
            cursor_value=cursor_value,
            sync_generation=str(run.generation),
        )

    async def _run_incremental_value_sync(
        self,
        run: _ValueIndexRun,
    ) -> ValueIndexSyncResult:
        """按固定上界和回看窗口同步取值，沿用已提交代次。"""
        if (
            run.cursor_column is None
            or run.cursor_value is None
            or run.generation is None
        ):
            raise RuntimeError("字段取值增量同步缺少已提交水位")
        upper_bound = await self._source_repo.get_value_sync_upper_bound(
            run.t_name,
            run.cursor_column,
        )
        if upper_bound is None:
            return ValueIndexSyncResult(
                mode="incremental",
                read_value_count=0,
                upserted_count=0,
                removed_count=0,
                cursor_value=run.cursor_value,
                sync_generation=str(run.generation),
            )
        previous_cursor = self._deserialize_cursor(run.cursor_value)
        lower_bound = self._lookback_lower_bound(
            previous_cursor,
            cfg.metadata_index.value_lookback_seconds,
        )
        read_count = await self._upsert_value_batches(
            self._source_repo.iter_changed_column_value_batches(
                run.t_name,
                run.c_name,
                run.cursor_column,
                lower_bound,
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
            removed_count=0,
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
        """序列化并写入非空取值，返回包含覆盖写入在内的数量。"""
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
                column_info.meta_version != run.column_meta_version
                or table_info.meta_version != run.table_meta_version
                or table_info.value_index_cursor_column != run.cursor_column
                or column_info.index_values != (run.mode != "clear")
            ):
                raise RuntimeError("字段取值索引同步配置已变化")
            if run.mode == "clear":
                await self._meta_repo.delete_value_index_state(
                    run.t_name,
                    run.c_name,
                )
                return
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
                full_sync=run.mode == "full",
                incremental_sync=run.mode == "incremental",
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

    @staticmethod
    def _serialize_cursor(value: Any) -> dict[str, object]:
        """将日期、小数及精确基础类型编码为带类型标记的 JSON 水位。"""
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
        """按类型标记恢复水位，数字水位不接受布尔值。"""
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
    def _lookback_lower_bound(cursor: Any, lookback_seconds: int) -> Any:
        """对时间游标应用回看窗口并重放其他类型边界。"""
        if isinstance(cursor, datetime):
            return cursor - timedelta(seconds=lookback_seconds)
        if isinstance(cursor, date):
            lookback_days = max(1, (lookback_seconds + 86_399) // 86_400)
            return cursor - timedelta(days=lookback_days)
        return cursor

    @staticmethod
    def _serialize_value(value: Any) -> str:
        """将字段取值转换为索引文本。"""
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value)
