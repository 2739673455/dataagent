#!/usr/bin/env python3
"""
将结构化 JSON 渲染成自包含 HTML 报告。

用法:
  uv run render_report.py --input analysis/report_payload.json --output outputs/report.html

JSON 示例:
{
  "title": "粉丝互动赢好礼用户分析及618营销建议",
  "subtitle": "分析周期：2026-05-01 至 2026-05-31",
  "generated_at": "2026-03-14 10:00",
  "summary": [
    "老客占比 75.14%，活动以老客激活为主。",
    "45-54 岁用户略高，但整体年龄分布较均衡。"
  ],
  "metrics": [
    {"label": "下单用户数", "value": "22,719", "note": "活动总参与用户"},
    {"label": "老客占比", "value": "75.14%", "note": "老客主导"}
  ],
  "sections": [
    {
      "title": "用户画像分析",
      "summary": "用户结构以老客和普通会员为主。",
      "insights": [
        "老客占比高，说明激活效果好于拉新效果。",
        "女性用户略高，可增加女性偏好商品推荐。"
      ],
      "highlights": [
        {"label": "老客", "value": "17,070"},
        {"label": "新客", "value": "5,649"}
      ],
      "rankings": [
        {
          "title": "年龄占比",
          "items": [
            {"name": "45-54岁", "value": 20.38, "note": "%"},
            {"name": "35-44岁", "value": 20.11, "note": "%"}
          ]
        }
      ],
      "tables": [
        {
          "title": "新老客结构",
          "columns": ["类型", "用户数", "占比"],
          "rows": [
            ["老客", "17,070", "75.14%"],
            ["新客", "5,649", "24.86%"]
          ]
        }
      ]
    }
  ],
  "recommendations": [
    "增加新客专属券和首单礼，提升拉新占比。",
    "围绕高贡献会员做分层运营和复购激励。"
  ],
  "appendix": [
    "数据来源：db_query 导出的订单与用户标签结果",
    "统计口径：以下单用户为准"
  ]
}
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def render_metrics(metrics: list[dict]) -> str:
    cards = []
    for metric in metrics:
        cards.append(
            f"""
            <div class="metric-card">
              <div class="metric-label">{esc(metric.get("label", ""))}</div>
              <div class="metric-value">{esc(metric.get("value", ""))}</div>
              <div class="metric-note">{esc(metric.get("note", ""))}</div>
            </div>
            """
        )
    return '<section class="metrics-grid">' + "".join(cards) + "</section>"


def render_table(table: dict) -> str:
    columns = table.get("columns", [])
    rows = table.get("rows", [])
    head = "".join(f"<th>{esc(col)}</th>" for col in columns)
    body_rows = []
    for row in rows:
        body_rows.append("<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>")
    return f"""
    <div class="table-block">
      <div class="block-title">{esc(table.get('title', '明细表'))}</div>
      <table>
        <thead><tr>{head}</tr></thead>
        <tbody>{''.join(body_rows)}</tbody>
      </table>
    </div>
    """


def render_rankings(rankings: list[dict]) -> str:
    blocks = []
    for ranking in rankings:
        items = ranking.get("items", [])
        max_value = max((float(item.get("value", 0) or 0) for item in items), default=1)
        rows = []
        for item in items:
            value = float(item.get("value", 0) or 0)
            width = 0 if max_value == 0 else max(6, round(value / max_value * 100, 2))
            rows.append(
                f"""
                <div class="ranking-row">
                  <div class="ranking-name">{esc(item.get("name", ""))}</div>
                  <div class="ranking-bar-wrap">
                    <div class="ranking-bar" style="width:{width}%"></div>
                  </div>
                  <div class="ranking-value">{esc(item.get("value", ""))}{esc(item.get("note", ""))}</div>
                </div>
                """
            )
        blocks.append(
            f"""
            <div class="ranking-block">
              <div class="block-title">{esc(ranking.get("title", "排行"))}</div>
              {''.join(rows)}
            </div>
            """
        )
    return "".join(blocks)


def render_section(section: dict) -> str:
    insights = "".join(f"<li>{esc(item)}</li>" for item in section.get("insights", []))
    highlights = "".join(
        f"""
        <div class="highlight-card">
          <div class="highlight-label">{esc(item.get('label', ''))}</div>
          <div class="highlight-value">{esc(item.get('value', ''))}</div>
        </div>
        """
        for item in section.get("highlights", [])
    )
    tables = "".join(render_table(table) for table in section.get("tables", []))
    rankings = render_rankings(section.get("rankings", []))
    return f"""
    <section class="report-section">
      <div class="section-head">
        <h2>{esc(section.get("title", "分析章节"))}</h2>
        <p>{esc(section.get("summary", ""))}</p>
      </div>
      {'<div class="highlights-grid">' + highlights + '</div>' if highlights else ''}
      {'<ul class="insight-list">' + insights + '</ul>' if insights else ''}
      {rankings}
      {tables}
    </section>
    """


def render_report(payload: dict) -> str:
    summary = "".join(f"<li>{esc(item)}</li>" for item in payload.get("summary", []))
    recommendations = "".join(f"<li>{esc(item)}</li>" for item in payload.get("recommendations", []))
    appendix = "".join(f"<li>{esc(item)}</li>" for item in payload.get("appendix", []))
    sections = "".join(render_section(section) for section in payload.get("sections", []))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{esc(payload.get("title", "分析报告"))}</title>
  <style>
    :root {{
      --bg: #f5efe6;
      --paper: #fffdf9;
      --ink: #1f1c18;
      --muted: #6c6358;
      --line: #ddd2c4;
      --brand: #c45c2e;
      --brand-soft: #f2d8c5;
      --accent: #215f5c;
      --shadow: 0 18px 50px rgba(74, 50, 29, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(196, 92, 46, 0.12), transparent 28%),
        radial-gradient(circle at top right, rgba(33, 95, 92, 0.12), transparent 24%),
        linear-gradient(180deg, #f7f0e6 0%, #f4ede2 100%);
      line-height: 1.6;
    }}
    .page {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(255,253,249,0.96), rgba(250,241,232,0.96));
      border: 1px solid rgba(221, 210, 196, 0.9);
      border-radius: 28px;
      padding: 32px;
      box-shadow: var(--shadow);
    }}
    .eyebrow {{
      display: inline-block;
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
    .subtitle, .meta {{
      color: var(--muted);
      margin: 0;
    }}
    .summary-panel, .recommend-panel, .appendix-panel, .report-section {{
      margin-top: 24px;
      background: rgba(255, 253, 249, 0.92);
      border: 1px solid rgba(221, 210, 196, 0.9);
      border-radius: 24px;
      padding: 24px;
      box-shadow: var(--shadow);
    }}
    .section-head h2 {{
      margin: 0 0 8px;
      font-size: 26px;
    }}
    .section-head p {{
      margin: 0;
      color: var(--muted);
    }}
    .metrics-grid, .highlights-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-top: 24px;
    }}
    .metric-card, .highlight-card {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 18px;
    }}
    .metric-label, .highlight-label {{
      color: var(--muted);
      font-size: 14px;
    }}
    .metric-value, .highlight-value {{
      margin-top: 8px;
      font-size: 28px;
      font-weight: 700;
      color: var(--accent);
    }}
    .metric-note {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
    }}
    .insight-list, .summary-list, .recommend-list, .appendix-list {{
      margin: 14px 0 0;
      padding-left: 20px;
    }}
    .block-title {{
      margin: 22px 0 10px;
      font-weight: 700;
      color: var(--brand);
    }}
    .ranking-row {{
      display: grid;
      grid-template-columns: 140px 1fr 100px;
      gap: 12px;
      align-items: center;
      margin: 10px 0;
    }}
    .ranking-bar-wrap {{
      height: 12px;
      background: #eee2d6;
      border-radius: 999px;
      overflow: hidden;
    }}
    .ranking-bar {{
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--brand), #e19654);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 16px;
      background: var(--paper);
    }}
    th, td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      background: #fbf6f0;
    }}
    @media (max-width: 720px) {{
      .hero, .summary-panel, .recommend-panel, .appendix-panel, .report-section {{
        padding: 20px;
        border-radius: 18px;
      }}
      .ranking-row {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <div class="eyebrow">Insight Report</div>
      <h1>{esc(payload.get("title", "分析报告"))}</h1>
      <p class="subtitle">{esc(payload.get("subtitle", ""))}</p>
      <p class="meta">生成时间：{esc(payload.get("generated_at", ""))}</p>
      {render_metrics(payload.get("metrics", []))}
    </section>

    <section class="summary-panel">
      <div class="section-head">
        <h2>核心结论</h2>
        <p>先看最值得被行动化的洞察。</p>
      </div>
      <ul class="summary-list">{summary}</ul>
    </section>

    {sections}

    <section class="recommend-panel">
      <div class="section-head">
        <h2>营销建议</h2>
        <p>将分析结果转为可执行动作。</p>
      </div>
      <ul class="recommend-list">{recommendations}</ul>
    </section>

    <section class="appendix-panel">
      <div class="section-head">
        <h2>附录与口径</h2>
        <p>便于报告复核与后续复用。</p>
      </div>
      <ul class="appendix-list">{appendix}</ul>
    </section>
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
