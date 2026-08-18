"""共享确定性分析内核测试"""

from __future__ import annotations

import unittest
from typing import Any, cast

import pandas as pd

from app.analysis.anomaly_detection import detect_anomalies, validate_time_series
from app.analysis.attribution import calculate_contribution
from app.analysis.visualization import (
    build_interactive_table,
    build_report,
    render_chart,
)


def _contribution_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "region": ["east", "east", "west", "west", "north", "north"],
            "period": ["before", "after"] * 3,
            "sales": [100, 70, 50, 40, 20, 40],
        }
    )


class AttributionKernelTest(unittest.TestCase):
    """验证加和指标变化贡献分解"""

    def test_returns_totals_sorted_rows_and_ratios(self) -> None:
        result = calculate_contribution(
            _contribution_frame(),
            dimension_column="region",
            metric_column="sales",
            period_column="period",
            baseline_value="before",
            comparison_value="after",
        )

        self.assertEqual(result["method"], "additive_change_decomposition")
        self.assertEqual(result["total_baseline"], 170.0)
        self.assertEqual(result["total_comparison"], 150.0)
        self.assertEqual(result["total_change"], -20.0)
        self.assertEqual(result["coverage_ratio"], 1.0)
        self.assertEqual(result["residual"], 0.0)
        self.assertEqual(result["dimension_count"], 3)
        self.assertEqual(
            [row["dimension_value"] for row in result["rows"]],
            ["east", "north", "west"],
        )
        self.assertEqual(
            [row["change"] for row in result["rows"]],
            [-30.0, 20.0, -10.0],
        )
        self.assertEqual(
            [row["contribution_ratio"] for row in result["rows"]],
            [1.5, -1.0, 0.5],
        )
        self.assertAlmostEqual(result["rows"][0]["absolute_change_share"], 0.5)

    def test_tracks_invalid_metric_rows_in_selected_periods(self) -> None:
        frame = pd.DataFrame(
            {
                "region": ["east", "east", "west"],
                "period": ["before", "after", "ignored"],
                "sales": [10, "invalid", "also-invalid"],
            }
        )

        result = calculate_contribution(
            frame,
            dimension_column="region",
            metric_column="sales",
            period_column="period",
            baseline_value="before",
            comparison_value="after",
        )

        self.assertEqual(result["invalid_metric_rows"], 1)
        self.assertEqual(result["total_baseline"], 10.0)
        self.assertEqual(result["total_comparison"], 0.0)

    def test_applies_top_n_coverage_and_residual(self) -> None:
        result = calculate_contribution(
            _contribution_frame(),
            dimension_column="region",
            metric_column="sales",
            period_column="period",
            baseline_value="before",
            comparison_value="after",
            top_n=1,
        )

        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["dimension_value"], "east")
        self.assertEqual(result["coverage_ratio"], 1.5)
        self.assertEqual(result["residual"], 10.0)

    def test_handles_zero_total_change(self) -> None:
        frame = pd.DataFrame(
            {
                "region": ["east", "east", "west", "west"],
                "period": ["before", "after", "before", "after"],
                "sales": [10, 20, 20, 10],
            }
        )

        result = calculate_contribution(
            frame,
            dimension_column="region",
            metric_column="sales",
            period_column="period",
            baseline_value="before",
            comparison_value="after",
        )

        self.assertEqual(result["total_change"], 0.0)
        self.assertEqual(result["coverage_ratio"], 1.0)
        self.assertEqual(result["residual"], 0.0)
        self.assertEqual(
            [row["contribution_ratio"] for row in result["rows"]],
            [0.0, 0.0],
        )
        self.assertEqual(
            [row["absolute_change_share"] for row in result["rows"]],
            [0.5, 0.5],
        )

    def test_fills_absent_comparison_period_with_zero(self) -> None:
        result = calculate_contribution(
            pd.DataFrame(
                {
                    "region": ["east", "west"],
                    "period": ["before", "before"],
                    "sales": [10, 5],
                }
            ),
            dimension_column="region",
            metric_column="sales",
            period_column="period",
            baseline_value="before",
            comparison_value="after",
        )

        self.assertEqual(result["total_baseline"], 15.0)
        self.assertEqual(result["total_comparison"], 0.0)
        self.assertTrue(all(row["comparison"] == 0.0 for row in result["rows"]))

    def test_rejects_missing_columns(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing columns: period, region"):
            calculate_contribution(
                pd.DataFrame({"sales": [1]}),
                dimension_column="region",
                metric_column="sales",
                period_column="period",
                baseline_value="before",
                comparison_value="after",
            )

    def test_rejects_top_n_out_of_range(self) -> None:
        for top_n in (0, 501):
            with (
                self.subTest(top_n=top_n),
                self.assertRaisesRegex(ValueError, "top_n must be between 1 and 500"),
            ):
                calculate_contribution(
                    _contribution_frame(),
                    dimension_column="region",
                    metric_column="sales",
                    period_column="period",
                    baseline_value="before",
                    comparison_value="after",
                    top_n=top_n,
                )

    def test_rejects_no_numeric_rows_for_requested_periods(self) -> None:
        with self.assertRaisesRegex(ValueError, "no numeric metric rows"):
            calculate_contribution(
                pd.DataFrame(
                    {
                        "region": ["east", "west"],
                        "period": ["before", "after"],
                        "sales": ["invalid", None],
                    }
                ),
                dimension_column="region",
                metric_column="sales",
                period_column="period",
                baseline_value="before",
                comparison_value="after",
            )


class AnomalyDetectionKernelTest(unittest.TestCase):
    """验证时序质量、点异常和变化点检测"""

    def test_reports_invalid_duplicate_non_monotonic_and_gaps(self) -> None:
        frame = pd.DataFrame(
            {
                "time": [
                    "2026-01-03",
                    "invalid",
                    "2026-01-01",
                    "2026-01-01",
                    "2026-01-10",
                ],
                "value": [1, "invalid", 2, 3, 4],
            }
        )

        result = validate_time_series(
            frame,
            time_column="time",
            value_column="value",
        )

        self.assertEqual(result["row_count"], 5)
        self.assertEqual(result["invalid_time_rows"], 1)
        self.assertEqual(result["invalid_value_rows"], 1)
        self.assertEqual(result["duplicate_timestamps"], 1)
        self.assertFalse(result["is_time_monotonic"])
        self.assertEqual(
            result["time_gaps"],
            [
                {
                    "row_index": "4",
                    "gap_seconds": 604800.0,
                    "expected_seconds": 172800.0,
                }
            ],
        )

    def test_returns_point_and_change_anomalies(self) -> None:
        frame = pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=10, freq="D"),
                "value": [10, 10, 10, 10, 10, 10, 10, 10, 10, 100],
            }
        )

        result = detect_anomalies(
            frame,
            time_column="time",
            value_column="value",
            threshold=2,
        )

        self.assertEqual(result["method"], "median_mad_robust_zscore")
        self.assertEqual(result["observation_count"], 10)
        self.assertEqual(len(result["point_anomalies"]), 1)
        self.assertEqual(len(result["change_points"]), 1)
        self.assertEqual(
            result["point_anomalies"][0]["time"],
            "2026-01-10T00:00:00+00:00",
        )
        self.assertEqual(result["change_points"][0]["change"], 90.0)

    def test_sorts_time_and_drops_invalid_observations(self) -> None:
        frame = pd.DataFrame(
            {
                "time": [
                    "2026-01-03",
                    "invalid",
                    "2026-01-01",
                    "2026-01-04",
                    "2026-01-02",
                    "2026-01-05",
                ],
                "value": [100, 999, 10, 100, 10, "invalid"],
            }
        )

        result = detect_anomalies(
            frame,
            time_column="time",
            value_column="value",
            threshold=2,
        )

        self.assertEqual(result["observation_count"], 4)
        self.assertEqual(result["quality"]["invalid_time_rows"], 1)
        self.assertEqual(result["quality"]["invalid_value_rows"], 1)
        self.assertEqual(len(result["change_points"]), 1)
        self.assertEqual(
            result["change_points"][0]["time"],
            "2026-01-03T00:00:00+00:00",
        )

    def test_handles_constant_series(self) -> None:
        result = detect_anomalies(
            pd.DataFrame(
                {
                    "time": pd.date_range("2026-01-01", periods=4, freq="D"),
                    "value": [5, 5, 5, 5],
                }
            ),
            time_column="time",
            value_column="value",
        )

        self.assertEqual(result["point_anomalies"], [])
        self.assertEqual(result["change_points"], [])

    def test_rejects_threshold_out_of_range(self) -> None:
        frame = pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=3, freq="D"),
                "value": [1, 2, 3],
            }
        )
        for threshold in (1.99, 10.01):
            with (
                self.subTest(threshold=threshold),
                self.assertRaisesRegex(
                    ValueError, "threshold must be between 2 and 10"
                ),
            ):
                detect_anomalies(
                    frame,
                    time_column="time",
                    value_column="value",
                    threshold=threshold,
                )

    def test_requires_three_valid_observations(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least three valid observations"):
            detect_anomalies(
                pd.DataFrame(
                    {
                        "time": ["2026-01-01", "2026-01-02", "invalid"],
                        "value": [1, 2, 3],
                    }
                ),
                time_column="time",
                value_column="value",
            )

    def test_rejects_missing_columns(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing columns: value"):
            validate_time_series(
                pd.DataFrame({"time": ["2026-01-01"]}),
                time_column="time",
                value_column="value",
            )


class VisualizationKernelTest(unittest.TestCase):
    """验证静态图表、HTML 报告和交互表格数据"""

    def test_builds_traceable_option_and_self_contained_html(self) -> None:
        option, document = render_chart(
            pd.DataFrame({"month": ["Jan", "Feb"], "sales": [10, 20]}),
            x_column="month",
            y_columns=["sales"],
            chart_type="line",
            title="Sales trend",
            source_path="/analyses/sales/shared/source.csv",
        )

        self.assertEqual(option["series"][0]["data"], [10.0, 20.0])
        self.assertEqual(
            option["title"]["subtext"],
            "Source: /analyses/sales/shared/source.csv",
        )
        self.assertIn("<svg", document)
        self.assertIn("default-src 'none'", document)
        self.assertIn("/analyses/sales/shared/source.csv", document)
        self.assertNotIn("<script", document)
        self.assertNotIn("https://", document)

    def test_emits_line_bar_and_scatter_svg_marks(self) -> None:
        frame = pd.DataFrame({"month": ["Jan", "Feb"], "sales": [10, 20]})

        _, line_document = render_chart(
            frame,
            x_column="month",
            y_columns=["sales"],
            chart_type="line",
            title="Line",
            source_path="/analyses/source.csv",
        )
        _, bar_document = render_chart(
            frame,
            x_column="month",
            y_columns=["sales"],
            chart_type="bar",
            title="Bar",
            source_path="/analyses/source.csv",
        )
        _, scatter_document = render_chart(
            frame,
            x_column="month",
            y_columns=["sales"],
            chart_type="scatter",
            title="Scatter",
            source_path="/analyses/source.csv",
        )

        self.assertIn("<polyline points=", line_document)
        self.assertIn('width="352.00"', bar_document)
        self.assertIn("<circle cx=", scatter_document)

    def test_preserves_null_values_and_escapes_html_content(self) -> None:
        option, document = render_chart(
            pd.DataFrame(
                {
                    "label": ["<script>alert(1)</script>", None],
                    "<sales>": ["invalid", 20],
                }
            ),
            x_column="label",
            y_columns=["<sales>"],
            chart_type="line",
            title="<unsafe>",
            source_path="/analyses/<source>.csv",
        )

        self.assertEqual(
            option["xAxis"]["data"],
            ["<script>alert(1)</script>", None],
        )
        self.assertEqual(option["series"][0]["data"], [None, 20.0])
        self.assertIn("&lt;unsafe&gt;", document)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", document)
        self.assertIn("&lt;sales&gt;", document)
        self.assertIn("/analyses/&lt;source&gt;.csv", document)
        self.assertNotIn("<script>", document)

    def test_rejects_invalid_chart_parameters(self) -> None:
        frame = pd.DataFrame({"month": ["Jan"], "sales": [10]})
        common = {
            "frame": frame,
            "chart_type": "line",
            "title": "Sales",
            "source_path": "/analyses/source.csv",
        }

        with self.assertRaisesRegex(ValueError, "missing column: missing"):
            render_chart(
                **common,
                x_column="missing",
                y_columns=["sales"],
            )
        with self.assertRaisesRegex(ValueError, "between 1 and 20"):
            render_chart(
                **common,
                x_column="month",
                y_columns=[],
            )
        with self.assertRaisesRegex(ValueError, "between 1 and 20"):
            render_chart(
                **common,
                x_column="month",
                y_columns=[f"series_{index}" for index in range(21)],
            )
        with self.assertRaisesRegex(ValueError, "missing columns: missing"):
            render_chart(
                **common,
                x_column="month",
                y_columns=["missing"],
            )
        with self.assertRaisesRegex(ValueError, "unsupported chart type"):
            render_chart(
                frame,
                x_column="month",
                y_columns=["sales"],
                chart_type=cast(Any, "pie"),
                title="Sales",
                source_path="/analyses/source.csv",
            )

    def test_report_escapes_all_supplied_content(self) -> None:
        document = build_report(
            pd.DataFrame({"name": ["<script>alert(1)</script>"]}),
            title="<title>",
            summary="<summary>",
            findings=["<finding>"],
            source_path="/analyses/<source>.csv",
        )

        for escaped in (
            "&lt;title&gt;",
            "&lt;summary&gt;",
            "&lt;finding&gt;",
            "&lt;script&gt;alert(1)&lt;/script&gt;",
            "/analyses/&lt;source&gt;.csv",
        ):
            with self.subTest(escaped=escaped):
                self.assertIn(escaped, document)
        self.assertIn("default-src 'none'", document)
        self.assertNotIn("<script>", document)
        self.assertNotIn("https://", document)

    def test_report_applies_max_rows_and_rejects_invalid_limit(self) -> None:
        frame = pd.DataFrame({"name": ["visible", "hidden"]})

        document = build_report(
            frame,
            title="Report",
            summary="Summary",
            findings=[],
            source_path="/analyses/source.csv",
            max_rows=1,
        )

        self.assertIn("<td>visible</td>", document)
        self.assertNotIn("<td>hidden</td>", document)
        for max_rows in (0, 1001):
            with (
                self.subTest(max_rows=max_rows),
                self.assertRaisesRegex(
                    ValueError, "max_rows must be between 1 and 1000"
                ),
            ):
                build_report(
                    frame,
                    title="Report",
                    summary="Summary",
                    findings=[],
                    source_path="/analyses/source.csv",
                    max_rows=max_rows,
                )

    def test_interactive_table_uses_requested_or_all_columns(self) -> None:
        frame = pd.DataFrame(
            {
                "region": ["east", "west"],
                "sales": [10, 20],
                "profit": [1, 2],
            }
        )

        selected = build_interactive_table(
            frame,
            columns=["sales", "region"],
            max_rows=1,
            source_path="/analyses/source.csv",
        )
        all_columns = build_interactive_table(
            frame,
            columns=[],
            max_rows=2,
            source_path="/analyses/source.csv",
        )

        self.assertEqual(selected["format"], "dataagent-interactive-table-v1")
        self.assertEqual(selected["columns"], ["sales", "region"])
        self.assertEqual(selected["rows"], [{"sales": 10, "region": "east"}])
        self.assertEqual(selected["total_rows"], 2)
        self.assertTrue(selected["truncated"])
        self.assertEqual(all_columns["columns"], ["region", "sales", "profit"])
        self.assertFalse(all_columns["truncated"])

    def test_interactive_table_serializes_nulls_and_iso_dates(self) -> None:
        result = build_interactive_table(
            pd.DataFrame(
                {
                    "time": [pd.Timestamp("2026-01-01T00:00:00Z"), pd.NaT],
                    "value": [None, 1.5],
                }
            ),
            columns=["time", "value"],
            max_rows=2,
            source_path="/analyses/source.csv",
        )

        self.assertEqual(result["rows"][0]["time"], "2026-01-01T00:00:00.000Z")
        self.assertIsNone(result["rows"][0]["value"])
        self.assertIsNone(result["rows"][1]["time"])

    def test_interactive_table_rejects_invalid_parameters(self) -> None:
        frame = pd.DataFrame({"region": ["east"]})

        with self.assertRaisesRegex(ValueError, "missing columns: missing"):
            build_interactive_table(
                frame,
                columns=["missing"],
                max_rows=1,
                source_path="/analyses/source.csv",
            )
        with self.assertRaisesRegex(ValueError, "between 1 and 100 entries"):
            build_interactive_table(
                frame,
                columns=[f"column_{index}" for index in range(101)],
                max_rows=1,
                source_path="/analyses/source.csv",
            )
        with self.assertRaisesRegex(ValueError, "between 1 and 100 entries"):
            build_interactive_table(
                pd.DataFrame(),
                columns=[],
                max_rows=1,
                source_path="/analyses/source.csv",
            )
        for max_rows in (0, 1001):
            with (
                self.subTest(max_rows=max_rows),
                self.assertRaisesRegex(
                    ValueError, "max_rows must be between 1 and 1000"
                ),
            ):
                build_interactive_table(
                    frame,
                    columns=["region"],
                    max_rows=max_rows,
                    source_path="/analyses/source.csv",
                )
