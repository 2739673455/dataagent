"""确定性的时序异常与数据质量检测"""

from __future__ import annotations

import math
from typing import Any, cast

import pandas as pd


def validate_time_series(
    frame: pd.DataFrame,
    *,
    time_column: str,
    value_column: str,
) -> dict[str, Any]:
    """检查时间、数值、重复和时间断层"""
    missing = sorted({time_column, value_column} - set(frame.columns))
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")
    times: Any = pd.to_datetime(
        cast(Any, frame[time_column]),
        errors="coerce",
        utc=True,
    )
    values: Any = pd.to_numeric(cast(Any, frame[value_column]), errors="coerce")
    original_valid_times = times.dropna()
    valid_times = original_valid_times.sort_values()
    gaps: list[dict[str, Any]] = []
    if len(valid_times) >= 3:
        differences = valid_times.diff().dropna()
        expected = differences.median()
        if expected > pd.Timedelta(0):
            gap_indexes = differences[differences > expected * 1.5].index
            gaps = [
                {
                    "row_index": str(index),
                    "gap_seconds": float(differences.loc[index].total_seconds()),
                    "expected_seconds": float(expected.total_seconds()),
                }
                for index in gap_indexes
            ]
    return {
        "row_count": len(frame),
        "invalid_time_rows": int(times.isna().sum()),
        "invalid_value_rows": int(values.isna().sum()),
        "duplicate_timestamps": int(times.dropna().duplicated().sum()),
        "time_gaps": gaps,
        "is_time_monotonic": bool(original_valid_times.is_monotonic_increasing),
    }


def detect_anomalies(
    frame: pd.DataFrame,
    *,
    time_column: str,
    value_column: str,
    threshold: float = 3.5,
) -> dict[str, Any]:
    """返回点异常、变化点及数据质量摘要"""
    if not 2 <= threshold <= 10:
        raise ValueError("threshold must be between 2 and 10")
    quality = validate_time_series(
        frame,
        time_column=time_column,
        value_column=value_column,
    )
    working: Any = pd.DataFrame(
        {
            "time": pd.to_datetime(
                cast(Any, frame[time_column]),
                errors="coerce",
                utc=True,
            ),
            "value": pd.to_numeric(
                cast(Any, frame[value_column]),
                errors="coerce",
            ),
        }
    ).dropna()
    working = working.sort_values("time")
    if len(working) < 3:
        raise ValueError("at least three valid observations are required")

    values: Any = working["value"].astype(float)
    median = float(values.median())
    mad = float((values - median).abs().median())
    if mad > 0:
        scores = 0.67448975 * (values - median) / mad
    else:
        standard_deviation = float(values.std(ddof=0))
        scores = (
            (values - float(values.mean())) / standard_deviation
            if standard_deviation > 0
            else pd.Series(0.0, index=values.index)
        )

    differences = values.diff()
    diff_median = float(differences.dropna().median())
    diff_mad = float((differences - diff_median).abs().dropna().median())
    if diff_mad > 0:
        change_scores = 0.67448975 * (differences - diff_median) / diff_mad
    else:
        change_scores = (differences - diff_median).map(
            lambda difference: (
                0.0
                if pd.isna(difference) or difference == 0
                else math.copysign(threshold + 1, difference)
            )
        )

    point_mask = scores.abs() >= threshold
    change_mask = change_scores.abs() >= threshold
    point_anomalies = [
        {
            "time": working.loc[index, "time"].isoformat(),
            "value": float(values.loc[index]),
            "score": float(scores.loc[index]),
        }
        for index in working.index[point_mask]
    ]
    change_points = [
        {
            "time": working.loc[index, "time"].isoformat(),
            "value": float(values.loc[index]),
            "change": float(differences.loc[index]),
            "score": float(change_scores.loc[index]),
        }
        for index in working.index[change_mask]
        if not pd.isna(differences.loc[index])
    ]
    return {
        "method": "median_mad_robust_zscore",
        "threshold": threshold,
        "quality": quality,
        "point_anomalies": point_anomalies,
        "change_points": change_points,
        "observation_count": len(working),
    }
