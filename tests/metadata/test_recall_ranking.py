"""召回排序和直接命中告警回归。"""

from app.metadata.models.catalog import ColumnInfo
from app.metadata.services.search import (
    SemanticCatalog,
    SemanticResourceRecallService,
    _ColumnContextBuilder,
    _RankedCandidates,
)


def test_fused_scores_keep_order_normalization_and_truncation():
    scores = {}
    for key, rank in [("a", 1), ("b", 2), ("b", 1), ("c", 3)]:
        SemanticResourceRecallService._add_candidate_score(
            scores, key, SemanticResourceRecallService._rrf_score(rank)
        )
    ranked, truncated = SemanticResourceRecallService._rank_candidates(scores, 2)
    assert ranked == [("b", 1.0), ("a", round((1 / 61) / (1 / 62 + 1 / 61), 6))]
    assert truncated


def test_only_direct_column_matches_warn_when_index_not_ready():
    columns = {
        ("orders", name): ColumnInfo(
            t_name="orders",
            name=name,
            type="VARCHAR",
            description=name,
            alias=[],
            examples=[],
            index_ready=False,
            index_values=True,
        )
        for name in ("direct", "owner")
    }
    warnings = []
    results, _, _ = _ColumnContextBuilder(
        SemanticCatalog(tables={}, columns=columns, metrics={}), warnings
    ).build(
        _RankedCandidates(
            columns=[(("orders", "direct"), 1.0)],
            metrics=[],
            values=[(("orders", "owner", "paid"), 1.0)],
            truncated=False,
        )
    )
    assert warnings == ["字段语义索引尚未就绪: orders.direct"]
    assert {item.name for item in results} == {"direct", "owner"}
    assert all("match_reasons" not in item.model_dump() for item in results)
