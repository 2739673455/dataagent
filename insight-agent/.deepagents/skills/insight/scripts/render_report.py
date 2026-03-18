"""
将结构化 JSON 渲染成自包含 HTML 报告。

用法:
  uv run python render_report.py --input analysis/report_payload.json --output outputs/report.html

schema:
{
  "meta": {
    "title": "分析报告",
    "subtitle": "2026-03-01 至 2026-03-15",
    "eyebrow": "Insight Report",
    "generated_at": "2026-03-16 14:30",
    "tags": ["业务分析", "渠道", "转化"]
  },
  "blocks": [
    {
      "type": "callout",
      "tone": "info",
      "title": "核心判断",
      "body": "本期增长主要由渠道结构变化驱动，而不是整体流量显著增加。"
    },
    {
      "type": "metrics",
      "title": "关键指标",
      "items": [
        {"label": "GMV", "value": "1,238,920", "note": "环比 +12.4%"},
        {"label": "转化率", "value": "4.8%", "note": "环比 +0.6pct"}
      ]
    },
    {
      "type": "section",
      "title": "渠道分析",
      "summary": "观察各渠道贡献、效率和结构变化。",
      "blocks": [
        {
          "type": "bar-chart",
          "title": "渠道贡献",
          "items": [
            {"label": "私域", "value": 42.1, "note": "%"},
            {"label": "搜索", "value": 27.4, "note": "%"}
          ]
        },
        {
          "type": "echarts",
          "title": "GMV 趋势",
          "height": 360,
          "option": {
            "tooltip": {"trigger": "axis"},
            "legend": {"data": ["2024年618", "2023年618"]},
            "xAxis": {"type": "category", "data": ["预热", "爆发", "返场"]},
            "yAxis": {"type": "value"},
            "series": [
              {"name": "2024年618", "type": "line", "data": [120, 260, 180]},
              {"name": "2023年618", "type": "line", "data": [110, 235, 210]}
            ]
          }
        },
        {
          "type": "table",
          "title": "渠道明细",
          "columns": ["渠道", "GMV", "转化率"],
          "rows": [
            ["私域", "521,000", "6.2%"],
            ["搜索", "339,000", "4.5%"]
          ]
        }
      ]
    }
  ]
}
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def as_list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def slugify(value: object) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "")).strip("-").lower()
    return text or "chart"


def json_for_script(value: object) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</script>", "<\\/script>")


def has_block_type(blocks: list, block_type: str) -> bool:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == block_type:
            return True
        if has_block_type(as_list(block.get("blocks")), block_type):
            return True
        for column in as_list(block.get("columns")):
            if isinstance(column, dict) and has_block_type(
                as_list(column.get("blocks")), block_type
            ):
                return True
    return False


def render_meta(meta: dict) -> str:
    tags = "".join(
        f'<span class="tag-chip">{esc(tag)}</span>' for tag in as_list(meta.get("tags"))
    )
    return f"""
    <section class="hero">
      <div class="eyebrow">{esc(meta.get("eyebrow", "Insight Report"))}</div>
      <h1>{esc(meta.get("title", "分析报告"))}</h1>
      <p class="subtitle">{esc(meta.get("subtitle", ""))}</p>
      <p class="meta-line">生成时间：{esc(meta.get("generated_at", ""))}</p>
      {'<div class="tag-row">' + tags + "</div>" if tags else ""}
    </section>
    """


def render_prose(block: dict) -> str:
    paragraphs = as_list(block.get("paragraphs"))
    if not paragraphs and block.get("body"):
        paragraphs = [block.get("body")]
    body = "".join(f"<p>{esc(text)}</p>" for text in paragraphs)
    return wrap_block(
        block,
        "prose-block",
        body,
    )


def render_list(block: dict) -> str:
    tag = "ol" if block.get("ordered") else "ul"
    items = "".join(f"<li>{esc(item)}</li>" for item in as_list(block.get("items")))
    return wrap_block(
        block,
        "list-block",
        f'<{tag} class="content-list">{items}</{tag}>',
    )


def render_metrics(block: dict) -> str:
    cards = []
    for item in as_list(block.get("items")):
        cards.append(
            f"""
            <div class="metric-card">
              <div class="metric-label">{esc(item.get("label", ""))}</div>
              <div class="metric-value">{esc(item.get("value", ""))}</div>
              <div class="metric-note">{esc(item.get("note", ""))}</div>
            </div>
            """
        )
    return wrap_block(
        block,
        "metrics-block",
        '<div class="metrics-grid">' + "".join(cards) + "</div>",
    )


def render_cards(block: dict) -> str:
    cards = []
    for item in as_list(block.get("items")):
        cards.append(
            f"""
            <div class="info-card">
              <div class="info-card-label">{esc(item.get("label", ""))}</div>
              <div class="info-card-value">{esc(item.get("value", ""))}</div>
              <div class="info-card-body">{esc(item.get("body", item.get("note", "")))}</div>
            </div>
            """
        )
    return wrap_block(
        block,
        "cards-block",
        '<div class="info-card-grid">' + "".join(cards) + "</div>",
    )


def render_table(block: dict) -> str:
    columns = as_list(block.get("columns"))
    rows = as_list(block.get("rows"))
    head = "".join(f"<th>{esc(col)}</th>" for col in columns)
    body_rows = []
    for row in rows:
        row_cells = row if isinstance(row, list) else [row]
        body_rows.append(
            "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row_cells) + "</tr>"
        )
    body = f"""
    <div class="table-scroll">
      <table>
        <thead><tr>{head}</tr></thead>
        <tbody>{"".join(body_rows)}</tbody>
      </table>
    </div>
    """
    return wrap_block(block, "table-block", body)


def render_bar_chart(block: dict) -> str:
    items = as_list(block.get("items"))
    max_value = max((to_float(item.get("value")) for item in items), default=1.0)
    rows = []
    for item in items:
        value = to_float(item.get("value"))
        width = 0 if max_value == 0 else max(4, round(value / max_value * 100, 2))
        rows.append(
            f"""
            <div class="chart-row">
              <div class="chart-label">{esc(item.get("label", ""))}</div>
              <div class="chart-bar-wrap">
                <div class="chart-bar" style="width:{width}%"></div>
              </div>
              <div class="chart-value">{esc(item.get("value", ""))}{esc(item.get("note", ""))}</div>
            </div>
            """
        )
    return wrap_block(block, "chart-block", "".join(rows))


def render_line_chart(block: dict) -> str:
    series = as_list(block.get("series"))
    if not series:
        return wrap_block(
            block,
            "chart-block",
            '<div class="chart-empty">暂无趋势数据</div>',
        )

    width = 760
    height = 260
    padding_left = 48
    padding_right = 20
    padding_top = 24
    padding_bottom = 36
    plot_width = width - padding_left - padding_right
    plot_height = height - padding_top - padding_bottom

    palette = [
        "#b5542e",
        "#1c6660",
        "#6e5bd6",
        "#d97706",
    ]

    all_values = [
        to_float(point.get("value"))
        for line in series
        for point in as_list(line.get("points"))
    ]
    max_value = max(all_values, default=1.0)
    min_value = min(all_values, default=0.0)
    if max_value == min_value:
        min_value = 0.0
    value_range = max(max_value - min_value, 1.0)

    max_points = max((len(as_list(line.get("points"))) for line in series), default=1)
    step_x = plot_width / max(max_points - 1, 1)

    x_labels = []
    if series:
        x_labels = [
            point.get("label", "") for point in as_list(series[0].get("points"))
        ]

    line_svgs = []
    legend = []
    point_labels = []

    for idx, line in enumerate(series):
        color = palette[idx % len(palette)]
        points = as_list(line.get("points"))
        coords = []
        for point_index, point in enumerate(points):
            value = to_float(point.get("value"))
            x = padding_left + step_x * point_index
            y = (
                padding_top
                + plot_height
                - ((value - min_value) / value_range) * plot_height
            )
            coords.append((x, y, point))

        if not coords:
            continue

        polyline_points = " ".join(f"{x:.2f},{y:.2f}" for x, y, _ in coords)
        circles = "".join(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}" />'
            for x, y, _ in coords
        )
        line_svgs.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="3" points="{polyline_points}" />{circles}'
        )
        legend.append(
            f'<div class="chart-legend-item"><span class="chart-legend-dot" style="background:{color}"></span>{esc(line.get("name", f"系列{idx + 1}"))}</div>'
        )

        point_labels.extend(
            f"""
            <div class="line-point-card">
              <div class="line-point-series">{esc(line.get("name", f"系列{idx + 1}"))}</div>
              <div class="line-point-label">{esc(point.get("label", ""))}</div>
              <div class="line-point-value">{esc(point.get("value", ""))}{esc(point.get("note", ""))}</div>
            </div>
            """
            for _, _, point in coords
        )

    y_ticks = []
    for tick in range(5):
        ratio = tick / 4
        y = padding_top + plot_height - ratio * plot_height
        value = min_value + ratio * value_range
        y_ticks.append(
            f"""
            <g>
              <line x1="{padding_left}" y1="{y:.2f}" x2="{width - padding_right}" y2="{y:.2f}" stroke="#eadfd5" stroke-width="1" />
              <text x="{padding_left - 8}" y="{y + 4:.2f}" text-anchor="end" font-size="11" fill="#8a7b6d">{value:.0f}</text>
            </g>
            """
        )

    x_tick_labels = []
    for point_index, label in enumerate(x_labels):
        x = padding_left + step_x * point_index
        x_tick_labels.append(
            f'<text x="{x:.2f}" y="{height - 10}" text-anchor="middle" font-size="11" fill="#8a7b6d">{esc(label)}</text>'
        )

    chart_html = f"""
    <div class="line-chart-wrap">
      <svg viewBox="0 0 {width} {height}" class="line-chart-svg" role="img" aria-label="{esc(block.get("title", "趋势图"))}">
        {"".join(y_ticks)}
        {"".join(line_svgs)}
        {"".join(x_tick_labels)}
      </svg>
      {'<div class="chart-legend">' + "".join(legend) + "</div>" if legend else ""}
      {'<div class="line-point-grid">' + "".join(point_labels) + "</div>" if point_labels else ""}
    </div>
    """
    return wrap_block(block, "chart-block line-chart-block", chart_html)


def render_echarts(block: dict) -> str:
    option = block.get("option") or {}
    option_json = json.dumps(option, sort_keys=True, ensure_ascii=False)
    option_digest = hashlib.md5(option_json.encode("utf-8")).hexdigest()[:8]
    chart_id = f"echarts-{slugify(block.get('title'))}-{option_digest}"
    height = max(240, int(to_float(block.get("height", 360), 360)))
    if not isinstance(option, dict) or not option:
        return wrap_block(
            block,
            "chart-block",
            '<div class="chart-empty">暂无图表配置</div>',
        )

    chart_html = f"""
    <div class="echarts-wrap">
      <div id="{chart_id}" class="echarts-canvas" style="height:{height}px;"></div>
      <script type="application/json" id="{chart_id}-option">{json_for_script(option)}</script>
      <script>
        window.__insightEchartsQueue = window.__insightEchartsQueue || [];
        window.__insightEchartsQueue.push({{
          id: "{chart_id}",
          optionId: "{chart_id}-option"
        }});
      </script>
    </div>
    """
    return wrap_block(block, "chart-block echarts-block", chart_html)


def render_callout(block: dict) -> str:
    tone = esc(block.get("tone", "info"))
    body = "".join(f"<p>{esc(text)}</p>" for text in as_list(block.get("paragraphs")))
    if not body and block.get("body"):
        body = f"<p>{esc(block.get('body', ''))}</p>"
    return f"""
    <section class="callout callout-{tone}">
      <div class="block-head">
        <h2>{esc(block.get("title", "提示"))}</h2>
        <p>{esc(block.get("summary", ""))}</p>
      </div>
      <div class="callout-body">{body}</div>
    </section>
    """


def render_columns(block: dict) -> str:
    columns = []
    for column in as_list(block.get("columns")):
        inner = "".join(render_block(item) for item in as_list(column.get("blocks")))
        columns.append(
            f"""
            <div class="column-card">
              {'<div class="column-title">' + esc(column.get("title", "")) + "</div>" if column.get("title") else ""}
              {inner}
            </div>
            """
        )
    return wrap_block(
        block,
        "columns-block",
        '<div class="columns-grid">' + "".join(columns) + "</div>",
    )


def render_section(block: dict) -> str:
    inner = "".join(render_block(item) for item in as_list(block.get("blocks")))
    return f"""
    <section class="panel section-block">
      <div class="block-head">
        <h2>{esc(block.get("title", "章节"))}</h2>
        <p>{esc(block.get("summary", ""))}</p>
      </div>
      {inner}
    </section>
    """


def wrap_block(block: dict, class_name: str, inner_html: str) -> str:
    return f"""
    <section class="panel {class_name}">
      {'<div class="block-head"><h2>' + esc(block.get("title", "")) + "</h2><p>" + esc(block.get("summary", "")) + "</p></div>" if block.get("title") or block.get("summary") else ""}
      {inner_html}
    </section>
    """


def render_block(block: dict) -> str:
    block_type = block.get("type")
    renderers = {
        "prose": render_prose,
        "list": render_list,
        "metrics": render_metrics,
        "cards": render_cards,
        "table": render_table,
        "bar-chart": render_bar_chart,
        "line-chart": render_line_chart,
        "echarts": render_echarts,
        "ranking": render_bar_chart,
        "callout": render_callout,
        "columns": render_columns,
        "section": render_section,
    }
    if block_type not in renderers:
        raise ValueError(f"Unsupported block type: {block_type}")
    return renderers[block_type](block)


def render_report(payload: dict) -> str:
    meta = payload.get("meta", {})
    block_items = as_list(payload.get("blocks"))
    blocks = "".join(render_block(block) for block in block_items)
    has_echarts = has_block_type(block_items, "echarts")
    echarts_assets = (
        """
  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
  <script>
    window.addEventListener("DOMContentLoaded", function () {
      if (!window.echarts || !window.__insightEchartsQueue) {
        return;
      }
      window.__insightEchartsQueue.forEach(function (item) {
        const el = document.getElementById(item.id);
        const optionEl = document.getElementById(item.optionId);
        if (!el || !optionEl) {
          return;
        }
        const option = JSON.parse(optionEl.textContent || "{}");
        const chart = window.echarts.init(el);
        chart.setOption(option);
        window.addEventListener("resize", function () {
          chart.resize();
        });
      });
    });
  </script>
    """
        if has_echarts
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{esc(meta.get("title", "分析报告"))}</title>
  {echarts_assets}
  <style>
    :root {{
      --bg: #f6efe6;
      --paper: rgba(255, 252, 247, 0.95);
      --paper-strong: #fffdf9;
      --ink: #221d18;
      --muted: #6f655a;
      --line: #dfd3c6;
      --brand: #b5542e;
      --brand-soft: #f4dccf;
      --accent: #1c6660;
      --accent-soft: #dbeceb;
      --warn: #9a4d26;
      --warn-soft: #f6dfd0;
      --shadow: 0 20px 50px rgba(84, 58, 34, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
      line-height: 1.6;
      background:
        radial-gradient(circle at top left, rgba(181, 84, 46, 0.14), transparent 25%),
        radial-gradient(circle at top right, rgba(28, 102, 96, 0.14), transparent 22%),
        linear-gradient(180deg, #f7f1e9 0%, #f3ece3 100%);
    }}
    .page {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .hero, .panel, .callout {{
      background: var(--paper);
      border: 1px solid rgba(223, 211, 198, 0.92);
      border-radius: 24px;
      box-shadow: var(--shadow);
    }}
    .hero {{
      padding: 32px;
      background: linear-gradient(135deg, rgba(255,253,249,0.98), rgba(247,238,229,0.98));
    }}
    .eyebrow {{
      display: inline-flex;
      padding: 6px 12px;
      border-radius: 999px;
      background: var(--brand-soft);
      color: var(--brand);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 14px 0 10px;
      font-size: clamp(30px, 5vw, 54px);
      line-height: 1.05;
    }}
    .subtitle, .meta-line {{
      margin: 0;
      color: var(--muted);
    }}
    .tag-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
    }}
    .tag-chip {{
      display: inline-flex;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(28, 102, 96, 0.08);
      color: var(--accent);
      font-size: 12px;
    }}
    .panel, .callout {{
      margin-top: 24px;
      padding: 24px;
    }}
    .block-head {{
      margin-bottom: 18px;
    }}
    .block-head h2 {{
      margin: 0 0 8px;
      font-size: 26px;
    }}
    .block-head p {{
      margin: 0;
      color: var(--muted);
    }}
    .prose-block p, .callout-body p {{
      margin: 10px 0 0;
    }}
    .content-list {{
      margin: 0;
      padding-left: 20px;
    }}
    .content-list li {{
      margin: 8px 0;
    }}
    .metrics-grid, .info-card-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
    }}
    .metric-card, .info-card, .column-card {{
      background: var(--paper-strong);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
    }}
    .metric-label, .info-card-label {{
      color: var(--muted);
      font-size: 14px;
    }}
    .metric-value, .info-card-value {{
      margin-top: 8px;
      font-size: 28px;
      font-weight: 700;
      color: var(--accent);
    }}
    .metric-note, .info-card-body {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
    }}
    .table-scroll {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--paper-strong);
      border-radius: 16px;
      overflow: hidden;
    }}
    th, td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }}
    th {{
      color: var(--muted);
      background: #fbf5ef;
      font-weight: 600;
    }}
    .chart-row {{
      display: grid;
      grid-template-columns: 160px 1fr 110px;
      gap: 12px;
      align-items: center;
      margin: 12px 0;
    }}
    .chart-label, .chart-value {{
      font-size: 14px;
    }}
    .chart-bar-wrap {{
      height: 12px;
      border-radius: 999px;
      background: #eee1d5;
      overflow: hidden;
    }}
    .chart-bar {{
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--brand), #e09755);
    }}
    .chart-empty {{
      color: var(--muted);
      font-size: 14px;
    }}
    .echarts-canvas {{
      width: 100%;
      min-height: 240px;
      border-radius: 18px;
      background: linear-gradient(180deg, #fffdf9 0%, #fcf7f1 100%);
      border: 1px solid var(--line);
      padding: 8px;
    }}
    .line-chart-wrap {{
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    .line-chart-svg {{
      width: 100%;
      height: auto;
      border-radius: 18px;
      background: linear-gradient(180deg, #fffdf9 0%, #fcf7f1 100%);
      border: 1px solid var(--line);
      padding: 8px;
    }}
    .chart-legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .chart-legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .chart-legend-dot {{
      width: 10px;
      height: 10px;
      border-radius: 999px;
      flex-shrink: 0;
    }}
    .line-point-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 12px;
    }}
    .line-point-card {{
      background: var(--paper-strong);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px;
    }}
    .line-point-series {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .line-point-label {{
      font-size: 13px;
      color: var(--ink);
    }}
    .line-point-value {{
      margin-top: 6px;
      font-size: 20px;
      font-weight: 700;
      color: var(--accent);
    }}
    .callout-info {{
      border-left: 6px solid var(--accent);
      background: linear-gradient(135deg, rgba(219,236,235,0.76), rgba(255,252,247,0.98));
    }}
    .callout-warn {{
      border-left: 6px solid var(--warn);
      background: linear-gradient(135deg, rgba(246,223,208,0.88), rgba(255,252,247,0.98));
    }}
    .callout-success {{
      border-left: 6px solid #3b7f52;
      background: linear-gradient(135deg, rgba(221,238,225,0.88), rgba(255,252,247,0.98));
    }}
    .columns-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
    }}
    .column-title {{
      margin-bottom: 12px;
      font-size: 18px;
      font-weight: 700;
      color: var(--brand);
    }}
    .column-card .panel, .column-card .callout {{
      margin-top: 16px;
      box-shadow: none;
    }}
    @media (max-width: 720px) {{
      .hero, .panel, .callout {{
        padding: 20px;
        border-radius: 18px;
      }}
      .chart-row {{
        grid-template-columns: 1fr;
      }}
      th, td {{
        white-space: normal;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    {render_meta(meta)}
    {blocks}
  </main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="JSON 输入文件")
    parser.add_argument("--output", required=True, help="HTML 输出文件")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    html_text = render_report(payload)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
