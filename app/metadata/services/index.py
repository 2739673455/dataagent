"""元数据检索索引同步服务"""

import uuid
from datetime import date, datetime
from typing import Any

from app.metadata.models import ColumnInfo, ColumnKey, MetricInfo, ValueInfo
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.search_models import SemanticTextType
from app.shared.clients.embedding_client_manager import EmbeddingClient


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
        self, column_keys: list[ColumnKey]
    ) -> dict[ColumnKey, int]:
        """同步多个字段的语义索引"""
        async with self._meta_repo.session.begin():
            unique_column_keys = list(dict.fromkeys(column_keys))
            column_infos = [
                await self._meta_repo.get_column_info(*column_key)
                for column_key in unique_column_keys
            ]
        results: dict[ColumnKey, int] = {}
        for column_info in column_infos:
            column_key = (column_info.t_name, column_info.name)
            results[column_key] = await self._sync_column_index(column_info)
            async with self._meta_repo.session.begin():
                self._meta_repo.mark_column_indexed(column_info)
        return results

    async def sync_column_values(
        self, column_keys: list[ColumnKey]
    ) -> dict[ColumnKey, int]:
        """同步多个字段的取值索引"""
        async with self._meta_repo.session.begin():
            unique_column_keys = list(dict.fromkeys(column_keys))
            column_infos = [
                await self._meta_repo.get_column_info(*column_key)
                for column_key in unique_column_keys
            ]
        results: dict[ColumnKey, int] = {}
        for column_info in column_infos:
            column_key = (column_info.t_name, column_info.name)
            if not column_info.index_values:
                await self._clear_column_values(*column_key)
                continue
            async with self._meta_repo.session.begin():
                self._meta_repo.mark_column_values_syncing(column_info)
            try:
                results[column_key] = await self._sync_column_values(column_info)
            except Exception:
                async with self._meta_repo.session.begin():
                    self._meta_repo.mark_column_values_failed(column_info)
                raise
            async with self._meta_repo.session.begin():
                self._meta_repo.mark_column_values_succeeded(column_info)
        return results

    async def sync_table_indexes(
        self, table_names: list[str]
    ) -> dict[ColumnKey, int]:
        """同步多个表下全部字段的语义索引"""
        column_keys = await self._get_column_keys_by_table_names(table_names)
        return await self.sync_column_indexes(column_keys)

    async def sync_table_values(
        self, table_names: list[str]
    ) -> dict[ColumnKey, int]:
        """同步多个表下已开启字段的取值索引"""
        column_keys = await self._get_column_keys_by_table_names(
            table_names,
            index_values=True,
        )
        return await self.sync_column_values(column_keys)

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

    async def sync_metric_indexes(self, metric_names: list[str]) -> dict[str, int]:
        """同步多个指标的语义索引"""
        async with self._meta_repo.session.begin():
            unique_metric_names = list(dict.fromkeys(metric_names))
            metric_infos = [
                await self._meta_repo.get_metric_info(metric_name)
                for metric_name in unique_metric_names
            ]
        results: dict[str, int] = {}
        for metric_info in metric_infos:
            results[metric_info.name] = await self._sync_metric_index(metric_info)
            async with self._meta_repo.session.begin():
                self._meta_repo.mark_metric_indexed(metric_info)
        return results

    async def delete_column_indexes(self, column_keys: list[ColumnKey]) -> None:
        """删除多个字段的语义和取值索引"""
        for t_name, c_name in dict.fromkeys(column_keys):
            await self._column_repo.delete(t_name, c_name)
            await self._value_repo.delete_by_column(t_name, c_name)

    async def delete_metric_indexes(self, metric_names: list[str]) -> None:
        """删除多个指标的语义索引"""
        for metric_name in dict.fromkeys(metric_names):
            await self._metric_repo.delete(metric_name)

    async def _sync_column_index(self, column_info: ColumnInfo) -> int:
        """替换字段的全部语义索引"""
        await self._column_repo.ensure_index()
        index_entries = self._get_index_entries(
            column_info.name,
            column_info.description,
            column_info.alias,
        )
        texts = [text for text, _ in index_entries]
        text_types: list[SemanticTextType] = [
            text_type for _, text_type in index_entries
        ]
        embeddings = await self._embed_texts(texts)
        resource_key = f"{column_info.t_name}.{column_info.name}"
        point_ids = self._get_point_ids("column", resource_key, texts)

        await self._column_repo.delete(
            column_info.t_name,
            column_info.name,
        )
        await self._column_repo.index(
            point_ids,
            texts,
            text_types,
            embeddings,
            column_info,
        )
        await self._column_repo.refresh()
        return len(point_ids)

    async def _sync_metric_index(self, metric_info: MetricInfo) -> int:
        """替换指标的全部语义索引"""
        await self._metric_repo.ensure_index()
        index_entries = self._get_index_entries(
            metric_info.name,
            metric_info.description,
            metric_info.alias,
        )
        texts = [text for text, _ in index_entries]
        text_types: list[SemanticTextType] = [
            text_type for _, text_type in index_entries
        ]
        embeddings = await self._embed_texts(texts)
        point_ids = self._get_point_ids("metric", metric_info.name, texts)

        await self._metric_repo.delete(metric_info.name)
        await self._metric_repo.index(
            point_ids,
            texts,
            text_types,
            embeddings,
            metric_info,
        )
        await self._metric_repo.refresh()
        return len(point_ids)

    async def _sync_column_values(
        self,
        column_info: ColumnInfo,
    ) -> int:
        """替换字段的全部取值索引"""
        await self._value_repo.ensure_index()
        await self._value_repo.delete_by_column(
            column_info.t_name,
            column_info.name,
        )
        indexed_count = 0
        async for values in self._source_repo.iter_column_value_batches(
            column_info.t_name,
            column_info.name,
        ):
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
                await self._value_repo.index(value_infos)
                indexed_count += len(value_infos)
        if indexed_count:
            await self._value_repo.refresh()
        return indexed_count

    async def _clear_column_values(self, t_name: str, c_name: str) -> None:
        """清理未启用字段的取值索引"""
        await self._value_repo.ensure_index()
        await self._value_repo.delete_by_column(t_name, c_name)

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """分批生成文本向量"""
        embeddings: list[list[float]] = []
        for index in range(0, len(texts), self._embedding_batch_size):
            batch = texts[index : index + self._embedding_batch_size]
            embeddings.extend(await self._embedding_client.aembed_documents(batch))
        return embeddings

    @staticmethod
    def _get_index_entries(
        name: str,
        description: str,
        aliases: list[str],
    ) -> list[tuple[str, SemanticTextType]]:
        """获取带类型且按文本稳定去重的索引内容"""
        entries: list[tuple[str, SemanticTextType]] = [
            (name, "name"),
            (description, "description"),
            *((alias, "alias") for alias in aliases),
        ]
        unique_entries: dict[str, SemanticTextType] = {}
        for text, text_type in entries:
            if text:
                unique_entries.setdefault(text, text_type)
        return list(unique_entries.items())

    @staticmethod
    def _get_point_ids(resource: str, resource_key: str, texts: list[str]) -> list[str]:
        """生成稳定的向量点编号"""
        return [
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{resource}:{resource_key}:{index}:{text}",
                )
            )
            for index, text in enumerate(texts)
        ]

    @staticmethod
    def _serialize_value(value: Any) -> str:
        """将字段取值转换为索引文本"""
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value)
