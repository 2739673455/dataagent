"""召回取消传播和字段上下文边界回归。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.metadata.models.catalog import ColumnInfo, TableInfo
from app.metadata.models.search import (
    SemanticMatchReason,
    SemanticMetricRecallResult,
    SemanticResourceRecallRequest,
)
from app.metadata.services.search import (
    SemanticCatalog,
    SemanticResourceRecallService,
    _ColumnContextBuilder,
    _RankedCandidates,
    _RecallContext,
)


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
        service = SemanticResourceRecallService(
            MagicMock(aembed_documents=AsyncMock(side_effect=asyncio.CancelledError())),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            context.catalog,
            MagicMock(),
            "source",
            "db",
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
