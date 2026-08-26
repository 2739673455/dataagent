"""Elasticsearch 组合字段资源键授权过滤测试"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.metadata.models.catalog import ColumnInfo, ValueInfo, column_resource_key
from app.metadata.models.search import SemanticIndexDelta, SemanticIndexDocument
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.value_index import (
    ValueESRepo,
    value_document_id,
)


def build_column() -> ColumnInfo:
    """构造字段索引测试实体"""
    return ColumnInfo(
        t_name="orders.eu",
        name="amount.total",
        type="DECIMAL",
        description="订单金额",
        examples=[10],
        alias=["金额"],
        index_values=True,
        reference_t_name=None,
        reference_c_name=None,
        meta_version=1,
        index_version=0,
    )


class ElasticsearchResourceKeyFilterTest(unittest.IsolatedAsyncioTestCase):
    """验证索引资源键写入、映射升级和大白名单查询"""

    def setUp(self) -> None:
        self.client = MagicMock()
        self.client.bulk = AsyncMock(return_value={"errors": False})
        self.client.delete_by_query = AsyncMock(
            return_value={"deleted": 0, "failures": []}
        )
        self.client.search = AsyncMock(return_value={"hits": {"hits": []}})
        self.client.indices = MagicMock()
        self.client.indices.exists = AsyncMock(return_value=True)
        self.client.indices.put_mapping = AsyncMock()
        self.client.indices.create = AsyncMock()
        self.client.indices.refresh = AsyncMock()

    async def test_existing_indexes_receive_resource_key_keyword_mapping(self) -> None:
        await ColumnESRepo(self.client).ensure_index()
        await ValueESRepo(self.client).ensure_index()

        self.assertEqual(self.client.indices.put_mapping.await_count, 2)
        column_properties = self.client.indices.put_mapping.await_args_list[0].kwargs[
            "properties"
        ]
        value_properties = self.client.indices.put_mapping.await_args_list[1].kwargs[
            "properties"
        ]
        self.assertEqual(column_properties["resource_key"], {"type": "keyword"})
        self.assertEqual(
            column_properties["embedding_revision"],
            {"type": "keyword"},
        )
        self.assertEqual(value_properties["resource_key"], {"type": "keyword"})
        self.assertEqual(
            value_properties["sync_generation"],
            {"type": "keyword"},
        )
        self.client.indices.create.assert_not_awaited()

    async def test_column_and_value_documents_write_same_composite_key(self) -> None:
        column = build_column()
        expected_key = column_resource_key(column.t_name, column.name)

        await ColumnESRepo(self.client).apply_delta(
            SemanticIndexDelta(
                create=[
                    SemanticIndexDocument(
                        id="column-document",
                        resource_key=expected_key,
                        text="订单金额",
                        text_type="name",
                        embedding=[0.1, 0.2],
                        embedding_revision="test:model:2:v1",
                        meta_version=1,
                        payload_hash="payload-hash",
                        payload={"t_name": column.t_name, "name": column.name},
                    )
                ],
                update=[],
                delete_ids=[],
                unchanged_count=0,
            )
        )
        column_operations = self.client.bulk.await_args.kwargs["operations"]
        self.assertEqual(column_operations[1]["resource_key"], expected_key)

        self.client.bulk.reset_mock()
        await ValueESRepo(self.client).upsert(
            [ValueInfo(value="10", t_name=column.t_name, c_name=column.name)],
            "generation-1",
        )
        value_operations = self.client.bulk.await_args.kwargs["operations"]
        self.assertEqual(value_operations[1]["resource_key"], expected_key)

    async def test_payload_only_update_does_not_overwrite_embedding(self) -> None:
        await ColumnESRepo(self.client).apply_delta(
            SemanticIndexDelta(
                create=[],
                update=[
                    SemanticIndexDocument(
                        id="column-document",
                        resource_key='["orders","amount"]',
                        text="订单金额",
                        text_type="description",
                        embedding=None,
                        embedding_revision="test:model:2:v1",
                        meta_version=2,
                        payload_hash="new-payload-hash",
                        payload={"type": "DECIMAL"},
                    )
                ],
                delete_ids=[],
                unchanged_count=0,
            )
        )

        operations = self.client.bulk.await_args.kwargs["operations"]
        self.assertIn("update", operations[0])
        self.assertNotIn("embedding", operations[1]["doc"])
        self.assertEqual(operations[1]["doc"]["payload"], {"type": "DECIMAL"})

    async def test_reconcile_deletes_only_other_value_generations(self) -> None:
        self.client.delete_by_query.return_value = {
            "deleted": 4,
            "failures": [],
        }

        deleted_count = await ValueESRepo(self.client).delete_other_generations(
            "orders",
            "status",
            "generation-2",
        )

        query = self.client.delete_by_query.await_args.kwargs["query"]
        self.assertEqual(
            query["bool"]["must_not"],
            [{"term": {"sync_generation": "generation-2"}}],
        )
        self.assertEqual(deleted_count, 4)

    async def test_large_whitelist_uses_one_terms_filter_without_bool_clauses(
        self,
    ) -> None:
        allowed = frozenset(
            (f"table_{index // 100}", f"column_{index}") for index in range(10_000)
        )

        await ColumnESRepo(self.client).search_text_hits(
            "收入",
            allowed_columns=allowed,
        )
        column_query = self.client.search.await_args.kwargs["query"]
        column_filter = column_query["bool"]["filter"]
        self.assertEqual(len(column_filter), 1)
        self.assertEqual(len(column_filter[0]["terms"]["resource_key"]), 10_000)
        self.assertNotIn("should", column_filter[0])

        self.client.search.reset_mock()
        await ValueESRepo(self.client).search_hits(
            "已支付",
            allowed_columns=allowed,
        )
        value_query = self.client.search.await_args.kwargs["query"]
        value_filter = value_query["bool"]["filter"]
        self.assertEqual(len(value_filter), 1)
        self.assertEqual(len(value_filter[0]["terms"]["resource_key"]), 10_000)
        self.assertNotIn("should", value_filter[0])

    async def test_resync_deletes_keyed_and_legacy_documents(self) -> None:
        column = build_column()

        await ColumnESRepo(self.client).delete(column.t_name, column.name)
        column_query = self.client.delete_by_query.await_args.kwargs["query"]
        column_should = column_query["bool"]["filter"][0]["bool"]["should"]
        self.assertEqual(
            column_should[0]["term"]["resource_key"],
            column_resource_key(column.t_name, column.name),
        )
        self.assertEqual(
            column_should[1]["bool"]["filter"],
            [
                {"term": {"t_name": column.t_name}},
                {"term": {"name": column.name}},
            ],
        )

        self.client.delete_by_query.reset_mock()
        await ValueESRepo(self.client).delete_by_column(column.t_name, column.name)
        value_query = self.client.delete_by_query.await_args.kwargs["query"]
        value_should = value_query["bool"]["should"]
        self.assertEqual(
            value_should[0]["term"]["resource_key"],
            column_resource_key(column.t_name, column.name),
        )
        self.assertEqual(
            value_should[1]["bool"]["filter"],
            [
                {"term": {"t_name": column.t_name}},
                {"term": {"c_name": column.name}},
            ],
        )

    def test_resource_key_does_not_collide_when_names_contain_dots(self) -> None:
        self.assertNotEqual(
            column_resource_key("sales.eu", "orders"),
            column_resource_key("sales", "eu.orders"),
        )

    def test_value_document_id_is_stable_and_unambiguous(self) -> None:
        first = ValueInfo(value="c", t_name="a", c_name="b")
        same = ValueInfo(value="c", t_name="a", c_name="b")
        different_boundary = ValueInfo(value="bc", t_name="a", c_name="")

        self.assertEqual(value_document_id(first), value_document_id(same))
        self.assertNotEqual(
            value_document_id(first),
            value_document_id(different_boundary),
        )
