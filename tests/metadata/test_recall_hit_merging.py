"""字段和指标双通道命中融合的排名、依据和失败边界。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.metadata.models.catalog import ColumnInfo, MetricInfo
from app.metadata.models.search import (
    SemanticRecallFailure,
    SemanticResourceRecallRequest,
)
from app.metadata.services.search import (
    SemanticCatalog,
    SemanticResourceRecallService,
    _RecallContext,
)
from app.shared.contracts.search import SearchHit


@pytest.mark.parametrize("resource_type", ["column", "metric"])
@pytest.mark.parametrize("channel", ["fulltext", "vector"])
@pytest.mark.parametrize("second_result", ["success", "failure", "cancel"])
def test_collect_preserves_ranking_reasons_and_failure_scope(
    resource_type, channel, second_result
):
    def item(name):
        if resource_type == "column":
            return ColumnInfo(t_name="orders", name=name)
        return MetricInfo(name=name, description=name, alias=[])

    context = _RecallContext(
        request=SemanticResourceRecallRequest(
            terms=["收入", "金额", "销售"], resource_types=[resource_type]
        ),
        catalog=SemanticCatalog(
            tables={},
            columns={
                ("orders", name): ColumnInfo(t_name="orders", name=name)
                for name in ("amount", "count")
            }
            if resource_type == "column"
            else {},
            metrics={
                name: MetricInfo(name=name, description=name, alias=[])
                for name in ("amount", "count")
            }
            if resource_type == "metric"
            else {},
        ),
    )
    responses: list[list[SearchHit[ColumnInfo | MetricInfo]] | BaseException] = [
        [
            SearchHit(item=item("outside"), score=100.0),
            SearchHit(item=item("amount"), score=8.0),
            SearchHit(item=item("amount"), score=7.0),
            SearchHit(item=item("count"), score=6.0),
        ],
        [SearchHit(item=item("amount"), score=5.0)],
        [SearchHit(item=item("amount"), score=4.0)],
    ]
    if second_result == "failure":
        responses[1] = RuntimeError("backend unavailable")
    elif second_result == "cancel":
        responses[1] = asyncio.CancelledError()
    column_repo = MagicMock(
        search_text_hits=AsyncMock(return_value=[]),
        search_vector_hits=AsyncMock(return_value=[]),
    )
    metric_repo = MagicMock(
        search_text_hits=AsyncMock(return_value=[]),
        search_vector_hits=AsyncMock(return_value=[]),
    )
    value_repo = MagicMock()
    repo = column_repo if resource_type == "column" else metric_repo
    search = AsyncMock(side_effect=responses)
    setattr(
        repo,
        "search_text_hits" if channel == "fulltext" else "search_vector_hits",
        search,
    )
    service = SemanticResourceRecallService(
        MagicMock(aembed_documents=AsyncMock(return_value=[[0.1], [0.2], [0.3]])),
        column_repo,
        metric_repo,
        value_repo,
        context.catalog,
        MagicMock(),
        "source",
        "db",
    )
    if second_result == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(service._retrieve(context))
        assert context.failures == []
        return
    asyncio.run(service._retrieve(context))
    scores = (
        {name: score for (_, name), score in context.column_scores.items()}
        if resource_type == "column"
        else context.metric_scores
    )
    assert set(scores) == {"amount", "count"}
    # 目录外及重复命中占据的原始排名不能被压缩，去重范围仅限当前检索词。
    assert scores["amount"].score == pytest.approx(
        1 / 62 + (2 if second_result == "success" else 1) / 61
    )
    assert scores["count"].score == pytest.approx(1 / 64)
    expected_reasons = [("收入", 8.0), ("金额", 5.0), ("销售", 4.0)]
    if second_result == "failure":
        expected_reasons.pop(1)
    assert [
        (reason.term, reason.score) for reason in scores["amount"].reasons
    ] == expected_reasons
    assert all(reason.match_type == channel for reason in scores["amount"].reasons)
    assert context.failures == (
        [
            SemanticRecallFailure(
                resource_type=resource_type, channel=channel, term="金额"
            )
        ]
        if second_result == "failure"
        else []
    )
    assert search.await_count == 3
    other_repo = metric_repo if resource_type == "column" else column_repo
    assert other_repo.mock_calls == []
    assert value_repo.mock_calls == []


@pytest.mark.parametrize(
    "resources,has_columns,has_metrics",
    [
        (["column", "metric", "value"], True, True),
        (["column"], True, True),
        (["metric"], True, True),
        (["value"], True, True),
        (["column", "metric", "value"], False, False),
        (["column", "metric", "value"], False, True),
        (["column", "metric", "value"], True, False),
    ],
)
@pytest.mark.parametrize("embedding_fails", [False, True])
def test_resource_routing_shared_embeddings_and_fulltext_fallback(
    resources, has_columns, has_metrics, embedding_fails
):
    events = []
    column = ColumnInfo(t_name="orders", name="amount", index_values=True)
    metric = MetricInfo(name="revenue", description="收入", alias=[])
    context = _RecallContext(
        request=SemanticResourceRecallRequest(terms=["收入"], resource_types=resources),
        catalog=SemanticCatalog(
            tables={},
            columns={("orders", "amount"): column} if has_columns else {},
            metrics={"revenue": metric} if has_metrics else {},
        ),
    )
    embeddings = [[0.1, 0.2]]

    async def embed(terms):
        events.append("embedding")
        if embedding_fails:
            raise RuntimeError("embedding unavailable")
        return embeddings

    def search(name, hits):
        async def run(*args, **kwargs):
            events.append(name)
            return hits

        return AsyncMock(side_effect=run)

    embedding = MagicMock(aembed_documents=AsyncMock(side_effect=embed))
    column_repo = MagicMock(
        search_text_hits=search("column/fulltext", [SearchHit(item=column, score=8.0)]),
        search_vector_hits=search("column/vector", [SearchHit(item=column, score=0.9)]),
    )
    metric_repo = MagicMock(
        search_text_hits=search("metric/fulltext", [SearchHit(item=metric, score=7.0)]),
        search_vector_hits=search("metric/vector", [SearchHit(item=metric, score=0.8)]),
    )
    value_repo = MagicMock(search_hits=search("value/fulltext", []))
    service = SemanticResourceRecallService(
        embedding,
        column_repo,
        metric_repo,
        value_repo,
        context.catalog,
        MagicMock(),
        "source",
        "db",
    )
    asyncio.run(service._retrieve(context))
    selected = [
        resource
        for resource, available in (("column", has_columns), ("metric", has_metrics))
        if resource in resources and available
    ]
    expected = ["embedding"] if selected else []
    for resource in selected:
        expected.append(f"{resource}/fulltext")
        if not embedding_fails:
            expected.append(f"{resource}/vector")
    if "value" in resources and has_columns:
        expected.append("value/fulltext")
    assert events == expected
    if selected:
        embedding.aembed_documents.assert_awaited_once_with(["收入"])
    else:
        embedding.aembed_documents.assert_not_awaited()
    for resource, repo, scores in (
        ("column", column_repo, list(context.column_scores.values())),
        ("metric", metric_repo, list(context.metric_scores.values())),
    ):
        if resource not in selected:
            assert scores == []
            continue
        candidate = scores[0]
        assert candidate.score == pytest.approx((1 if embedding_fails else 2) / 61)
        assert [reason.match_type for reason in candidate.reasons] == (
            ["fulltext"] if embedding_fails else ["fulltext", "vector"]
        )
        if not embedding_fails:
            assert repo.search_vector_hits.call_args.args[0] is embeddings[0]
            permission = "allowed_keys"
            assert (
                repo.search_text_hits.call_args.kwargs[permission]
                is repo.search_vector_hits.call_args.kwargs[permission]
            )
    assert [
        (failure.resource_type, failure.channel, failure.term)
        for failure in context.failures
    ] == (
        [(resource, "vector", None) for resource in selected] if embedding_fails else []
    )
