---
name: visualization
description: 将已验证的分析结果制作成静态图表或自包含 HTML 报告。用于图表绘制、报告排版与最终展示交付；按任务规模选择内容，不重新编造数据或分析结论。
---

# 图表与自包含 HTML 报告

## 交付范围

- 单图任务交付 PNG 或 SVG，不额外生成整份报告。
- 最终报告或综合报告交付自包含 HTML，数据表、图表和脚本可作为配套文件。
- 使用 analysis 阶段已验证的证据；发现口径或数据问题时先修正计算，不能通过修改图表文字掩盖问题。
- 只生成有实质内容的章节，不为填满模板编造 KPI、结论或检查结果。

## 信息层级与排版

- 结论前置，先说明核心发现，再展开证据、限制和建议。
- 顶部通常放 1～4 个与问题直接相关的核心指标；次要指标放表格，不机械凑数量。
- 宽屏可用约 6:4 的图文分栏，窄屏改为单列。按任务需要选用趋势、分群、归因或明细模块。
- 对比表可增加份额条和变化标签，但同时保留数字、单位和文字说明，不能只靠颜色传达含义。
- 涨跌不等于好坏：成本、投诉率等指标下降可能是改善，颜色按业务含义选择。
- 复杂口径、SQL 和方法说明可放入原生 `<details>` 附录；关键限制必须在正文中可见。
- 报告正文不展示 `/data/...` 等内部路径，也不把它们作为浏览器链接。来源说明使用表名、业务口径、统计周期和样本量；可下载的证据另按任务交付规则提供。

## 数据与图表

- 用 Python 的 Matplotlib 或 Seaborn 生成高清静态 PNG/SVG，图表直接读取证据表。
- 保持数值、单位、时间窗口、时区、排序、分母与证据一致；缺失值与零值分开表达。
- 标题描述具体问题，坐标轴和图例说明口径；对数轴、截断轴及异常值处理必须注明。
- Matplotlib 使用沙箱已安装的中文字体，不联网下载字体、不生成点阵字形：

```python
import matplotlib.pyplot as plt

plt.rcParams.update({
    "figure.dpi": 200,
    "font.family": "WenQuanYi Zen Hei",
    "axes.unicode_minus": False,
    "font.size": 10,
})
```

- Pillow 使用 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`。
- 推荐蓝色 `#2563eb` 为主色，灰色 `#64748b` 为辅助色；正负含义明确时再使用绿/红色。
- 减少装饰与多余网格线，保证标签、图例和图像尺寸足以阅读。

## HTML 约束

- CSS 放在文档内的 `<style>` 或 style 属性中；图片转为 Base64 Data URI。
- 不引用外部样式、字体、脚本或图片文件，报告应可离线打开。
- 不使用 JavaScript、事件处理属性、iframe 或其他动态嵌入；折叠使用原生 `<details>`。
- 数据文本写入 HTML 前转义，避免分类名称等内容破坏标签。

以下只是布局骨架，按真实任务删减扩展。所有 `{{...}}` 占位符必须由验证过的数据替换，不能原样交付，也不能填入虚构示例数值：

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{报告标题}}</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #f8fafc; color: #334155;
    font: 15px/1.6 "Microsoft YaHei", "WenQuanYi Zen Hei", sans-serif; }
  main { max-width: 1200px; margin: auto; padding: 24px; }
  header, section, details { background: white; border: 1px solid #e2e8f0;
    border-radius: 12px; padding: 20px; margin-bottom: 20px; }
  h1, h2 { color: #0f172a; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }
  .value { font-size: 28px; font-weight: bold; font-variant-numeric: tabular-nums; }
  .split { display: grid; grid-template-columns: 3fr 2fr; gap: 24px; }
  img { width: 100%; height: auto; }
  .table-scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 10px; border-bottom: 1px solid #e2e8f0; text-align: left; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  summary { cursor: pointer; }
  @media (max-width: 800px) { .split { grid-template-columns: 1fr; } }
</style>
</head>
<body><main>
  <header><h1>{{报告标题}}</h1><p>{{时间窗口、核心口径与样本覆盖}}</p></header>
  <section class="kpis">{{按实际问题生成指标卡及比较基准}}</section>
  <section class="split">
    <div>{{内嵌图表及说明}}</div><div>{{与该图表对应的关键发现和限制}}</div>
  </section>
  <section class="table-scroll">{{必要的证据表格}}</section>
  <details><summary>方法与数据来源</summary>{{口径、来源、验证结果及限制}}</details>
</main></body>
</html>
```

## 交付前检查

- 核对报告中的数字、单位、比较基准和结论能追溯到证据，不保留模板占位符。
- 用代码检查 HTML 图片均已内嵌、没有外部资源或脚本，产物存在且非空。
- 确认图表脚本、证据表与最终报告使用同一批计算结果；只写实际完成的校验，不套用“全部通过”等结论。
- 当前没有图片查看或浏览器工具。可检查数据、源码、图片尺寸与文件完整性，但不能声称已视觉验收；影响交付的未验证项需说明。
