"""字段和指标索引共享解析与损坏资源处理回归测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.metadata.errors import CorruptedSemanticIndexDocumentError
from app.metadata.models.catalog import ColumnInfo, MetricInfo, column_resource_key
from app.metadata.models.search import SemanticIndexDelta, SemanticIndexDocument
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.value_index import ValueESRepo


@pytest.mark.parametrize("repo_class", [ColumnESRepo, MetricESRepo, ValueESRepo])
@pytest.mark.parametrize("exists", [False, True])
def test_ensure_index_only_creates_missing_indexes(repo_class, exists) -> None:
    client = MagicMock()
    client.indices.exists = AsyncMock(return_value=exists)
    client.indices.create = AsyncMock()
    repo = repo_class(client)
    asyncio.run(repo.ensure_index())
    client.indices.put_mapping.assert_not_called()
    if exists:
        client.indices.create.assert_not_awaited()
    else:
        client.indices.create.assert_awaited_once_with(
            index=repo._index_name, mappings=repo._index_mappings
        )
        if repo_class is ValueESRepo:
            assert client.indices.create.call_args.kwargs["mappings"]["properties"][
                "sync_generation"
            ] == {"type": "keyword"}


@pytest.mark.parametrize("repo_class", [ColumnESRepo, MetricESRepo])
@pytest.mark.parametrize(
    "hit,document_id,resource_key,error_type",
    [
        (None, "<missing>", "<missing>", TypeError),
        ({"_id": 7}, "7", "<missing>", TypeError),
        ({"_id": "doc", "_source": []}, "doc", "<missing>", TypeError),
        (
            {"_id": "doc", "_source": {"resource_key": "key", "payload": []}},
            "doc",
            "key",
            TypeError,
        ),
        (
            {
                "_id": "doc",
                "_source": {"resource_key": "key", "payload": {"invalid": 1}},
            },
            "doc",
            "key",
            TypeError,
        ),
    ],
)
def test_corrupted_hit_preserves_available_identity_and_cause(
    repo_class, hit, document_id, resource_key, error_type
) -> None:
    repo = repo_class(MagicMock())
    with (
        patch("app.metadata.repositories.semantic_index.logger") as logger,
        pytest.raises(CorruptedSemanticIndexDocumentError) as caught,
    ):
        repo.parse_hits(
            {"hits": {"hits": [hit]}},
            ColumnInfo if repo_class is ColumnESRepo else MetricInfo,
        )
    assert caught.value.document_id == document_id
    assert isinstance(caught.value.__cause__, error_type)
    logger.bind.assert_called_once_with(
        index_name=repo._index_name,
        document_id=document_id,
        resource_key=resource_key,
        stage="read-corrupted-document",
    )


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


@pytest.mark.parametrize(
    "repo_class,method_name,argument,permission_name,resource",
    [
        (
            ColumnESRepo,
            "search_text_hits",
            "x",
            "allowed_columns",
            ("orders", "amount"),
        ),
        (
            ColumnESRepo,
            "search_vector_hits",
            [0.1],
            "allowed_columns",
            ("orders", "amount"),
        ),
        (MetricESRepo, "search_text_hits", "x", "allowed_metrics", "amount"),
        (MetricESRepo, "search_vector_hits", [0.1], "allowed_metrics", "amount"),
        (ValueESRepo, "search_hits", "x", "allowed_columns", ("orders", "amount")),
    ],
)
@pytest.mark.parametrize("permission", ["empty", "unrestricted", "restricted"])
def test_search_permission_boundaries(
    repo_class, method_name, argument, permission_name, resource, permission
) -> None:
    client = MagicMock(
        search=AsyncMock(return_value=SimpleNamespace(body={"hits": {"hits": []}}))
    )
    allowed = {
        "empty": frozenset(),
        "unrestricted": None,
        "restricted": frozenset({resource}),
    }[permission]
    hits = asyncio.run(
        getattr(repo_class(client), method_name)(argument, **{permission_name: allowed})
    )
    assert hits == []
    if permission == "empty":
        assert client.mock_calls == []
        return
    client.search.assert_awaited_once()
    kwargs = client.search.call_args.kwargs
    if method_name == "search_vector_hits":
        resource_filter = kwargs["knn"].get("filter")
    else:
        filters = kwargs["query"].get("bool", {}).get("filter")
        resource_filter = filters[0] if filters else None
    if permission == "unrestricted":
        assert resource_filter is None
    else:
        key = (
            column_resource_key(*resource) if isinstance(resource, tuple) else resource
        )
        assert resource_filter == {"terms": {"resource_key": [key]}}


@pytest.mark.parametrize("repo_class", [ColumnESRepo, MetricESRepo])
def test_index_creation_preserves_search_and_version_mappings(repo_class) -> None:
    client = MagicMock()
    client.indices.exists = AsyncMock(return_value=False)
    client.indices.create = AsyncMock()
    repo = repo_class(client)
    asyncio.run(repo.ensure_index())
    kwargs = client.indices.create.call_args.kwargs
    assert kwargs["index"] == repo._index_name
    mappings = kwargs["mappings"]
    assert mappings["dynamic"] is False
    assert set(mappings["properties"]) == {
        "resource_key",
        "text",
        "text_type",
        "meta_version",
        "embedding_revision",
        "payload_hash",
        "embedding",
        "payload",
    }
    assert mappings["properties"]["meta_version"] == {"type": "long"}
    assert mappings["properties"]["payload"] == {"type": "object", "enabled": False}


@pytest.mark.parametrize("repo_class", [ColumnESRepo, MetricESRepo])
def test_mixed_delta_preserves_vectors_versions_and_index_routing(repo_class) -> None:
    client = MagicMock(
        bulk=AsyncMock(return_value=SimpleNamespace(body={"errors": False}))
    )
    client.indices.refresh = AsyncMock()
    repo = repo_class(client)

    def document(document_id, embedding):
        return SemanticIndexDocument(
            id=document_id,
            resource_key="resource",
            text="销售额",
            text_type="name",
            embedding=embedding,
            embedding_revision="revision",
            meta_version=2,
            payload_hash="hash",
            payload={"name": "amount"},
        )

    asyncio.run(
        repo.apply_delta(
            SemanticIndexDelta(
                create=[document("new", [0.1])],
                update=[document("payload-only", None), document("new-vector", [0.2])],
                delete_ids=["removed"],
                unchanged_count=0,
            )
        )
    )
    operations = client.bulk.call_args.kwargs["operations"]
    assert operations[0] == {"index": {"_index": repo._index_name, "_id": "new"}}
    assert operations[2] == {
        "update": {"_index": repo._index_name, "_id": "payload-only"}
    }
    assert operations[4] == {"index": {"_index": repo._index_name, "_id": "new-vector"}}
    assert operations[6] == {"delete": {"_index": repo._index_name, "_id": "removed"}}
    assert operations[1]["embedding"] == [0.1]
    assert operations[5]["embedding"] == [0.2]
    assert "embedding" not in operations[3]["doc"]
    for source in [operations[1], operations[3]["doc"], operations[5]]:
        assert source["meta_version"] == 2
        assert source["embedding_revision"] == "revision"
        assert source["payload_hash"] == "hash"
        assert source["payload"] == {"name": "amount"}
    client.indices.refresh.assert_awaited_once_with(index=repo._index_name)
