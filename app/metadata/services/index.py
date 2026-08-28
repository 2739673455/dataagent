"""元数据检索索引增量同步服务"""

import hashlib
import json
import unicodedata
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

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
    RequestedValueIndexSyncMode,
    SemanticIndexDelta,
    SemanticIndexDocument,
    SemanticIndexSyncResult,
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
from app.shared.config.app_config import cfg

_SEMANTIC_PREPROCESS_VERSION = "v1"


class MetaIndexService:
    """同步字段、字段值和指标检索索引"""

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
        """初始化元数据检索索引同步服务"""
        self._meta_repo = meta_repo
        self._source_repo = source_repo
        self._column_repo = column_repo
        self._metric_repo = metric_repo
        self._embedding_client = embedding_client
        self._value_repo = value_repo

    async def sync_column_indexes(
        self,
        column_keys: list[ColumnKey],
    ) -> dict[ColumnKey, SemanticIndexSyncResult]:
        """差量同步多个字段的语义索引"""
        results: dict[ColumnKey, SemanticIndexSyncResult] = {}
        for t_name, c_name in dict.fromkeys(column_keys):
            resource_key = column_resource_key(t_name, c_name)
            async with self._meta_repo.session.begin():
                await self._meta_repo.acquire_index_lock("column", resource_key)
                column_info = await self._meta_repo.get_column_info(t_name, c_name)
                result = await self._sync_column_index(column_info)
                committed = await self._meta_repo.mark_column_indexed_if_current(
                    t_name,
                    c_name,
                    result.target_version,
                )
            results[(t_name, c_name)] = replace(
                result,
                version_committed=committed,
            )
        return results

    async def sync_metric_indexes(
        self,
        metric_names: list[str],
    ) -> dict[str, SemanticIndexSyncResult]:
        """差量同步多个指标的语义索引"""
        results: dict[str, SemanticIndexSyncResult] = {}
        for metric_name in dict.fromkeys(metric_names):
            async with self._meta_repo.session.begin():
                await self._meta_repo.acquire_index_lock("metric", metric_name)
                metric_info = await self._meta_repo.get_metric_info(metric_name)
                result = await self._sync_metric_index(metric_info)
                committed = await self._meta_repo.mark_metric_indexed_if_current(
                    metric_name,
                    result.target_version,
                )
            results[metric_name] = replace(
                result,
                version_committed=committed,
            )
        return results

    async def sync_column_values(
        self,
        column_keys: list[ColumnKey],
        *,
        mode: RequestedValueIndexSyncMode,
    ) -> dict[ColumnKey, ValueIndexSyncResult]:
        """按水位或全量校准模式同步多个字段取值"""
        results: dict[ColumnKey, ValueIndexSyncResult] = {}
        for column_key in dict.fromkeys(column_keys):
            results[column_key] = await self._sync_column_value_index(
                *column_key,
                requested_mode=mode,
            )
        return results

    async def sync_table_indexes(
        self,
        table_names: list[str],
    ) -> dict[ColumnKey, SemanticIndexSyncResult]:
        """同步多个表下全部字段的语义索引"""
        column_keys = await self._get_column_keys_by_table_names(table_names)
        return await self.sync_column_indexes(column_keys)

    async def sync_table_values(
        self,
        table_names: list[str],
        *,
        mode: RequestedValueIndexSyncMode,
    ) -> dict[ColumnKey, ValueIndexSyncResult]:
        """同步多个表下已开启字段的取值索引"""
        column_keys = await self._get_column_keys_by_table_names(
            table_names,
            index_values=True,
        )
        return await self.sync_column_values(
            column_keys,
            mode=mode,
        )

    async def _get_column_keys_by_table_names(
        self,
        table_names: list[str],
        *,
        index_values: bool | None = None,
    ) -> list[ColumnKey]:
        """根据多个表名获取字段键"""
        async with self._meta_repo.session.begin():
            column_infos = await self._meta_repo.list_column_infos_by_table_names(
                table_names,
                index_values=index_values,
            )
        return [(column_info.t_name, column_info.name) for column_info in column_infos]

    async def delete_column_indexes(self, column_keys: list[ColumnKey]) -> None:
        """删除多个字段的语义和取值索引"""
        for t_name, c_name in dict.fromkeys(column_keys):
            await self._column_repo.delete(t_name, c_name)
            await self._value_repo.delete_by_column(t_name, c_name)

    async def delete_metric_indexes(self, metric_names: list[str]) -> None:
        """删除多个指标的语义索引"""
        for metric_name in dict.fromkeys(metric_names):
            await self._metric_repo.delete(metric_name)

    async def _sync_column_index(
        self,
        column_info: ColumnInfo,
    ) -> SemanticIndexSyncResult:
        """差量替换字段内部发生变化的语义文档"""
        await self._column_repo.ensure_index()
        resource_key = column_resource_key(column_info.t_name, column_info.name)
        payload = self._column_payload(column_info)
        targets = self._target_semantic_documents(
            "column",
            resource_key,
            column_info.meta_version,
            payload,
            column_info.name,
            column_info.description,
            column_info.alias,
        )
        current = await self._column_repo.list_resource_documents(
            resource_key,
        )
        delta, embedded_count = await self._semantic_delta(targets, current)
        await self._column_repo.apply_delta(delta)
        return self._semantic_result(delta, embedded_count, column_info.meta_version)

    async def _sync_metric_index(
        self,
        metric_info: MetricInfo,
    ) -> SemanticIndexSyncResult:
        """差量替换指标内部发生变化的语义文档"""
        await self._metric_repo.ensure_index()
        payload = self._metric_payload(metric_info)
        targets = self._target_semantic_documents(
            "metric",
            metric_info.name,
            metric_info.meta_version,
            payload,
            metric_info.name,
            metric_info.description,
            metric_info.alias,
        )
        current = await self._metric_repo.list_resource_documents(metric_info.name)
        delta, embedded_count = await self._semantic_delta(targets, current)
        await self._metric_repo.apply_delta(delta)
        return self._semantic_result(delta, embedded_count, metric_info.meta_version)

    async def _semantic_delta(
        self,
        targets: list[SemanticIndexDocument],
        current: list[SemanticIndexDocument],
    ) -> tuple[SemanticIndexDelta, int]:
        """计算文档差异并只补充必要的向量"""
        current_by_id = {document.id: document for document in current}
        target_ids = {document.id for document in targets}
        create: list[SemanticIndexDocument] = []
        update: list[SemanticIndexDocument] = []
        unchanged_count = 0
        embedding_targets: list[tuple[str, int, SemanticIndexDocument]] = []
        for target in targets:
            existing = current_by_id.get(target.id)
            if existing is None:
                embedding_targets.append(("create", len(create), target))
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
                embedding_targets.append(("update", len(update), target))
            update.append(target)

        if embedding_targets:
            embeddings = await self._embed_texts(
                [target.text for _, _, target in embedding_targets]
            )
            for (operation, index, target), embedding in zip(
                embedding_targets,
                embeddings,
                strict=True,
            ):
                embedded = replace(target, embedding=embedding)
                if operation == "create":
                    create[index] = embedded
                else:
                    update[index] = embedded

        return (
            SemanticIndexDelta(
                create=create,
                update=update,
                delete_ids=sorted(
                    document.id for document in current if document.id not in target_ids
                ),
                unchanged_count=unchanged_count,
            ),
            len(embedding_targets),
        )

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
        """生成规范化、去重且编号稳定的目标文档"""
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

    async def _sync_column_value_index(
        self,
        t_name: str,
        c_name: str,
        *,
        requested_mode: RequestedValueIndexSyncMode,
    ) -> ValueIndexSyncResult:
        """执行单字段取值索引状态机"""
        run_id = uuid.uuid4()
        started_at = datetime.now(UTC)
        async with self._meta_repo.session.begin():
            await self._meta_repo.acquire_index_lock(
                "value",
                column_resource_key(t_name, c_name),
            )
            column_info = await self._meta_repo.get_column_info(t_name, c_name)
            if not column_info.index_values:
                return await self._clear_value_index(column_info)
            table_info = await self._meta_repo.get_table_info(t_name)
            cursor_column = table_info.value_index_cursor_column
            state = column_info.value_index_state
            mode = self._select_value_sync_mode(
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
            await self._meta_repo.begin_value_index_sync(
                t_name,
                c_name,
                run_id=run_id,
                generation=generation,
                started_at=started_at,
            )

        try:
            async with self._meta_repo.session.begin():
                await self._meta_repo.acquire_index_lock(
                    "value",
                    column_resource_key(t_name, c_name),
                )
                column_info = await self._meta_repo.get_column_info(t_name, c_name)
                state = column_info.value_index_state
                if state is None or state.active_run_id != run_id:
                    raise RuntimeError("字段取值索引同步运行所有权已失效")
                if not column_info.index_values:
                    return await self._clear_value_index(column_info)
                current_cursor_column = (
                    await self._meta_repo.get_table_info(t_name)
                ).value_index_cursor_column
                if current_cursor_column != cursor_column:
                    raise RuntimeError("字段取值索引同步配置已变化")
                await self._value_repo.ensure_index()
                if mode == "full":
                    result = await self._run_full_value_sync(
                        column_info,
                        state,
                        cursor_column,
                        generation,
                    )
                else:
                    result = await self._run_incremental_value_sync(
                        column_info,
                        state,
                        cursor_column,
                        generation,
                    )
                committed = await self._meta_repo.complete_value_index_sync(
                    t_name,
                    c_name,
                    run_id=run_id,
                    cursor_value=(
                        result.cursor_value
                        if isinstance(result.cursor_value, dict)
                        else state.cursor_value
                    ),
                    generation=generation,
                    completed_at=datetime.now(UTC),
                    full_sync=mode == "full",
                    incremental_sync=mode == "incremental",
                )
                if not committed:
                    raise RuntimeError("字段取值索引同步状态提交冲突")
                return result
        except Exception as exc:
            async with self._meta_repo.session.begin():
                await self._meta_repo.acquire_index_lock(
                    "value",
                    column_resource_key(t_name, c_name),
                )
                await self._meta_repo.fail_value_index_sync(
                    t_name,
                    c_name,
                    run_id=run_id,
                    error=f"{type(exc).__name__}: {exc}",
                    failed_at=datetime.now(UTC),
                )
            raise

    async def _run_full_value_sync(
        self,
        column_info: ColumnInfo,
        state: ValueIndexSyncState,
        cursor_column: str | None,
        generation: uuid.UUID,
    ) -> ValueIndexSyncResult:
        """执行字段取值索引全量替换"""
        upper_bound = (
            await self._source_repo.get_value_sync_upper_bound(
                column_info.t_name,
                cursor_column,
            )
            if cursor_column is not None
            else None
        )
        read_count = await self._upsert_value_batches(
            self._source_repo.iter_column_value_batches(
                column_info.t_name,
                column_info.name,
            ),
            column_info,
            generation,
        )
        if read_count:
            await self._value_repo.refresh()
        removed_count = await self._value_repo.delete_other_generations(
            column_info.t_name,
            column_info.name,
            str(generation),
        )
        cursor_value = (
            self._serialize_cursor(upper_bound)
            if upper_bound is not None
            else state.cursor_value
        )
        return ValueIndexSyncResult(
            mode="full",
            read_value_count=read_count,
            upserted_count=read_count,
            removed_count=removed_count,
            cursor_value=cursor_value,
            sync_generation=str(generation),
        )

    async def _run_incremental_value_sync(
        self,
        column_info: ColumnInfo,
        state: ValueIndexSyncState,
        cursor_column: str | None,
        generation: uuid.UUID,
    ) -> ValueIndexSyncResult:
        """执行固定上界和重叠窗口的日常水位同步"""
        if cursor_column is None or state.cursor_value is None:
            raise RuntimeError("字段取值增量同步缺少已提交水位")
        upper_bound = await self._source_repo.get_value_sync_upper_bound(
            column_info.t_name,
            cursor_column,
        )
        if upper_bound is None:
            return ValueIndexSyncResult(
                mode="incremental",
                read_value_count=0,
                upserted_count=0,
                removed_count=0,
                cursor_value=state.cursor_value,
                sync_generation=str(generation),
            )
        previous_cursor = self._deserialize_cursor(state.cursor_value)
        lower_bound = self._lookback_lower_bound(
            previous_cursor,
            cfg.metadata_index.value_lookback_seconds,
        )
        read_count = await self._upsert_value_batches(
            self._source_repo.iter_changed_column_value_batches(
                column_info.t_name,
                column_info.name,
                cursor_column,
                lower_bound,
                upper_bound,
            ),
            column_info,
            generation,
        )
        if read_count:
            await self._value_repo.refresh()
        return ValueIndexSyncResult(
            mode="incremental",
            read_value_count=read_count,
            upserted_count=read_count,
            removed_count=0,
            cursor_value=self._serialize_cursor(upper_bound),
            sync_generation=str(generation),
        )

    async def _upsert_value_batches(
        self,
        batches: AsyncIterator[list[Any]],
        column_info: ColumnInfo,
        generation: uuid.UUID,
    ) -> int:
        """序列化并写入 Doris 返回的分批去重取值"""
        count = 0
        async for values in batches:
            value_infos = [
                ValueInfo(
                    value=self._serialize_value(value),
                    t_name=column_info.t_name,
                    c_name=column_info.name,
                )
                for value in values
                if value is not None
            ]
            if value_infos:
                await self._value_repo.upsert(value_infos, str(generation))
                count += len(value_infos)
        return count

    async def _clear_value_index(
        self,
        column_info: ColumnInfo,
    ) -> ValueIndexSyncResult:
        """清理已关闭字段的取值索引与同步状态"""
        removed_count = await self._value_repo.delete_by_column(
            column_info.t_name,
            column_info.name,
        )
        await self._meta_repo.delete_value_index_state(
            column_info.t_name,
            column_info.name,
        )
        return ValueIndexSyncResult(
            mode="clear",
            read_value_count=0,
            upserted_count=0,
            removed_count=removed_count,
            cursor_value=None,
            sync_generation=None,
        )

    @staticmethod
    def _select_value_sync_mode(
        cursor_column: str | None,
        state: ValueIndexSyncState | None,
        *,
        requested_mode: RequestedValueIndexSyncMode,
    ) -> ValueIndexSyncMode:
        """校验请求模式所需状态并选择同步模式"""
        if requested_mode == "full":
            return "full"
        if state is None or state.current_generation is None:
            raise RuntimeError("字段取值增量同步缺少全量同步状态")
        if cursor_column is None or state.cursor_value is None:
            raise RuntimeError("字段取值增量同步缺少游标配置或已提交水位")
        return "incremental"

    @staticmethod
    def _column_payload(column_info: ColumnInfo) -> dict[str, Any]:
        """构造顺序稳定的字段语义索引载荷"""
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
        """构造顺序稳定的指标语义索引载荷"""
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
        """汇总语义索引差量统计"""
        return SemanticIndexSyncResult(
            created_count=len(delta.create),
            updated_count=len(delta.update),
            deleted_count=len(delta.delete_ids),
            embedded_count=embedded_count,
            unchanged_count=delta.unchanged_count,
            target_version=target_version,
            version_committed=False,
        )

    @staticmethod
    def _embedding_revision() -> str:
        """生成当前嵌入模型和预处理规则版本"""
        return (
            f"openai-compatible:{cfg.embedding.model}:"
            f"{cfg.elasticsearch.embedding_size}:{_SEMANTIC_PREPROCESS_VERSION}"
        )

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """分批生成文本向量"""
        embeddings: list[list[float]] = []
        for index in range(0, len(texts), self._embedding_batch_size):
            batch = texts[index : index + self._embedding_batch_size]
            embeddings.extend(await self._embedding_client.aembed_documents(batch))
        if len(embeddings) != len(texts):
            raise ValueError("语义索引向量生成数量不匹配")
        return embeddings

    @staticmethod
    def _serialize_cursor(value: Any) -> dict[str, object]:
        """将 Doris 类型化游标转换为 JSON 状态"""
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
        """恢复 JSON 状态中的 Doris 类型化游标"""
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
    def _lookback_lower_bound(cursor: Any, lookback_seconds: int) -> Any:
        """对时间游标应用回看窗口并重放其他类型边界"""
        if isinstance(cursor, datetime):
            return cursor - timedelta(seconds=lookback_seconds)
        if isinstance(cursor, date):
            lookback_days = max(1, (lookback_seconds + 86_399) // 86_400)
            return cursor - timedelta(days=lookback_days)
        return cursor

    @staticmethod
    def _serialize_value(value: Any) -> str:
        """将字段取值转换为索引文本"""
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value)
