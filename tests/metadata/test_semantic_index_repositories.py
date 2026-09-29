"""字段和指标索引共享解析与损坏资源处理回归测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.metadata.errors import CorruptedSemanticIndexDocumentError
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo


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
                allowed_keys=frozenset({("orders", "amount")}),
            )
        )
    else:
        hits = asyncio.run(
            repo.search_text_hits(
                "销售额",
                allowed_keys=frozenset({"amount"}),
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
    allowed = {"allowed_keys": None}
    with pytest.raises(CorruptedSemanticIndexDocumentError) as caught:
        asyncio.run(repo.search_text_hits("x", **allowed))
    assert caught.value.document_id == "broken-doc"
    assert caught.value.index_name == repo._index_name


@pytest.mark.parametrize("repo_class", [ColumnESRepo, MetricESRepo])
def test_full_documents_are_written_with_vectors(repo_class):
    from app.metadata.models.search import SemanticIndexDocument

    client = MagicMock(
        bulk=AsyncMock(return_value=SimpleNamespace(body={"errors": False}))
    )
    client.indices.refresh = AsyncMock()
    document = SemanticIndexDocument(
        "doc", "resource", "金额", "name", [0.1, 0.2], {"name": "amount"}
    )
    asyncio.run(repo_class(client).write_documents([document]))
    operations = client.bulk.call_args.kwargs["operations"]
    assert operations[0] == {"index": {"_index": repo_class._index_name, "_id": "doc"}}
    assert operations[1] == {
        "resource_key": "resource",
        "text": "金额",
        "text_type": "name",
        "embedding": [0.1, 0.2],
        "payload": {"name": "amount"},
    }
    client.search.assert_not_called()
    client.indices.refresh.assert_awaited_once()


def test_bulk_item_failure_is_not_reported_as_success():
    from app.metadata.models.search import SemanticIndexDocument

    client = MagicMock(
        bulk=AsyncMock(
            return_value=SimpleNamespace(
                body={
                    "errors": True,
                    "items": [{"index": {"error": "invalid vector"}}],
                }
            )
        )
    )
    client.indices.refresh = AsyncMock()
    with pytest.raises(RuntimeError, match="invalid vector"):
        asyncio.run(
            ColumnESRepo(client).write_documents(
                [
                    SemanticIndexDocument("doc", "resource", "金额", "name", [0.1], {}),
                ]
            )
        )
    client.indices.refresh.assert_not_awaited()


@pytest.mark.parametrize("channel", ["text", "vector"])
@pytest.mark.parametrize("allowed", [frozenset(), None, frozenset({"amount"})])
def test_metric_allowlist_distinguishes_empty_and_unrestricted(channel, allowed):
    client = MagicMock(
        search=AsyncMock(return_value=SimpleNamespace(body={"hits": {"hits": []}}))
    )
    repo = MetricESRepo(client)
    operation = (
        repo.search_text_hits("金额", allowed_keys=allowed)
        if channel == "text"
        else repo.search_vector_hits([0.1], allowed_keys=allowed)
    )
    assert asyncio.run(operation) == []
    if allowed == frozenset():
        client.search.assert_not_awaited()
        return
    client.search.assert_awaited_once()
    kwargs = client.search.call_args.kwargs
    if allowed is None:
        if channel == "vector":
            assert "filter" not in kwargs["knn"]
        else:
            assert "bool" not in kwargs["query"]
    else:
        expected = {"terms": {"resource_key": ["amount"]}}
        if channel == "vector":
            assert kwargs["knn"]["filter"] == expected
        else:
            assert kwargs["query"]["bool"]["filter"] == [expected]


@pytest.mark.parametrize("channel", ["text", "vector", "value"])
@pytest.mark.parametrize(
    "allowed", [frozenset(), None, frozenset({("orders", "amount")})]
)
def test_column_allowlist_distinguishes_empty_and_unrestricted(channel, allowed):
    from app.metadata.models.catalog import column_resource_key
    from app.metadata.repositories.value_index import ValueESRepo

    client = MagicMock(
        search=AsyncMock(return_value=SimpleNamespace(body={"hits": {"hits": []}}))
    )
    if channel == "value":
        operation = ValueESRepo(client).search_hits("金额", allowed_columns=allowed)
    elif channel == "vector":
        operation = ColumnESRepo(client).search_vector_hits([0.1], allowed_keys=allowed)
    else:
        operation = ColumnESRepo(client).search_text_hits("金额", allowed_keys=allowed)
    assert asyncio.run(operation) == []
    if allowed == frozenset():
        client.search.assert_not_awaited()
        return
    client.search.assert_awaited_once()
    kwargs = client.search.call_args.kwargs
    if allowed is None:
        if channel == "vector":
            assert "filter" not in kwargs["knn"]
        else:
            assert "bool" not in kwargs["query"]
    else:
        expected = {
            "terms": {"resource_key": [column_resource_key("orders", "amount")]}
        }
        if channel == "vector":
            assert kwargs["knn"]["filter"] == expected
        else:
            assert kwargs["query"]["bool"]["filter"] == [expected]


@pytest.mark.parametrize(
    "hit, document_id, cause",
    [
        (None, "<missing>", TypeError),
        ({}, "<missing>", KeyError),
        ({"_id": 0, "_source": []}, "0", TypeError),
        ({"_id": "broken", "_source": None}, "broken", TypeError),
        ({"_id": "broken", "_source": {}}, "broken", KeyError),
        (
            {"_id": "broken", "_source": {"resource_key": "key", "payload": []}},
            "broken",
            TypeError,
        ),
    ],
)
def test_corrupted_hit_preserves_available_document_identity(hit, document_id, cause):
    repo = ColumnESRepo(MagicMock())
    with pytest.raises(CorruptedSemanticIndexDocumentError) as caught:
        repo._parse_hits({"hits": {"hits": [hit]}})
    assert caught.value.document_id == document_id
    assert caught.value.index_name == repo._index_name
    assert isinstance(caught.value.__cause__, cause)
