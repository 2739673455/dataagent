"""召回取消传播和字段上下文边界回归。"""

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.identity.contracts import AssetAccessPolicy, AssetIdentity
from app.metadata.application.search import (
    SemanticCatalog,
    SemanticResourceService,
    _ColumnContextBuilder,
    _RankedCandidates,
    _RecallContext,
)
from app.metadata.contracts import (
    SemanticMatchReason,
    SemanticMetricRecallResult,
    SemanticResourceRecallRequest,
)
from app.metadata.models.catalog import ColumnInfo, TableInfo
from app.shared.config.app_config import cfg
from app.shared.contracts.search import SearchHit


def test_context_preserves_examples_and_deduplicates_inclusion_reasons():
    key = ("orders", "id")
    column = ColumnInfo(
        t_name="orders",
        name="id",
        type="BIGINT",
        description="编号",
        alias=[],
        examples=[1, 2, 3, 4],
        index_values=True,
        meta_version=1,
        index_version=1,
    )
    catalog = SemanticCatalog(
        tables={
            "orders": TableInfo(
                name="orders",
                role="fact",
                description="订单",
                primary_key_columns=["id"],
                meta_version=1,
            )
        },
        columns={key: column},
        metrics={},
    )
    reasons = [SemanticMatchReason(match_type="fulltext", term="编号", score=2.0)]
    columns, _, truncated = _ColumnContextBuilder(catalog, []).build(
        _RankedCandidates(
            columns=[(key, 1.0, reasons)],
            metrics=[],
            values=[(("orders", "id", "1"), 1.0, []), (("orders", "id", "2"), 0.5, [])],
            truncated=False,
        )
    )
    assert columns[0].inclusion_reasons == [
        "direct_match",
        "value_owner",
        "primary_key",
    ]
    assert columns[0].examples == [1, 2, 3]
    assert column.examples == [1, 2, 3, 4]
    assert columns[0].rank_score == 1.0
    assert columns[0].match_reasons == reasons
    assert not truncated


@pytest.mark.parametrize(
    "reference", [{"t_name": "orders"}, {"c_name": "id"}, {"t_name": 1, "c_name": "id"}]
)
def test_metric_response_requires_complete_string_column_reference(reference):
    with pytest.raises(ValidationError):
        SemanticMetricRecallResult.model_validate(
            {
                "name": "count",
                "description": "数量",
                "alias": [],
                "relevant_columns": [reference],
                "rank_score": 1.0,
                "match_reasons": [],
                "meta_version": 1,
                "index_version": 1,
                "index_status": "current",
            }
        )


def test_metric_response_schema_uses_column_reference():
    schema = SemanticMetricRecallResult.model_json_schema()
    assert schema["properties"]["relevant_columns"]["items"] == {
        "$ref": "#/$defs/ColumnReference"
    }
    assert set(schema["$defs"]["ColumnReference"]["required"]) == {"t_name", "c_name"}


def test_foreign_key_context_is_sorted_one_level_and_adds_target_primary_keys():
    def column(table, name, target_table=None, target_column=None):
        return ColumnInfo(
            t_name=table,
            name=name,
            type="BIGINT",
            description=name,
            alias=[],
            examples=[],
            index_values=False,
            meta_version=1,
            index_version=1,
            reference_t_name=target_table,
            reference_c_name=target_column,
        )

    # Deliberately reverse source order; b → c must not expand after a introduces b.
    items = [
        column("b", "c_id", "c", "id"),
        column("c", "id"),
        column("a", "z_id", "b", "code"),
        column("b", "id"),
        column("b", "code"),
        column("a", "b_id", "b", "code"),
        column("a", "incomplete", "b"),
        column("a", "id"),
    ]
    catalog = SemanticCatalog(
        tables={
            name: TableInfo(
                name=name,
                role="fact",
                description=name,
                primary_key_columns=["id"],
                meta_version=1,
            )
            for name in ("a", "b", "c")
        },
        columns={(item.t_name, item.name): item for item in items},
        metrics={},
    )
    columns, tables, truncated = _ColumnContextBuilder(catalog, []).build(
        _RankedCandidates(
            columns=[(("a", "id"), 1.0, [])], metrics=[], values=[], truncated=False
        )
    )
    assert [(item.t_name, item.name) for item in columns] == [
        ("a", "id"),
        ("a", "b_id"),
        ("b", "code"),
        ("a", "z_id"),
        ("b", "id"),
    ]
    assert [item.name for item in tables] == ["a", "b"]
    assert columns[2].inclusion_reasons == ["reference_target"]
    assert not truncated


@pytest.mark.parametrize("stage", ["embedding", "backend"])
def test_cancellation_propagates_without_recording_backend_failure(stage):
    context = _RecallContext(
        request=SemanticResourceRecallRequest(
            terms=["收入"], resource_types=["column"]
        ),
        catalog=SemanticCatalog(tables={}, columns={}, metrics={}),
    )
    if stage == "backend":
        with pytest.raises(asyncio.CancelledError):
            context.record_backend_failure(
                "字段全文",
                asyncio.CancelledError(),
                resource_type="column",
                channel="fulltext",
            )
    else:
        context.catalog.columns[("orders", "amount")] = ColumnInfo(
            t_name="orders", name="amount"
        )
        service = SemanticResourceService(
            MagicMock(),
            MagicMock(),
            MagicMock(aembed_documents=AsyncMock(side_effect=asyncio.CancelledError())),
        )
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(service._retrieve(context))
    assert context.failures == []


@pytest.mark.parametrize("limit", [1, 5, 20])
def test_candidate_expansion_respects_request_boundaries(limit):
    context = _RecallContext(
        request=SemanticResourceRecallRequest(
            terms=["收入"], resource_types=["column"], limit_per_type=limit
        ),
        catalog=SemanticCatalog(tables={}, columns={}, metrics={}),
    )
    assert context.search_limit == limit * 3


def test_shared_service_keeps_concurrent_permissions_and_query_limits_per_request():
    columns = [
        ColumnInfo(
            t_name="orders",
            name=name,
            type="BIGINT",
            description=name,
            alias=[],
            examples=[],
            index_values=False,
            meta_version=1,
            index_version=1,
        )
        for name in ("amount", "cost")
    ]
    table = TableInfo(
        name="orders",
        role="fact",
        description="订单",
        primary_key_columns=[],
        meta_version=1,
    )
    catalog_repo = MagicMock(
        list_table_infos=AsyncMock(return_value=[table]),
        list_column_infos=AsyncMock(return_value=columns),
        list_metric_infos=AsyncMock(return_value=[]),
    )

    @asynccontextmanager
    async def session():
        yield MagicMock()

    async def run():
        both_started = asyncio.Event()
        started = set()
        active = defaultdict(int)
        allowed = defaultdict(set)

        async def hits(term, *, allowed_keys, limit):
            request_name = term.split("-")[0]
            active[request_name] += 1
            assert active[request_name] == 1
            allowed[request_name].add(allowed_keys)
            started.add(request_name)
            if len(started) == 2:
                both_started.set()
            try:
                await both_started.wait()
                await asyncio.sleep(0)
                return [SearchHit(item=column, score=1.0) for column in columns]
            finally:
                active[request_name] -= 1

        service = SemanticResourceService(
            MagicMock(session=session),
            MagicMock(),
            MagicMock(aembed_documents=AsyncMock(return_value=[[0.1], [0.2]])),
            max_concurrent_index_queries=1,
        )
        service._column_repo = MagicMock(
            search_text_hits=AsyncMock(side_effect=hits),
            search_vector_hits=AsyncMock(return_value=[]),
        )
        with patch(
            "app.metadata.application.search.MetaPGRepo", return_value=catalog_repo
        ):
            async with asyncio.timeout(1):
                responses = await asyncio.gather(
                    *(
                        service.recall(
                            SemanticResourceRecallRequest(
                                terms=[f"{request_name}-1", f"{request_name}-2"],
                                resource_types=["column"],
                            ),
                            AssetAccessPolicy(
                                user_id,
                                grants=frozenset(
                                    {
                                        AssetIdentity(
                                            cfg.query.data_source,
                                            cfg.doris.database,
                                            "orders",
                                            column_name,
                                        )
                                    }
                                ),
                            ),
                        )
                        for user_id, request_name, column_name in (
                            (7, "left", "amount"),
                            (8, "right", "cost"),
                        )
                    )
                )
        assert [
            [column.name for column in response.columns] for response in responses
        ] == [["amount"], ["cost"]]
        assert all(response.status == "success" for response in responses)
        assert allowed == {
            "left": {frozenset({("orders", "amount")})},
            "right": {frozenset({("orders", "cost")})},
        }
        assert dict(active) == {"left": 0, "right": 0}

    asyncio.run(run())
