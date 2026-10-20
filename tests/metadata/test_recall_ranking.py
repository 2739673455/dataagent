"""召回排序和字段上下文回归。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.metadata.models.catalog import ColumnInfo, MetricInfo, TableInfo, ValueInfo
from app.metadata.models.search import SearchHit, SemanticResourceRecallRequest
from app.metadata.services.search import (
    ColumnContextBuilder,
    RankedCandidates,
    RecallContext,
    SemanticCatalog,
    SemanticResourceRecallService,
)


def test_fused_scores_keep_order_normalization_and_truncation():
    scores = {}
    for key, rank in [("a", 1), ("b", 2), ("b", 1), ("c", 3)]:
        SemanticResourceRecallService._add_candidate_score(scores, key, rank)
    ranked, truncated = SemanticResourceRecallService._rank_candidates(scores, 2)
    assert ranked == [("b", 1.0), ("a", round((1 / 61) / (1 / 62 + 1 / 61), 6))]
    assert truncated


def test_direct_and_value_owner_columns_preserve_context():
    columns = {
        ("orders", name): ColumnInfo(
            t_name="orders",
            name=name,
            type="VARCHAR",
            description=name,
            alias=[],
            examples=[],
            index_values=True,
        )
        for name in ("direct", "owner")
    }
    warnings = []
    results, _, _ = ColumnContextBuilder(
        SemanticCatalog(tables={}, columns=columns, metrics={}), warnings
    ).build(
        RankedCandidates(
            columns=[(("orders", "direct"), 1.0)],
            metrics=[],
            values=[(("orders", "owner", "paid"), 1.0)],
            truncated=False,
        )
    )
    assert warnings == []
    assert {item.name for item in results} == {"direct", "owner"}
    assert all("match_reasons" not in item.model_dump() for item in results)


@pytest.mark.parametrize("kind", ["column", "metric"])
@pytest.mark.parametrize("channel", ["fulltext", "vector"])
def test_merge_hits_preserves_original_rank_deduplication_and_failure_scope(
    kind, channel
):
    columns = {
        ("orders", name): ColumnInfo(
            t_name="orders",
            name=name,
            type="VARCHAR",
            description=name,
            alias=[],
            examples=[],
            index_values=False,
        )
        for name in ("a", "b")
    }
    metrics = {
        name: MetricInfo(name=name, description=name, alias=[]) for name in ("a", "b")
    }
    catalog = SemanticCatalog(tables={}, columns=columns, metrics=metrics)
    context = RecallContext(
        request=SemanticResourceRecallRequest(
            terms=["first", "failed", "third"], resource_types=[kind]
        ),
        catalog=catalog,
    )
    if kind == "column":
        a, b = columns.values()
        missing = ColumnInfo(t_name="orders", name="missing")
        scores = context.column_scores
        a_key, b_key = ("orders", "a"), ("orders", "b")
    else:
        a, b = metrics.values()
        missing = MetricInfo(name="missing", description="Missing", alias=[])
        scores = context.metric_scores
        a_key, b_key = "a", "b"
    results = [
        [SearchHit(item=item, score=1.0) for item in (a, a, missing, b)],
        RuntimeError("backend unavailable"),
        [SearchHit(item=a, score=1.0)],
    ]
    repo = MagicMock(
        search_text_hits=AsyncMock(
            side_effect=results if channel == "fulltext" else None, return_value=[]
        ),
        search_vector_hits=AsyncMock(
            side_effect=results if channel == "vector" else None, return_value=[]
        ),
    )
    service = SemanticResourceRecallService(
        MagicMock(aembed_documents=AsyncMock(return_value=[[1.0], [2.0], [3.0]])),
        repo,
        repo,
        MagicMock(),
        catalog,
    )
    asyncio.run(service._retrieve(context))
    assert scores == {a_key: 2 / 61, b_key: 1 / 64}
    assert [failure.model_dump() for failure in context.failures] == [
        {"resource_type": kind, "channel": channel, "term": "failed"}
    ]


@pytest.mark.parametrize(
    "resource_types, empty_catalog, embedding_error",
    [
        (["column"], False, False),
        (["metric"], False, False),
        (["value"], False, False),
        (["column", "metric", "value"], False, False),
        (["column", "metric", "value"], True, False),
        (["column"], False, True),
        (["column", "metric", "value"], False, True),
    ],
)
def test_retrieval_routes_resources_and_shares_embeddings(
    resource_types, empty_catalog, embedding_error
):
    column = ColumnInfo(t_name="orders", name="status", index_values=True)
    metric = MetricInfo(name="count", description="Count", alias=[])
    catalog = SemanticCatalog(
        tables={},
        columns={} if empty_catalog else {("orders", "status"): column},
        metrics={} if empty_catalog else {"count": metric},
    )
    context = RecallContext(
        request=SemanticResourceRecallRequest(
            terms=["orders"], resource_types=resource_types
        ),
        catalog=catalog,
    )
    embedding = MagicMock(
        aembed_documents=AsyncMock(
            return_value=[[1.0]],
            side_effect=RuntimeError("embedding unavailable")
            if embedding_error
            else None,
        )
    )
    columns = MagicMock(
        search_text_hits=AsyncMock(return_value=[SearchHit(column, 1.0)]),
        search_vector_hits=AsyncMock(return_value=[SearchHit(column, 1.0)]),
    )
    metrics = MagicMock(
        search_text_hits=AsyncMock(return_value=[SearchHit(metric, 1.0)]),
        search_vector_hits=AsyncMock(return_value=[SearchHit(metric, 1.0)]),
    )
    values = MagicMock(search_hits=AsyncMock(return_value=[]))
    service = SemanticResourceRecallService(
        embedding, columns, metrics, values, catalog
    )
    asyncio.run(service._retrieve(context))
    semantic_types = (
        {"column", "metric"}.intersection(resource_types)
        if not empty_catalog
        else set()
    )
    assert embedding.aembed_documents.await_count == bool(semantic_types)
    for kind, repo, scores in (
        ("column", columns, context.column_scores),
        ("metric", metrics, context.metric_scores),
    ):
        enabled = kind in semantic_types
        assert repo.search_text_hits.await_count == enabled
        assert repo.search_vector_hits.await_count == (enabled and not embedding_error)
        assert list(scores.values()) == (
            [1 / 61 if embedding_error else 2 / 61] if enabled else []
        )
    assert values.search_hits.await_count == (
        "value" in resource_types and not empty_catalog
    )
    assert {failure.resource_type for failure in context.failures} == (
        semantic_types if embedding_error else set()
    )
    assert all(
        failure.channel == "vector" and failure.term is None
        for failure in context.failures
    )


def test_embedding_cancellation_propagates_without_starting_index_queries():
    context = RecallContext(
        request=SemanticResourceRecallRequest(
            terms=["orders"], resource_types=["column"]
        ),
        catalog=SemanticCatalog(
            tables={},
            columns={("orders", "status"): ColumnInfo(t_name="orders", name="status")},
            metrics={},
        ),
    )
    repo = MagicMock(search_text_hits=AsyncMock(), search_vector_hits=AsyncMock())
    service = SemanticResourceRecallService(
        MagicMock(aembed_documents=AsyncMock(side_effect=asyncio.CancelledError())),
        repo,
        MagicMock(),
        MagicMock(),
        context.catalog,
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service._retrieve(context))
    repo.search_text_hits.assert_not_awaited()
    repo.search_vector_hits.assert_not_awaited()
    assert context.failures == []


def test_recall_uses_full_catalog_and_preserves_references_metrics_and_values():
    tables = {
        name: TableInfo(
            name=name, role="fact", description=name, primary_key_columns=["id"]
        )
        for name in ("orders", "customers")
    }
    columns = {
        (table, name): ColumnInfo(
            t_name=table,
            name=name,
            type="VARCHAR",
            description=name,
            alias=[],
            examples=[],
            index_values=True,
        )
        for table, name in (
            ("orders", "id"),
            ("orders", "customer_id"),
            ("orders", "status"),
            ("customers", "id"),
        )
    }
    reference = columns[("orders", "customer_id")]
    reference.reference_t_name = "customers"
    reference.reference_c_name = "id"
    metric = MetricInfo(
        name="orders_count",
        description="订单数",
        alias=[],
        relevant_columns=[{"t_name": "orders", "c_name": "id"}],
    )
    column_repo = MagicMock(
        search_text_hits=AsyncMock(return_value=[SearchHit(reference, 1.0)]),
        search_vector_hits=AsyncMock(return_value=[]),
    )
    metric_repo = MagicMock(
        search_text_hits=AsyncMock(return_value=[SearchHit(metric, 1.0)]),
        search_vector_hits=AsyncMock(return_value=[]),
    )
    value_repo = MagicMock(
        search_hits=AsyncMock(
            return_value=[
                SearchHit(ValueInfo("paid", "orders", "status"), 1.0),
                SearchHit(ValueInfo("stale", "removed_table", "status"), 1.0),
            ]
        )
    )
    service = SemanticResourceRecallService(
        MagicMock(aembed_documents=AsyncMock(return_value=[[1.0]])),
        column_repo,
        metric_repo,
        value_repo,
        SemanticCatalog(tables=tables, columns=columns, metrics={metric.name: metric}),
    )
    response = asyncio.run(
        service.recall(
            SemanticResourceRecallRequest(
                terms=["订单"],
                resource_types=["column", "metric", "value"],
            )
        )
    )
    assert response.status == "success"
    assert {table.name for table in response.tables} == set(tables)
    assert all(table.primary_key_columns == ["id"] for table in response.tables)
    assert {(column.t_name, column.name) for column in response.columns} == set(columns)
    recalled_reference = next(c for c in response.columns if c.name == "customer_id")
    assert (
        recalled_reference.reference_t_name,
        recalled_reference.reference_c_name,
    ) == ("customers", "id")
    assert response.metrics[0].relevant_columns == metric.relevant_columns
    assert [value.value for value in response.values] == ["paid"]
    assert column_repo.search_text_hits.call_args.kwargs["catalog_keys"] == frozenset(
        columns
    )
    assert value_repo.search_hits.call_args.kwargs["catalog_columns"] == frozenset(
        columns
    )
