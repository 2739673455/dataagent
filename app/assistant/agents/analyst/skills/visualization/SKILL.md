---
name: visualization
description: 将 Analyst 已验证的分析结果制作成专业、美观、清晰的自包含 HTML 报告与图表。采用结构化排版、分栏网格与降噪设计；用于最终可视化、综合报告与展示交付。
---

# 专业商业数据可视化与 HTML 报告规范

## 1. 设计核心理念：结构化排版与认知降噪

报告混乱的根源通常在于**缺乏视觉层级**、**过多指标平铺**、**大段文字堆积**与**单列无限下滚**。优秀的分析报告必须遵循 **MECE 原则** 与 **BLUF（Bottom Line Up Front，结论前置）**，做到：

1. **强视觉层级（Visual Hierarchy）**：
   - **Hero KPI 只放 3~4 个核心指标**，严禁并排堆叠 8~10 个无主次的指标卡片；次要与诊断指标放入结构化小表。
   - **图文联动分栏（Split View）**：大盘走势图与核心业务发现左右分栏并排（6:4），让读者“左眼看图、右眼读结论”。
2. **结构化卡片（Componentized Cards）**：
   - 杜绝长篇大论的自然段；将类目分级、归因拆解放入“🟢 增长驱动 / 🔴 下滑预警 / ⚪ 结构切换”的分栏矩阵卡片中。
3. **表格视觉增强（Visualized Tables）**：
   - 表格不只填纯数字：为份额字段配置内联迷你进度条（Progress Mini-bar），为环比/增速配置红绿胶囊 Badge，提升扫描效率。
4. **技术细节下沉折叠（Collapsible Appendix）**：
   - 复杂的 SQL 逻辑、多口径核对表、数据字典使用原生 `<details class="accordion">` 封装，供深入审计时展开，不干扰正文主线阅读。
5. **严禁在 HTML 中输出内部文件路径（No Server Paths）**：
   - **绝对禁止**在 HTML 报告中展示服务器内部路径（如 `/data/...`、容器目录、本地文件路径、脚本文件名等）。用户在浏览器中无法访问服务器本地文件，输出路径不仅无用，还会破坏排版。数据溯源只需陈述数据源表名、业务口径、统计周期与样本量。
6. **完全自包含（Zero Dependency）**：
   - 纯内联 CSS，所有图表转为 Base64，无需外部网络，支持 `<details>` 原生无 JS 展开收起。

---

## 2. 报告标准信息架构（Information Architecture）

一份标准的商业分析报告由以下六大模块构成：

```text
┌─────────────────────────────────────────────────────────────┐
│ 1. 简洁顶部 Header：报告主标题 + 关键元数据胶囊 (窗口/口径/版本)  │
├─────────────────────────────────────────────────────────────┤
│ 2. 核心 KPI 矩阵 (3~4 个 Hero Cards，大字号 + 涨跌对比胶囊)    │
├──────────────────────────────┬──────────────────────────────┤
│ 3. 总体趋势走势图 (Base64)    │ 3. 核心业务判断 (3~4 条关键发现)│
│    (左侧 58% 宽度)           │    (右侧 42% 结构化卡片)     │
├──────────────────────────────┴──────────────────────────────┤
│ 4. 结构与归因矩阵卡片 (分栏：🟢 增长引擎 / 🔴 承压类目 / ⚪ 波动) │
├─────────────────────────────────────────────────────────────┤
│ 5. 重点维度明细数据表 (带份额迷你进度条 + 增长率 Badge)        │
├─────────────────────────────────────────────────────────────┤
│ 6. 深入下钻图表区 (2 列并排图表卡片)                          │
├─────────────────────────────────────────────────────────────┤
│ 7. 可折叠附录 (<details> 封装多口径核对、方法限制与 SQL 溯源)   │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 标准 HTML 报告模板与内联 CSS 系统

生成最终 HTML 时，必须直接使用并遵循以下结构化样式系统：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>分析报告标题</title>
<style>
  :root {
    --bg-page: #f8fafc;
    --bg-card: #ffffff;
    --border: #e2e8f0;
    --border-hover: #cbd5e1;
    --text-main: #0f172a;
    --text-sub: #334155;
    --text-muted: #64748b;
    --text-light: #94a3b8;
    --blue: #2563eb;
    --blue-bg: #eff6ff;
    --blue-border: #bfdbfe;
    --green: #059669;
    --green-bg: #ecfdf5;
    --green-border: #a7f3d0;
    --red: #dc2626;
    --red-bg: #fef2f2;
    --red-border: #fecaca;
    --amber: #d97706;
    --amber-bg: #fffbeb;
    --amber-border: #fde68a;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "WenQuanYi Zen Hei", sans-serif;
    background-color: var(--bg-page);
    color: var(--text-sub);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }

  .container {
    max-width: 1200px;
    margin: 0 auto;
    padding: 32px 24px 80px;
  }

  /* 1. 报告头部 */
  .report-header {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 24px 28px;
    margin-bottom: 24px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
  }

  .header-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 10px;
  }

  .report-title {
    font-size: 24px;
    font-weight: 700;
    color: var(--text-main);
    letter-spacing: -0.02em;
  }

  .meta-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }

  .meta-pill {
    display: inline-flex;
    align-items: center;
    background: #f1f5f9;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 12px;
    font-weight: 500;
    color: var(--text-sub);
  }

  /* 2. 核心 KPI 网格 (严格限制 3~4 个卡片) */
  .hero-kpis {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }

  .kpi-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 18px 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
    position: relative;
  }

  .kpi-card.primary {
    border-top: 3px solid var(--blue);
  }

  .kpi-title {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
    margin-bottom: 6px;
  }

  .kpi-value {
    font-size: 26px;
    font-weight: 700;
    color: var(--text-main);
    font-variant-numeric: tabular-nums;
    line-height: 1.2;
  }

  .kpi-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    margin-top: 8px;
    color: var(--text-muted);
  }

  /* 胶囊标签 */
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 11.5px;
    font-weight: 600;
    padding: 2px 7px;
    border-radius: 4px;
  }
  .badge-pos { background: var(--green-bg); color: var(--green); border: 1px solid var(--green-border); }
  .badge-neg { background: var(--red-bg); color: var(--red); border: 1px solid var(--red-border); }
  .badge-neutral { background: #f1f5f9; color: var(--text-muted); border: 1px solid var(--border); }

  /* 3. 分栏布局 (左右并排) */
  .split-row {
    display: grid;
    grid-template-columns: 1.4fr 1fr;
    gap: 20px;
    margin-bottom: 28px;
    align-items: stretch;
  }

  @media (max-width: 900px) {
    .split-row { grid-template-columns: 1fr; }
  }

  .panel-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
    display: flex;
    flex-direction: column;
  }

  .panel-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--text-main);
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid #f1f5f9;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  /* 结构化结论条目 */
  .insight-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
    list-style: none;
    flex: 1;
  }

  .insight-item {
    background: #f8fafc;
    border: 1px solid var(--border);
    border-left: 3px solid var(--blue);
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 13px;
    line-height: 1.6;
  }

  .insight-item.danger { border-left-color: var(--red); }
  .insight-item.success { border-left-color: var(--green); }
  .insight-item.warning { border-left-color: var(--amber); }

  .insight-item b {
    color: var(--text-main);
    font-weight: 600;
  }

  /* 4. 类目分级卡片阵列 (三列/两列网格) */
  .segment-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 16px;
    margin-bottom: 28px;
  }

  .segment-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 18px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
  }

  .segment-card.growth { border-top: 3px solid var(--green); }
  .segment-card.drop { border-top: 3px solid var(--red); }
  .segment-card.stable { border-top: 3px solid var(--text-muted); }

  .segment-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }

  .segment-title {
    font-size: 14px;
    font-weight: 700;
    color: var(--text-main);
  }

  .segment-body {
    font-size: 13px;
    color: var(--text-sub);
    line-height: 1.6;
  }

  /* 5. 增强数据表格 */
  .table-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
    margin-bottom: 28px;
  }

  .table-header-box {
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .table-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--text-main);
  }

  .table-scroll {
    overflow-x: auto;
    max-height: 480px;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    text-align: left;
  }

  th {
    background: #f8fafc;
    color: var(--text-sub);
    font-weight: 600;
    font-size: 12px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    position: sticky;
    top: 0;
    z-index: 1;
  }

  td {
    padding: 9px 14px;
    border-bottom: 1px solid #f1f5f9;
    color: var(--text-sub);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  tbody tr:hover td {
    background: #f8fafc;
  }

  .num { text-align: right; }
  .center { text-align: center; }

  /* 进度条单元格 */
  .bar-cell {
    display: flex;
    align-items: center;
    gap: 8px;
    justify-content: flex-end;
  }

  .bar-bg {
    width: 64px;
    height: 6px;
    background: #e2e8f0;
    border-radius: 3px;
    overflow: hidden;
  }

  .bar-fill {
    height: 100%;
    background: var(--blue);
    border-radius: 3px;
  }

  /* 6. 可折叠附录 (原生无需 JS) */
  details.accordion {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    margin-bottom: 12px;
    overflow: hidden;
    transition: all 0.2s;
  }

  details.accordion summary {
    padding: 14px 18px;
    font-size: 13.5px;
    font-weight: 600;
    color: var(--text-main);
    cursor: pointer;
    user-select: none;
    background: #f8fafc;
    border-bottom: 1px solid transparent;
  }

  details.accordion[open] summary {
    border-bottom-color: var(--border);
    background: var(--bg-card);
  }

  details.accordion .content {
    padding: 16px 18px;
    font-size: 13px;
    line-height: 1.6;
    color: var(--text-sub);
  }

  /* 页脚 */
  .report-footer {
    margin-top: 48px;
    border-top: 1px solid var(--border);
    padding-top: 16px;
    font-size: 12px;
    color: var(--text-muted);
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
  }
</style>
</head>
<body>
<div class="container">
  <!-- 1. 顶部 Header -->
  <header class="report-header">
    <div class="header-top">
      <h1 class="report-title">最近 30 天一级类目 GMV 增长走势分析</h1>
      <span class="badge badge-neutral">v2.0 商业分析报告</span>
    </div>
    <div class="meta-pills">
      <span class="meta-pill">时间窗口：2026-08-03 ~ 2026-09-01</span>
      <span class="meta-pill">统计口径：全量支付成功 GMV</span>
      <span class="meta-pill">分析样本：3,845 笔分摊明细</span>
      <span class="meta-pill">数据主题：电商交易与类目大盘</span>
    </div>
  </header>

  <!-- 2. 核心 KPI 矩阵 (只放 3~4 个关键指标) -->
  <section class="hero-kpis">
    <div class="kpi-card primary">
      <div class="kpi-title">30 天支付 GMV 总额</div>
      <div class="kpi-value">¥5,390,341.56</div>
      <div class="kpi-meta">日均 ¥179,678.05 · CV 48%</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">区间环比 (前15天 vs 后15天)</div>
      <div class="kpi-value">-1.51%</div>
      <div class="kpi-meta"><span class="badge badge-neutral">持平高位震荡</span></div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">稳健窗口环比 (剔除首末日)</div>
      <div class="kpi-value">+11.89%</div>
      <div class="kpi-meta"><span class="badge badge-pos">核心期稳步走强</span></div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">类目增长结构分布</div>
      <div class="kpi-value">3 增长 · 2 下滑</div>
      <div class="kpi-meta">其余 4 类目平稳/高波动</div>
    </div>
  </section>

  <!-- 3. 大盘走势与关键发现分栏 (Split View) -->
  <section class="split-row">
    <!-- 左侧：走势图 -->
    <div class="panel-card">
      <div class="panel-title">
        <span>大盘日度 GMV 走势与 7 日移动均线</span>
        <span class="badge badge-neutral">日度聚合</span>
      </div>
      <img src="data:image/png;base64,..." alt="走势图" style="width:100%; border-radius:6px;" />
    </div>

    <!-- 右侧：核心业务洞察 -->
    <div class="panel-card">
      <div class="panel-title">核心业务判断与关键发现</div>
      <ul class="insight-list">
        <li class="insight-item success">
          <b>大盘保持高位震荡</b>：日均 17.97 万元，前后半段等长区间环比微降 1.51%，剔除端点日后稳健窗口环比上升 11.89%。
        </li>
        <li class="insight-item success">
          <b>头部类目引领绝对增量</b>：手机数码贡献主要增量（环比 +25.0%，份额 35.83%），内部呈现由微单向摄像机结构替换。
        </li>
        <li class="insight-item danger">
          <b>运动户外与母婴承压回落</b>：运动户外受前期大额日透支环比 -53.9%；母婴玩具后半程日均持续走低（-20.3%）。
        </li>
      </ul>
    </div>
  </section>

  <!-- 4. 类目分级卡片矩阵 (Segment Grid) -->
  <section class="segment-grid">
    <div class="segment-card growth">
      <div class="segment-header">
        <span class="segment-title">🟢 增长驱动引擎</span>
        <span class="badge badge-pos">+25.0% 环比</span>
      </div>
      <div class="segment-body">
        <b>手机数码</b>（总额 193.1 万 / 份额 35.8%）：增量主要来自手机通讯（+11.7 万）与摄影摄像（+9.3 万）。<br/>
        <b>低基数高增长</b>：汽车用品（+49.0%）、宠物生活（+49.5%），规模合计占 2.55%。
      </div>
    </div>

    <div class="segment-card drop">
      <div class="segment-header">
        <span class="segment-title">🔴 承压与下滑类目</span>
        <span class="badge badge-neg">回落明显</span>
      </div>
      <div class="segment-body">
        <b>运动户外</b>（-53.9%）：08-17 单日 7.2 万脉冲后持续回落。<br/>
        <b>母婴玩具</b>（-20.3%）：末段 6 日均值（2,125 元）显著弱于前段均值（5,791 元）。
      </div>
    </div>

    <div class="segment-card stable">
      <div class="segment-header">
        <span class="segment-title">⚪ 高波动与结构切换</span>
        <span class="badge badge-neutral">品类重组</span>
      </div>
      <div class="segment-body">
        <b>电脑办公</b>：存在大额日脉冲与 4 个零交易日，整机上升 vs DIY 硬件回落。<br/>
        <b>家居家装</b>：床垫与桌类增长，灯具与家纺回落。
      </div>
    </div>
  </section>

  <!-- 5. 重点维度明细数据表 (带份额 Mini-Bar 与增长率 Badge) -->
  <section class="table-card">
    <div class="table-header-box">
      <div class="table-title">一级类目 GMV 规模与增长分级总览</div>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>类目名称</th>
            <th class="num">30天 GMV (元)</th>
            <th class="num">份额占比</th>
            <th class="num">前半段 (H1)</th>
            <th class="num">后半段 (H2)</th>
            <th class="center">区间环比</th>
            <th class="center">增长定级</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><b>手机数码</b></td>
            <td class="num">¥1,931,274.22</td>
            <td>
              <div class="bar-cell">
                <div class="bar-bg"><div class="bar-fill" style="width: 35.8%;"></div></div>
                <span>35.8%</span>
              </div>
            </td>
            <td class="num">¥858,359.20</td>
            <td class="num">¥1,072,915.02</td>
            <td class="center"><span class="badge badge-pos">+25.0%</span></td>
            <td class="center"><span class="badge badge-pos">增长类目</span></td>
          </tr>
          <!-- 其他类目行 -->
        </tbody>
      </table>
    </div>
  </section>

  <!-- 6. 技术附录与口径溯源 (使用原生 details 折叠，严禁出现服务器内部文件路径) -->
  <section>
    <details class="accordion">
      <summary>📌 数据口径定义与多口径对照表（点击展开）</summary>
      <div class="content">
        <p>下单 GMV 563.95 万，排除取消后有效下单 GMV 539.26 万，最终支付成功 GMV 539.03 万，退款打款 36.87 万...</p>
      </div>
    </details>

    <details class="accordion">
      <summary>📌 数据来源与分析维度说明（点击展开）</summary>
      <div class="content">
        <p>数据源：订单交易明细宽表（dws_order_item_df）；分析维度：一级类目（L1 Category）、日度时间序列；聚合方式：按支付时间归属日聚合...</p>
      </div>
    </details>
  </section>

  <!-- 7. 页脚 -->
  <footer class="report-footer">
    <span>DataAgent Analyst 自动生成 · 遵循商业分析报告规范</span>
    <span>数据校验：Σ 校验 100% 通过</span>
  </footer>
</div>
</body>
</html>
```

---

## 4. 图表高清现代美化参数 (Matplotlib / Seaborn)

绘制图表时，必须通过 Python 设置统一的高清现代商务图表风格：

```python
import matplotlib.pyplot as plt

plt.rcParams['figure.dpi'] = 200
plt.rcParams['font.family'] = 'WenQuanYi Zen Hei'
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.size'] = 10

# 极简配色
PALETTE = {
    'primary': '#2563eb',    # 皇家蓝 (主趋势)
    'success': '#059669',    # 翡翠绿 (正增长)
    'danger': '#dc2626',     # 玫瑰红 (下滑)
    'warning': '#d97706',    # 琥珀金 (预警)
    'slate': '#64748b',      # 中性灰
    'grid': '#f1f5f9',       # 极淡网格
    'bg': '#ffffff',
}

def setup_clean_chart(ax, title=None):
    ax.set_facecolor('#ffffff')
    ax.figure.patch.set_facecolor('#ffffff')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#cbd5e1')
    ax.spines['bottom'].set_color('#cbd5e1')
    ax.yaxis.grid(True, linestyle='--', alpha=0.6, color=PALETTE['grid'])
    ax.xaxis.grid(False)
    if title:
        ax.set_title(title, fontsize=13, weight='bold', color='#0f172a', pad=12)
    ax.tick_params(colors='#475569', labelsize=9.5)
```

---

## 5. 生成报告自检准则 (Quality Checklist)

在保存交付前，对照以下项进行自查：
1. [ ] **无视觉过载**：顶部 Hero KPI 严格控制在 3~4 个以内，没有 8~10 个指标平铺。
2. [ ] **分栏图文对应**：大盘走势图采用 6:4 分栏并排展示，右侧为精炼结论。
3. [ ] **分类结构清晰**：增长/下滑类目采用分栏卡片（绿色/红色/灰色）组织，而非单一长段落。
4. [ ] **表格带视觉辅助**：主要表格包含占比 mini-bar 或涨跌幅 Badge，表头置顶。
5. [ ] **技术细节折叠**：指标定义、口径对照放入 `<details>` 折叠，保持主干清爽。
6. [ ] **严禁内部文件路径**：HTML 中绝对不出现服务器/容器内部路径（如 `/data/...`、`.parquet/.csv` 本地磁盘路径、`scripts/...` 脚本路径等），溯源仅呈现业务表名、指标与口径。
7. [ ] **完全自包含**：所有图表 Base64 内嵌，离线双击即可完美呈现。
