"""确定性的静态图表、交互表格数据和 HTML 报告渲染"""

from __future__ import annotations

import html
import json
from typing import Any, Literal, cast

import pandas as pd

ChartType = Literal["line", "bar", "scatter"]


def render_chart(
    frame: pd.DataFrame,
    *,
    x_column: str,
    y_columns: list[str],
    chart_type: ChartType,
    title: str,
    source_path: str,
) -> tuple[dict[str, Any], str]:
    """生成图表配置和自包含静态 SVG 文档"""
    if x_column not in frame.columns:
        raise ValueError(f"missing column: {x_column}")
    if not y_columns or len(y_columns) > 20:
        raise ValueError("y_columns must contain between 1 and 20 columns")
    if chart_type not in {"line", "bar", "scatter"}:
        raise ValueError("unsupported chart type")
    missing = sorted(set(y_columns) - set(frame.columns))
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")
    x_values = [
        None if pd.isna(value) else str(value)
        for value in cast(Any, frame[x_column]).tolist()
    ]
    series = []
    for column in y_columns:
        numeric = pd.to_numeric(cast(Any, frame[column]), errors="coerce")
        series.append(
            {
                "name": column,
                "type": chart_type,
                "data": [
                    None if pd.isna(value) else float(value)
                    for value in cast(Any, numeric).tolist()
                ],
                "connectNulls": False,
            }
        )
    option = {
        "title": {"text": title, "subtext": f"Source: {source_path}"},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": y_columns},
        "xAxis": {"type": "category", "data": x_values},
        "yAxis": {"type": "value"},
        "dataZoom": [{"type": "inside"}, {"type": "slider"}],
        "series": series,
    }

    width = 1000
    height = 600
    left = 80
    right = 40
    top = 90
    bottom = 90
    plot_width = width - left - right
    plot_height = height - top - bottom
    numeric_values = [
        value for item in series for value in item["data"] if value is not None
    ]
    minimum = min(numeric_values, default=0.0)
    maximum = max(numeric_values, default=1.0)
    if minimum == maximum:
        minimum -= 1.0
        maximum += 1.0

    def x_position(index: int) -> float:
        return left + (
            plot_width * index / max(1, len(x_values) - 1)
            if len(x_values) > 1
            else plot_width / 2
        )

    def y_position(value: float) -> float:
        return top + plot_height * (maximum - value) / (maximum - minimum)

    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c"]
    marks: list[str] = []
    for series_index, item in enumerate(series):
        color = colors[series_index % len(colors)]
        points = [
            (index, value)
            for index, value in enumerate(item["data"])
            if value is not None
        ]
        if chart_type == "bar":
            group_width = plot_width / max(1, len(x_values)) * 0.8
            bar_width = group_width / max(1, len(series))
            zero_y = y_position(min(max(0.0, minimum), maximum))
            for index, value in points:
                x = (
                    left
                    + plot_width * (index + 0.1) / max(1, len(x_values))
                    + series_index * bar_width
                )
                y = y_position(float(value))
                marks.append(
                    f'<rect x="{x:.2f}" y="{min(y, zero_y):.2f}" '
                    f'width="{bar_width:.2f}" height="{abs(zero_y - y):.2f}" '
                    f'fill="{color}" />'
                )
        elif chart_type == "line":
            coordinates = " ".join(
                f"{x_position(index):.2f},{y_position(float(value)):.2f}"
                for index, value in points
            )
            marks.append(
                f'<polyline points="{coordinates}" fill="none" '
                f'stroke="{color}" stroke-width="2" />'
            )
        else:
            marks.extend(
                f'<circle cx="{x_position(index):.2f}" '
                f'cy="{y_position(float(value)):.2f}" r="4" fill="{color}" />'
                for index, value in points
            )
    tick_step = max(1, len(x_values) // 12)
    x_labels = "".join(
        f'<text x="{x_position(index):.2f}" y="{height - 55}" '
        f'text-anchor="middle" font-size="11">{html.escape(str(value))}</text>'
        for index, value in enumerate(x_values)
        if index % tick_step == 0
    )
    legend = "".join(
        f'<rect x="{left + index * 150}" y="48" width="12" height="12" '
        f'fill="{colors[index % len(colors)]}" />'
        f'<text x="{left + 18 + index * 150}" y="59" font-size="12">'
        f"{html.escape(str(item['name']))}</text>"
        for index, item in enumerate(series)
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">
<rect width="100%" height="100%" fill="white" />
<text x="{width / 2}" y="28" text-anchor="middle" font-size="20">{html.escape(title)}</text>
{legend}
<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#333" />
<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#333" />
<text x="{left - 8}" y="{top + 5}" text-anchor="end" font-size="11">{maximum:.4g}</text>
<text x="{left - 8}" y="{top + plot_height}" text-anchor="end" font-size="11">{minimum:.4g}</text>
{"".join(marks)}{x_labels}
</svg>"""
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
</head><body>{svg}<p>数据来源：{html.escape(source_path)}</p></body></html>"""
    return option, document


def build_report(
    frame: pd.DataFrame,
    *,
    title: str,
    summary: str,
    findings: list[str],
    source_path: str,
    max_rows: int = 200,
) -> str:
    """生成带来源、结论和数据表的静态 HTML 报告"""
    if not 1 <= max_rows <= 1000:
        raise ValueError("max_rows must be between 1 and 1000")
    finding_items = "".join(f"<li>{html.escape(item)}</li>" for item in findings)
    table = frame.head(max_rows).to_html(index=False, escape=True, border=0)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>body{{font-family:sans-serif;max-width:1200px;margin:auto;padding:24px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:6px}}</style></head>
<body><h1>{html.escape(title)}</h1><p>{html.escape(summary)}</p>
<h2>主要发现</h2><ul>{finding_items}</ul><p>数据来源：{html.escape(source_path)}</p>
<h2>数据预览</h2>{table}</body></html>"""


def build_interactive_table(
    frame: pd.DataFrame,
    *,
    columns: list[str],
    max_rows: int,
    source_path: str,
) -> dict[str, Any]:
    """生成由可信前端筛选、排序和分页的表格数据"""
    selected_columns = columns or [str(value) for value in frame.columns]
    if not selected_columns or len(selected_columns) > 100:
        raise ValueError("columns must contain between 1 and 100 entries")
    missing = sorted(set(selected_columns) - set(frame.columns))
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")
    if not 1 <= max_rows <= 1000:
        raise ValueError("max_rows must be between 1 and 1000")
    selected = frame.loc[:, selected_columns].head(max_rows)
    rows = json.loads(selected.to_json(orient="records", date_format="iso"))
    return {
        "format": "dataagent-interactive-table-v1",
        "source_path": source_path,
        "columns": selected_columns,
        "rows": rows,
        "total_rows": len(frame),
        "truncated": len(frame) > len(selected),
    }
