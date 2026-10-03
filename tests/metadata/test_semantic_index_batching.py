"""跨资源向量批次、差量重试和版本提交回归。"""

import asyncio
import math
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.metadata.indexing import MetaIndexService
from app.metadata.models.catalog import ColumnInfo, MetricInfo, column_resource_key
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.shared.config.app_config import cfg


class _IndexScenario:
    """使用真实索引服务和 ES 仓库，模拟外部存储及事务提交。"""

    def __init__(self, kind, count=3):
        self.kind = kind
        self.items = {}
        for i in range(count):
            name = f"item_{i:03}"
            kwargs: dict[str, Any] = {
                "name": name,
                "description": f"说明 {i}",
                "alias": [f"  {name}  "],
                "meta_version": 1,
            }
            item = (
                ColumnInfo(t_name="orders", examples=[], **kwargs)
                if kind == "column"
                else MetricInfo(**kwargs)
            )
            self.items[("orders", name) if kind == "column" else name] = item
        self.documents = {}
        self.versions = {}
        self.pending = {}
        self.active_locks = []
        self.lock_batches = []
        self.transactions = []
        self.fail_bulk = False
        self.fail_embedding = False
        self.bump_version = False

        @asynccontextmanager
        async def transaction():
            assert not self.active_locks
            self.pending = {}
            try:
                yield
            except BaseException:
                self.transactions.append("rollback")
                raise
            else:
                self.versions.update(self.pending)
                self.transactions.append("commit")
            finally:
                self.lock_batches.append(list(self.active_locks))
                self.active_locks.clear()

        async def lock(resource_type, resource_key):
            assert resource_type == kind
            self.active_locks.append(resource_key)

        async def load(*args):
            key = args if kind == "column" else args[0]
            assert self.resource_key(key) in self.active_locks
            return self.items[key]

        async def mark(*args):
            key, version = (args[:2] if kind == "column" else args[0]), args[-1]
            assert self.resource_key(key) in self.active_locks
            if self.items[key].meta_version != version:
                return False
            self.pending[key] = version
            return True

        self.mark = AsyncMock(side_effect=mark)
        self.meta = MagicMock(
            session=MagicMock(begin=transaction),
            acquire_index_lock=AsyncMock(side_effect=lock),
            get_column_info=AsyncMock(side_effect=load),
            get_metric_info=AsyncMock(side_effect=load),
            mark_column_indexed_if_current=self.mark,
            mark_metric_indexed_if_current=self.mark,
        )

        async def search(**kwargs):
            key = kwargs["query"]["term"]["resource_key"]
            return SimpleNamespace(
                body={
                    "hits": {
                        "hits": [
                            {"_id": doc_id, "_source": source.copy()}
                            for doc_id, source in self.documents.items()
                            if source["resource_key"] == key
                        ]
                    }
                }
            )

        async def bulk(*, operations, refresh):
            assert refresh is False
            iterator = iter(operations)
            for action in iterator:
                operation, metadata = next(iter(action.items()))
                doc_id = metadata["_id"]
                if operation == "delete":
                    self.documents.pop(doc_id, None)
                else:
                    source = next(iterator)
                    if operation == "index":
                        self.documents[doc_id] = source.copy()
                    else:
                        self.documents[doc_id].update(source["doc"])
                if self.fail_bulk:
                    return SimpleNamespace(
                        body={
                            "errors": True,
                            "items": [{"index": {"error": "unavailable"}}],
                        }
                    )
            if self.bump_version:
                for item in self.items.values():
                    item.meta_version += 1
            return SimpleNamespace(body={"errors": False})

        self.es = MagicMock(
            search=AsyncMock(side_effect=search), bulk=AsyncMock(side_effect=bulk)
        )
        self.es.indices.exists = AsyncMock(return_value=True)
        self.es.indices.refresh = AsyncMock()

        async def embed(texts):
            assert self.active_locks
            assert 0 < len(texts) <= cfg.embedding.batch_size
            if self.fail_embedding:
                raise RuntimeError("embedding unavailable")
            return [self.vector(text) for text in texts]

        self.embedding = MagicMock(aembed_documents=AsyncMock(side_effect=embed))
        self.service = MetaIndexService(
            self.meta,
            MagicMock(),
            ColumnESRepo(self.es),
            MetricESRepo(self.es),
            self.embedding,
            MagicMock(),
        )

    def resource_key(self, key):
        return column_resource_key(*key) if self.kind == "column" else key

    @staticmethod
    def vector(text):
        return [float(sum(text.encode()))]

    async def sync(self, keys=None):
        selected = list(self.items) if keys is None else keys
        method = (
            self.service.sync_column_indexes
            if self.kind == "column"
            else self.service.sync_metric_indexes
        )
        return await method(selected)


@pytest.mark.parametrize("kind", ["column", "metric"])
def test_batching_preserves_documents_reuses_vectors_and_checks_versions(kind):
    case = _IndexScenario(kind)

    async def run():
        keys = list(reversed(case.items))
        results = await case.sync(keys + keys)
        assert list(results) == keys
        assert all(
            result.created_count == result.embedded_count == 2
            for result in results.values()
        )
        assert all(result.version_committed for result in results.values())
        assert (
            case.embedding.aembed_documents.await_count == 2
        )  # 六条文本合并为 4 + 2。
        case.es.bulk.assert_awaited_once()
        case.es.indices.refresh.assert_awaited_once()
        assert case.lock_batches == [sorted(case.resource_key(key) for key in keys)]
        assert len(case.documents) == 6
        assert all(
            doc["embedding"] == case.vector(doc["text"])
            for doc in case.documents.values()
        )
        ids = set(case.documents)
        case.embedding.aembed_documents.reset_mock()
        case.es.bulk.reset_mock()
        results = await case.sync()
        assert all(
            result.unchanged_count == 2 and result.embedded_count == 0
            for result in results.values()
        )
        case.embedding.aembed_documents.assert_not_awaited()
        case.es.bulk.assert_not_awaited()
        # 仅载荷版本改变时使用 partial update；规范化的别名仍与名称去重。
        for item in case.items.values():
            item.meta_version = 2
        results = await case.sync()
        assert all(
            result.updated_count == 2 and result.embedded_count == 0
            for result in results.values()
        )
        assert set(case.documents) == ids
        case.embedding.aembed_documents.assert_not_awaited()
        assert all(
            doc["embedding"] == case.vector(doc["text"])
            for doc in case.documents.values()
        )
        first = next(iter(case.items.values()))
        first.alias = [first.name, "新增别名"]
        first.description = first.name  # 名称优先，旧说明删除。
        first.meta_version = 3
        result = (await case.sync())[keys[-1]]
        assert (result.created_count, result.deleted_count, result.embedded_count) == (
            1,
            1,
            1,
        )
        assert (
            next(doc for doc in case.documents.values() if doc["text"] == first.name)[
                "text_type"
            ]
            == "name"
        )
        # 外部写入期间目录更新，旧版本不得确认。
        case.bump_version = True
        first.meta_version = 4
        results = await case.sync()
        assert not any(result.version_committed for result in results.values())

    with patch.object(cfg.embedding, "batch_size", 4):
        asyncio.run(run())


@pytest.mark.parametrize("kind", ["column", "metric"])
@pytest.mark.parametrize("stage", ["embedding", "bulk", "cancel"])
def test_failed_batch_keeps_versions_pending_and_retry_repairs_partial_writes(
    kind, stage
):
    case = _IndexScenario(kind)

    async def run():
        case.fail_embedding = stage == "embedding"
        case.fail_bulk = stage == "bulk"
        if stage == "cancel":
            case.embedding.aembed_documents.side_effect = asyncio.CancelledError()
        with pytest.raises(
            asyncio.CancelledError if stage == "cancel" else RuntimeError
        ):
            await case.sync()
        assert case.versions == {}
        case.mark.assert_not_awaited()
        assert case.transactions == ["rollback"]
        assert not case.active_locks
        if stage == "bulk":
            assert len(case.documents) == 1
            case.fail_bulk = False
            results = await case.sync()
            assert all(result.version_committed for result in results.values())
            assert len(case.documents) == 6

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["column", "metric"])
def test_large_import_is_bounded_and_commits_completed_batches(kind):
    case = _IndexScenario(kind, count=70)

    async def run():
        results = await case.sync()
        assert len(results) == 70
        assert len(case.documents) == 140
        assert len(case.lock_batches) == 5
        assert max(map(len, case.lock_batches)) == 16
        assert case.es.bulk.await_count == case.es.indices.refresh.await_count == 5
        assert case.embedding.aembed_documents.await_count == 4 * math.ceil(
            32 / 7
        ) + math.ceil(12 / 7)
        assert all(
            doc["embedding"] == case.vector(doc["text"])
            for doc in case.documents.values()
        )

    with (
        patch.object(cfg.embedding, "batch_size", 7),
        patch.object(cfg.metadata_index, "semantic_resource_batch_size", 16),
    ):
        asyncio.run(run())


def test_later_batch_failure_keeps_earlier_commits():
    case = _IndexScenario("metric", count=4)
    original = case.embedding.aembed_documents.side_effect
    calls = 0

    async def embed(texts):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second batch")
        return await original(texts)

    case.embedding.aembed_documents.side_effect = embed
    with (
        patch.object(cfg.metadata_index, "semantic_resource_batch_size", 2),
        pytest.raises(RuntimeError, match="second batch"),
    ):
        asyncio.run(case.sync())
    assert case.transactions == ["commit", "rollback"]
    assert set(case.versions) == set(list(case.items)[:2])
