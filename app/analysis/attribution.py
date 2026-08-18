"""指标变化的确定性贡献分解"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd


def calculate_contribution(
    frame: pd.DataFrame,
    *,
    dimension_column: str,
    metric_column: str,
    period_column: str,
    baseline_value: str,
    comparison_value: str,
    top_n: int = 50,
) -> dict[str, Any]:
    """按维度计算两个时期之间的加和指标变化贡献"""
    required = {dimension_column, metric_column, period_column}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")
    if not 1 <= top_n <= 500:
        raise ValueError("top_n must be between 1 and 500")

    periods = cast(Any, frame[period_column]).astype(str)
    selected: Any = frame.loc[
        periods.isin([baseline_value, comparison_value]),
        [dimension_column, metric_column, period_column],
    ].copy()
    selected[metric_column] = pd.to_numeric(
        selected[metric_column],
        errors="coerce",
    )
    invalid_metric_rows = int(selected[metric_column].isna().sum())
    selected = selected.dropna(subset=[metric_column])
    if selected.empty:
        raise ValueError("no numeric metric rows for the requested periods")

    grouped: Any = (
        selected.groupby(
            [dimension_column, period_column],
            dropna=False,
        )[metric_column]
        .sum()
        .unstack(fill_value=0)
    )
    for period in (baseline_value, comparison_value):
        if period not in grouped.columns:
            grouped[period] = 0.0

    details: Any = pd.DataFrame(
        {
            "dimension_value": grouped.index.astype(str),
            "baseline": grouped[baseline_value].astype(float).to_numpy(),
            "comparison": grouped[comparison_value].astype(float).to_numpy(),
        }
    )
    details["change"] = details["comparison"] - details["baseline"]
    total_baseline = float(details["baseline"].sum())
    total_comparison = float(details["comparison"].sum())
    total_change = total_comparison - total_baseline
    absolute_change = float(details["change"].abs().sum())
    details["contribution_ratio"] = (
        details["change"] / total_change if total_change != 0 else 0.0
    )
    details["absolute_change_share"] = (
        details["change"].abs() / absolute_change if absolute_change != 0 else 0.0
    )
    details = details.sort_values(
        "change",
        key=lambda values: values.abs(),
        ascending=False,
    )
    returned = details.head(top_n)
    returned_change = float(returned["change"].sum())

    return {
        "method": "additive_change_decomposition",
        "dimension_column": dimension_column,
        "metric_column": metric_column,
        "period_column": period_column,
        "baseline_value": baseline_value,
        "comparison_value": comparison_value,
        "total_baseline": total_baseline,
        "total_comparison": total_comparison,
        "total_change": total_change,
        "coverage_ratio": (
            returned_change / total_change if total_change != 0 else 1.0
        ),
        "residual": total_change - returned_change,
        "invalid_metric_rows": invalid_metric_rows,
        "dimension_count": len(details),
        "rows": returned.to_dict(orient="records"),
    }
