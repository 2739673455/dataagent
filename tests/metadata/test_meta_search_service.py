"""元数据语义检索服务测试"""

import asyncio
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models.catalog import (
    ColumnInfo,
    MetricInfo,
    TableInfo,
    ValueIndexSyncState,
    ValueInfo,
)
from app.metadata.models.search import SearchHit, SemanticSearchRequest
from app.metadata.services.search import MetaSearchService


def build_table(
    name: str = "orders",
    primary_key_columns: list[str] | None = None,
) -> TableInfo:
    """构造测试表元数据"""
    return TableInfo(
        name=name,
        role="fact",
        description=f"{name} table",
        primary_key_columns=primary_key_columns or [],
        meta_version=1,
    )


def build_column(
    t_name: str = "orders",
    name: str = "amount",
    *,
    index_values: bool = True,
    reference_t_name: str | None = None,
    reference_c_name: str | None = None,
) -> ColumnInfo:
    """构造测试字段元数据"""
    column_info = ColumnInfo(
        t_name=t_name,
        name=name,
        type="decimal",
        description=name,
        examples=[100],
        alias=["销售额"],
        index_values=index_values,
        reference_t_name=reference_t_name,
        reference_c_name=reference_c_name,
        meta_version=1,
        index_version=1,
    )
    now = datetime.now(UTC)
    column_info.value_index_state = ValueIndexSyncState(
        t_name=t_name,
        c_name=name,
        cursor_value=None,
        status="succeeded",
        active_run_id=None,
        current_generation=uuid4(),
        active_generation=None,
        last_incremental_synced_at=None,
        last_full_synced_at=now,
        last_error=None,
        updated_at=now,
    )
    return column_info


def build_metric() -> MetricInfo:
    """构造测试指标元数据"""
    return MetricInfo(
        name="revenue",
        description="收入",
        alias=["GMV"],
        relevant_columns=[],
        meta_version=1,
        index_version=1,
    )


class MetaSearchServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证阶段化检索编排和资源类型选择"""

    async def asyncSetUp(self) -> None:
        self.table = build_table()
        self.column = build_column()
        self.metric = build_metric()

        self.embedding_client = MagicMock()
        self.embedding_client.aembed_documents = AsyncMock()
        self.column_repo = MagicMock()
        self.column_repo.search_text_hits = AsyncMock()
        self.column_repo.search_vector_hits = AsyncMock()
        self.metric_repo = MagicMock()
        self.metric_repo.search_text_hits = AsyncMock()
        self.metric_repo.search_vector_hits = AsyncMock()
        self.value_repo = MagicMock()
        self.value_repo.search_hits = AsyncMock()
        self.meta_repo = MagicMock()
        self.meta_repo.list_table_infos = AsyncMock(return_value=[self.table])
        self.meta_repo.list_column_infos = AsyncMock(return_value=[self.column])
        self.meta_repo.list_metric_infos = AsyncMock(return_value=[self.metric])

        self.service = MetaSearchService(
            embedding_client=self.embedding_client,
            column_repo=self.column_repo,
            metric_repo=self.metric_repo,
            value_repo=self.value_repo,
            meta_repo=self.meta_repo,
            asset_policy=AssetAccessPolicy(user_id=1, unrestricted=True),
            data_source="doris",
            database_name="ecommerce",
        )

    def build_restricted_service(
        self,
        *grants: AssetIdentity,
    ) -> MetaSearchService:
        """构造字段白名单受限的检索服务"""
        return MetaSearchService(
            embedding_client=self.embedding_client,
            column_repo=self.column_repo,
            metric_repo=self.metric_repo,
            value_repo=self.value_repo,
            meta_repo=self.meta_repo,
            asset_policy=AssetAccessPolicy(user_id=2, grants=frozenset(grants)),
            data_source="doris",
            database_name="ecommerce",
        )

    async def test_asset_policy_filters_catalog_before_index_retrieval(self) -> None:
        denied = build_column(name="secret")
        self.meta_repo.list_column_infos.return_value = [self.column, denied]
        self.embedding_client.aembed_documents.return_value = [[0.1]]
        self.column_repo.search_text_hits.return_value = [
            SearchHit(item=denied, score=1.0),
            SearchHit(item=self.column, score=0.8),
        ]
        self.column_repo.search_vector_hits.return_value = []
        service = self.build_restricted_service(
            AssetIdentity(
                data_source="doris",
                database_name="ecommerce",
                table_name="orders",
                column_name="amount",
            )
        )

        response = await service.search(
            SemanticSearchRequest(query="金额", resource_types=["column"])
        )

        self.assertEqual([item.name for item in response.columns], ["amount"])
        expected_filter = frozenset({("orders", "amount")})
        self.column_repo.search_text_hits.assert_awaited_once_with(
            "金额",
            allowed_columns=expected_filter,
            limit=15,
        )
        self.column_repo.search_vector_hits.assert_awaited_once_with(
            [0.1],
            allowed_columns=expected_filter,
            limit=15,
        )

    async def test_empty_asset_policy_skips_all_backends(self) -> None:
        service = self.build_restricted_service()

        response = await service.search(
            SemanticSearchRequest(
                query="收入",
                resource_types=["column", "metric", "value"],
            )
        )

        self.assertEqual(response.columns, [])
        self.assertEqual(response.metrics, [])
        self.assertEqual(response.values, [])
        self.embedding_client.aembed_documents.assert_not_awaited()
        self.column_repo.search_text_hits.assert_not_awaited()
        self.metric_repo.search_text_hits.assert_not_awaited()
        self.value_repo.search_hits.assert_not_awaited()

    async def test_column_request_uses_text_and_vector_without_other_resources(
        self,
    ) -> None:
        column_hit = SearchHit(item=self.column, score=0.9)
        self.embedding_client.aembed_documents.return_value = [[0.1], [0.2]]
        self.column_repo.search_text_hits.side_effect = [
            [column_hit],
            [column_hit],
        ]
        self.column_repo.search_vector_hits.side_effect = [
            [column_hit],
            [column_hit],
        ]

        response = await self.service.search(
            SemanticSearchRequest(
                query="销售额",
                terms=["GMV"],
                resource_types=["column"],
            )
        )

        self.assertEqual(response.queries, ["销售额", "GMV"])
        self.assertEqual([item.name for item in response.columns], ["amount"])
        self.assertEqual(
            [reason.model_dump() for reason in response.columns[0].match_reasons],
            [
                {"match_type": "fulltext", "query": "销售额", "score": 0.9},
                {"match_type": "fulltext", "query": "GMV", "score": 0.9},
                {"match_type": "vector", "query": "销售额", "score": 0.9},
                {"match_type": "vector", "query": "GMV", "score": 0.9},
            ],
        )
        self.assertEqual(response.metrics, [])
        self.assertEqual(response.values, [])
        self.embedding_client.aembed_documents.assert_awaited_once_with(
            ["销售额", "GMV"]
        )
        self.assertEqual(self.column_repo.search_text_hits.await_count, 2)
        self.assertEqual(self.column_repo.search_vector_hits.await_count, 2)
        self.metric_repo.search_text_hits.assert_not_awaited()
        self.metric_repo.search_vector_hits.assert_not_awaited()
        self.value_repo.search_hits.assert_not_awaited()

    async def test_metric_request_does_not_search_columns_or_values(self) -> None:
        metric_hit = SearchHit(item=self.metric, score=0.8)
        self.embedding_client.aembed_documents.return_value = [[0.1]]
        self.metric_repo.search_text_hits.return_value = [metric_hit]
        self.metric_repo.search_vector_hits.return_value = [metric_hit]

        response = await self.service.search(
            SemanticSearchRequest(query="收入", resource_types=["metric"])
        )

        self.assertEqual([item.name for item in response.metrics], ["revenue"])
        self.assertEqual(response.columns, [])
        self.column_repo.search_text_hits.assert_not_awaited()
        self.column_repo.search_vector_hits.assert_not_awaited()
        self.value_repo.search_hits.assert_not_awaited()

    async def test_value_request_skips_embedding_and_semantic_indexes(self) -> None:
        self.value_repo.search_hits.return_value = [
            SearchHit(
                item=ValueInfo(value="100", t_name="orders", c_name="amount"),
                score=0.7,
            )
        ]

        response = await self.service.search(
            SemanticSearchRequest(query="100", resource_types=["value"])
        )

        self.assertEqual([item.value for item in response.values], ["100"])
        self.assertEqual([item.name for item in response.columns], ["amount"])
        self.embedding_client.aembed_documents.assert_not_awaited()
        self.column_repo.search_text_hits.assert_not_awaited()
        self.metric_repo.search_text_hits.assert_not_awaited()

    async def test_backend_failure_keeps_other_retrieval_and_marks_partial(
        self,
    ) -> None:
        column_hit = SearchHit(item=self.column, score=0.9)
        self.embedding_client.aembed_documents.return_value = [[0.1]]
        self.column_repo.search_text_hits.side_effect = RuntimeError("text down")
        self.column_repo.search_vector_hits.return_value = [column_hit]

        response = await self.service.search(
            SemanticSearchRequest(query="销售额", resource_types=["column"])
        )

        self.assertEqual(response.status, "partial")
        self.assertEqual([item.name for item in response.columns], ["amount"])
        self.assertEqual(
            response.warnings,
            ["字段全文 检索服务暂不可用"],
        )

    async def test_column_context_adds_primary_key_and_one_hop_relation(self) -> None:
        order_id = build_column(name="id", index_values=False)
        customer_id = build_column(
            name="customer_id",
            index_values=False,
            reference_t_name="customers",
            reference_c_name="id",
        )
        target_id = build_column(
            t_name="customers",
            name="id",
            index_values=False,
        )
        target_tenant_id = build_column(
            t_name="customers",
            name="tenant_id",
            index_values=False,
        )
        self.meta_repo.list_table_infos.return_value = [
            build_table(primary_key_columns=["id"]),
            build_table("customers", ["id", "tenant_id"]),
        ]
        self.meta_repo.list_column_infos.return_value = [
            self.column,
            order_id,
            customer_id,
            target_id,
            target_tenant_id,
        ]
        direct_hit = SearchHit(item=self.column, score=0.9)
        self.embedding_client.aembed_documents.return_value = [[0.1]]
        self.column_repo.search_text_hits.return_value = [direct_hit]
        self.column_repo.search_vector_hits.return_value = [direct_hit]

        response = await self.service.search(
            SemanticSearchRequest(query="销售额", resource_types=["column"])
        )

        self.assertEqual(
            {(item.t_name, item.name) for item in response.columns},
            {
                ("orders", "amount"),
                ("orders", "id"),
                ("orders", "customer_id"),
                ("customers", "id"),
                ("customers", "tenant_id"),
            },
        )
        self.assertEqual(len(response.relations), 1)
        self.assertEqual(response.relations[0].source_c_name, "customer_id")
        self.assertEqual(response.relations[0].target_t_name, "customers")

    async def test_match_reason_preserves_query_containing_colon(self) -> None:
        column_hit = SearchHit(item=self.column, score=0.75)
        self.embedding_client.aembed_documents.return_value = [[0.1]]
        self.column_repo.search_text_hits.return_value = [column_hit]
        self.column_repo.search_vector_hits.return_value = []

        response = await self.service.search(
            SemanticSearchRequest(
                query="销售额:含税",
                resource_types=["column"],
            )
        )

        reason = response.columns[0].match_reasons[0]
        self.assertEqual(reason.match_type, "fulltext")
        self.assertEqual(reason.query, "销售额:含税")
        self.assertEqual(reason.score, 0.75)

    async def test_index_queries_obey_service_concurrency_limit(self) -> None:
        active = 0
        max_active = 0

        async def search_value(*_args: object, **_kwargs: object) -> list[object]:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return []

        self.value_repo.search_hits.side_effect = search_value
        self.service = MetaSearchService(
            embedding_client=self.embedding_client,
            column_repo=self.column_repo,
            metric_repo=self.metric_repo,
            value_repo=self.value_repo,
            meta_repo=self.meta_repo,
            asset_policy=AssetAccessPolicy(user_id=1, unrestricted=True),
            data_source="doris",
            database_name="ecommerce",
            max_concurrent_index_queries=2,
        )

        await self.service.search(
            SemanticSearchRequest(
                query="q0",
                terms=[f"q{index}" for index in range(1, 10)],
                resource_types=["value"],
            )
        )

        self.assertEqual(max_active, 2)

    async def test_metadata_catalog_is_loaded_sequentially(self) -> None:
        calls: list[str] = []

        async def load_tables() -> list[object]:
            calls.append("tables")
            return [self.table]

        async def load_columns() -> list[object]:
            calls.append("columns")
            return [self.column]

        async def load_metrics() -> list[object]:
            calls.append("metrics")
            return [self.metric]

        self.meta_repo.list_table_infos.side_effect = load_tables
        self.meta_repo.list_column_infos.side_effect = load_columns
        self.meta_repo.list_metric_infos.side_effect = load_metrics
        self.value_repo.search_hits.return_value = []

        await self.service.search(
            SemanticSearchRequest(query="100", resource_types=["value"])
        )

        self.assertEqual(calls, ["tables", "columns", "metrics"])

    async def test_structural_columns_are_complete_after_ranked_context_limit(
        self,
    ) -> None:
        dependency_columns = [
            build_column(name=f"dimension_{index:02}", index_values=False)
            for index in range(35)
        ]
        order_id = build_column(name="id", index_values=False)
        customer_id = build_column(
            name="customer_id",
            index_values=False,
            reference_t_name="customers",
            reference_c_name="id",
        )
        target_id = build_column(
            t_name="customers",
            name="id",
            index_values=False,
        )
        metric = MetricInfo(
            name="wide_metric",
            description="宽指标",
            alias=[],
            relevant_columns=[
                {"t_name": column.t_name, "c_name": column.name}
                for column in dependency_columns
            ],
            meta_version=1,
            index_version=1,
        )
        self.meta_repo.list_table_infos.return_value = [
            build_table(primary_key_columns=["id"]),
            build_table("customers", ["id"]),
        ]
        self.meta_repo.list_column_infos.return_value = [
            *dependency_columns,
            order_id,
            customer_id,
            target_id,
        ]
        self.meta_repo.list_metric_infos.return_value = [metric]
        metric_hit = SearchHit(item=metric, score=0.9)
        self.embedding_client.aembed_documents.return_value = [[0.1]]
        self.metric_repo.search_text_hits.return_value = [metric_hit]
        self.metric_repo.search_vector_hits.return_value = []

        response = await self.service.search(
            SemanticSearchRequest(query="宽指标", resource_types=["metric"])
        )

        returned_columns = {(column.t_name, column.name) for column in response.columns}
        self.assertTrue(response.truncated)
        self.assertIn(("orders", "id"), returned_columns)
        self.assertIn(("orders", "customer_id"), returned_columns)
        self.assertIn(("customers", "id"), returned_columns)
        self.assertEqual(len(response.relations), 1)
        self.assertIn(
            "排序后的字段上下文已截断，最多保留 30 个资源",
            response.warnings,
        )


if __name__ == "__main__":
    unittest.main()
