"""字段和指标索引共享解析与损坏资源处理回归测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.semantic_index import CorruptedSemanticIndexDocumentError


@pytest.mark.parametrize("repo_class", [ColumnESRepo, MetricESRepo])
def test_search_preserves_payload_score_and_permission_filter(repo_class) -> None:
    payload = {"name": "amount", "description": "销售额", "alias": []}
    if repo_class is ColumnESRepo:
        payload["t_name"] = "orders"
    client = MagicMock(
        search=AsyncMock(
            return_value=SimpleNamespace(
                body={
                    "hits": {
                        "hits": [
                            {
                                "_id": "doc",
                                "_score": 2.5,
                                "_source": {"payload": payload},
                            }
                        ]
                    },
                }
            )
        )
    )
    repo = repo_class(client)
    if isinstance(repo, ColumnESRepo):
        hits = asyncio.run(
            repo.search_text_hits(
                "销售额",
                allowed_columns=frozenset({("orders", "amount")}),
            )
        )
    else:
        hits = asyncio.run(
            repo.search_text_hits(
                "销售额",
                allowed_metrics=frozenset({"amount"}),
            )
        )
    assert hits[0].item.name == "amount"
    assert hits[0].score == 2.5
    query = client.search.call_args.kwargs["query"]
    assert query["bool"]["filter"]


@pytest.mark.parametrize("repo_class", [ColumnESRepo, MetricESRepo])
@pytest.mark.parametrize("payload", [None, [], {"unknown_field": "invalid"}])
def test_search_reports_corrupted_payload_with_document_identity(
    repo_class, payload
) -> None:
    client = MagicMock(
        search=AsyncMock(
            return_value=SimpleNamespace(
                body={
                    "hits": {
                        "hits": [{"_id": "broken-doc", "_source": {"payload": payload}}]
                    },
                }
            )
        )
    )
    repo = repo_class(client)
    allowed = (
        {"allowed_columns": None}
        if repo_class is ColumnESRepo
        else {"allowed_metrics": None}
    )
    with pytest.raises(CorruptedSemanticIndexDocumentError) as caught:
        asyncio.run(repo.search_text_hits("x", **allowed))
    assert caught.value.document_id == "broken-doc"
    assert caught.value.index_name == repo._index_name


@pytest.mark.parametrize("repo_class", [ColumnESRepo, MetricESRepo])
def test_corrupted_resource_is_removed_before_rebuild(repo_class) -> None:
    client = MagicMock(
        search=AsyncMock(
            return_value=SimpleNamespace(body={"hits": {"hits": [{"_id": "broken"}]}})
        ),
        delete_by_query=AsyncMock(),
    )
    client.indices.exists = AsyncMock(return_value=True)
    repo = repo_class(client)
    assert asyncio.run(repo.list_resource_documents("resource")) == []
    client.delete_by_query.assert_awaited_once()
    assert client.delete_by_query.call_args.kwargs["query"] == {
        "bool": {"filter": [{"term": {"resource_key": "resource"}}]},
    }
