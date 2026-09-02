---
name: visualization
description: 将 Analyst 已验证的分析结果制作成专业、美观、现代的高管级自包含 HTML 报告与图表。用于最终可视化、综合报告与展示交付；不用于底层取数或改变数据分析口径。
---

# 数据可视化与专业 HTML 报告规范

## 1. 核心目标与交付标准

将 Analyst 验证过的指标、证据表和分析结论，转化为具备**咨询公司/专业 BI 级视觉品质**、结构严谨、排版现代、完全自包含（离线可用）的 HTML 综合分析报告与高清图表。

### 关键交付原则：
1. **完全自包含（Zero External Dependency）**：
   - 样式一律采用内联 `<style>`。
   - 图表一律转换为 `data:image/png;base64,...` 或 SVG 嵌入 HTML。
   - 严禁引用外部 CDN（如 Google Fonts、外部 CSS/JS、图床图片），严禁使用 `<script>` 标签。
2. **专业现代审美（Executive-Grade Visual Standards）**：
   - 拒绝 90 年代老旧表格与粗糙饱和色块；采用现代 Slate/Neutral 灰阶底色、柔和圆角、精致微边框、分层阴影与专业配色。
   - 所有数值统一使用等宽数字（`tabular-nums`），带千分位与合理小数位。
3. **证据与图文严格对应**：
   - 报告中所有数值、图表数据标签与证据表 100% 吻合，具备完整的数据来源与口径追溯说明。

---

## 2. 现代 HTML 报告设计规范与样式模板

在生成最终 HTML 报告时，必须直接内嵌并遵循以下 CSS 样式系统：

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
    --border-color: #e2e8f0;
    --border-hover: #cbd5e1;
    --text-primary: #0f172a;
    --text-secondary: #334155;
    --text-muted: #64748b;
    --text-light: #94a3b8;
    --accent-blue: #2563eb;
    --accent-blue-bg: #eff6ff;
    --accent-blue-border: #bfdbfe;
    --accent-green: #059669;
    --accent-green-bg: #ecfdf5;
    --accent-green-border: #a7f3d0;
    --accent-red: #dc2626;
    --accent-red-bg: #fef2f2;
    --accent-red-border: #fecaca;
    --accent-amber: #d97706;
    --accent-amber-bg: #fffbeb;
    --accent-amber-border: #fde68a;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "WenQuanYi Zen Hei", sans-serif;
    background-color: var(--bg-page);
    color: var(--text-secondary);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }

  .container {
    max-width: 1160px;
    margin: 0 auto;
    padding: 36px 24px 80px;
  }

  /* 报告主头部 */
  .report-header {
    margin-bottom: 28px;
    border-bottom: 1px solid var(--border-color);
    padding-bottom: 20px;
  }

  .report-title {
    font-size: 26px;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.02em;
    margin-bottom: 12px;
  }

  .meta-tags {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--text-muted);
  }

  .meta-tag {
    display: inline-flex;
    align-items: center;
    background: #f1f5f9;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 12px;
    font-weight: 500;
    color: var(--text-secondary);
  }

  /* 导航药丸条 */
  .nav-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 20px 0;
  }

  .nav-pill {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 20px;
    padding: 5px 14px;
    font-size: 13px;
    color: var(--text-secondary);
    text-decoration: none;
    font-weight: 500;
    transition: all 0.15s ease;
  }

  .nav-pill:hover {
    background: #f1f5f9;
    border-color: var(--border-hover);
    color: var(--text-primary);
  }

  /* 章节标题 */
  h2.section-title {
    font-size: 18px;
    font-weight: 700;
    color: var(--text-primary);
    margin: 36px 0 16px;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  h2.section-title::before {
    content: "";
    display: inline-block;
    width: 4px;
    height: 18px;
    background: var(--accent-blue);
    border-radius: 2px;
  }

  h3.subsection-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
    margin: 20px 0 10px;
  }

  /* KPI 指标卡片网格 */
  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 14px;
    margin: 18px 0;
  }

  .kpi-card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 14px 16px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.03);
  }

  .kpi-label {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
    margin-bottom: 4px;
  }

  .kpi-value {
    font-size: 22px;
    font-weight: 700;
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
    line-height: 1.2;
  }

  .kpi-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 11.5px;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 4px;
    margin-top: 6px;
  }

  .badge-pos { background: var(--accent-green-bg); color: var(--accent-green); border: 1px solid var(--accent-green-border); }
  .badge-neg { background: var(--accent-red-bg); color: var(--accent-red); border: 1px solid var(--accent-red-border); }
  .badge-neutral { background: #f1f5f9; color: var(--text-muted); border: 1px solid var(--border-color); }

  /* 提示框与核心结论 Banner */
  .callout {
    border-radius: 8px;
    padding: 14px 18px;
    margin: 16px 0;
    font-size: 14px;
    line-height: 1.6;
  }

  .callout-info {
    background: var(--accent-blue-bg);
    border-left: 4px solid var(--accent-blue);
    color: #1e3a8a;
  }

  .callout-warn {
    background: var(--accent-amber-bg);
    border-left: 4px solid var(--accent-amber);
    color: #78350f;
  }

  .callout-success {
    background: var(--accent-green-bg);
    border-left: 4px solid var(--accent-green);
    color: #064e3b;
  }

  .callout-title {
    font-weight: 700;
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  /* 发现清单 */
  .findings-list {
    list-style: none;
    margin: 14px 0;
    display: flex;
    flex-col;
    gap: 10px;
  }

  .findings-list li {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 13.5px;
    line-height: 1.6;
    box-shadow: 0 1px 2px rgba(0,0,0,0.02);
  }

  .findings-list li b {
    color: var(--text-primary);
  }

  /* 数据表格 */
  .table-card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
    margin: 16px 0;
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
    color: var(--text-secondary);
    font-weight: 600;
    font-size: 12px;
    padding: 10px 14px;
    border-bottom: 2px solid var(--border-color);
    white-space: nowrap;
    position: sticky;
    top: 0;
    z-index: 1;
  }

  td {
    padding: 9px 14px;
    border-bottom: 1px solid #f1f5f9;
    color: var(--text-secondary);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  tbody tr:hover td {
    background: #f8fafc;
  }

  .num { text-align: right; }
  .center { text-align: center; }

  /* 图表卡片 */
  .figure-card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 16px;
    margin: 18px 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
  }

  .figure-header {
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid #f1f5f9;
  }

  .figure-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
  }

  .figure-desc {
    font-size: 12.5px;
    color: var(--text-muted);
    margin-top: 2px;
  }

  .figure-card img {
    width: 100%;
    height: auto;
    display: block;
    border-radius: 6px;
  }

  .figure-footer {
    font-size: 12px;
    color: var(--text-muted);
    margin-top: 8px;
    padding-top: 6px;
    border-top: 1px solid #f8fafc;
  }

  /* 页脚 */
  .report-footer {
    margin-top: 48px;
    border-top: 1px solid var(--border-color);
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
  <!-- 报告头部 -->
  <header class="report-header">
    <h1 class="report-title">分析报告主标题</h1>
    <div class="meta-tags">
      <span class="meta-tag">窗口：2026-08-03 ~ 2026-09-01</span>
      <span class="meta-tag">主口径：支付成功 GMV</span>
      <span class="meta-tag">版本：v1.1</span>
    </div>
    <!-- 导航药丸 -->
    <nav class="nav-pills">
      <a href="#s1" class="nav-pill">1 执行摘要</a>
      <a href="#s2" class="nav-pill">2 指标口径</a>
      <a href="#s3" class="nav-pill">3 趋势分析</a>
      <a href="#s4" class="nav-pill">4 结构归因</a>
      <a href="#s5" class="nav-pill">5 限制与来源</a>
    </nav>
  </header>

  <!-- 核心结论 Banner -->
  <div class="callout callout-info">
    <div class="callout-title">核心业务判断</div>
    <p>此处清晰阐述分析得出的核心结论，避免冗长描述，突出关键变动方向与核心贡献项。</p>
  </div>

  <!-- KPI 卡片区 -->
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-label">30天 GMV 总额</div>
      <div class="kpi-value">¥5,390,341.56</div>
      <span class="kpi-badge badge-neutral">日均 ¥179.68k</span>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">前半 vs 后半 环比</div>
      <div class="kpi-value">-1.51%</div>
      <span class="kpi-badge badge-neg">微幅波动</span>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">稳健窗口环比 (去端点)</div>
      <div class="kpi-value">+11.89%</div>
      <span class="kpi-badge badge-pos">上行趋势</span>
    </div>
  </div>

  <!-- 章节内容与表格、图表示例 -->
  <section id="s1">
    <h2 class="section-title">1 执行摘要</h2>
    <ul class="findings-list">
      <li><b>头部引领增长</b>：手机数码贡献主要增量，区间环比 +25.0%，份额占比 35.83%。</li>
      <li><b>低基数高弹性</b>：汽车用品（+49.0%）与宠物生活（+49.5%）增速领先，但绝对规模尚小（合计 2.55%）。</li>
    </ul>
  </section>

  <!-- 页脚 -->
  <footer class="report-footer">
    <span>DataAgent Analyst 自动生成</span>
    <span>数据来源：已验证数据产物</span>
  </footer>
</div>
</body>
</html>
```

---

## 3. Python 图表绘制专业美化规范 (Matplotlib / Seaborn)

绘制图表时，必须通过 Python 设置统一的高清现代商务图表风格。

### 3.1 全局参数配置模板
```python
import matplotlib.pyplot as plt
import seaborn as sns

# 1. 设置高分辨率与中文字体
plt.rcParams['figure.dpi'] = 200
plt.rcParams['font.family'] = 'WenQuanYi Zen Hei'
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.size'] = 10
plt.rcParams['axes.titlesize'] = 13
plt.rcParams['axes.labelsize'] = 11
plt.rcParams['xtick.labelsize'] = 9.5
plt.rcParams['ytick.labelsize'] = 9.5

# 2. 现代极简色彩板
PALETTE = {
    'primary': '#2563eb',    # 皇家蓝 (主趋势/主条形)
    'success': '#059669',    # 翡翠绿 (正增长/目标达成)
    'danger': '#dc2626',     # 玫瑰红 (下滑/异常)
    'warning': '#d97706',    # 琥珀金 (预警/关注)
    'purple': '#7c3aed',     # 紫罗兰 (次要维度)
    'slate': '#64748b',      # 中性灰 (基线/对比期)
    'grid': '#e2e8f0',       # 极浅网格线
    'bg': '#ffffff',         # 纯白画布
}

# 3. 统一美化函数
def apply_chart_style(ax, title=None, xlabel=None, ylabel=None):
    ax.set_facecolor(PALETTE['bg'])
    ax.figure.patch.set_facecolor(PALETTE['bg'])
    
    # 去除顶部与右侧边框
    for spine in ['top', 'right']:
        ax.spines[spine].set_visible(False)
    for spine in ['left', 'bottom']:
        ax.spines[spine].set_color('#cbd5e1')
        ax.spines[spine].set_linewidth(0.8)
    
    # 仅保留水平虚线辅助网格
    ax.yaxis.grid(True, linestyle='--', alpha=0.5, color=PALETTE['grid'])
    ax.xaxis.grid(False)
    
    if title:
        ax.set_title(title, weight='bold', color='#0f172a', pad=12)
    if xlabel:
        ax.set_xlabel(xlabel, color='#475569', labelpad=8)
    if ylabel:
        ax.set_ylabel(ylabel, color='#475569', labelpad=8)
    
    ax.tick_params(colors='#475569', width=0.8)
```

### 3.2 常见图表美化要点
- **折线趋势图**：
  - 核心线条线宽设为 `2.2`，配合平滑标记点 `markersize=4.5`。
  - 使用半透明阴影填充（`fill_between(..., alpha=0.1)`）增强立体感。
  - 关键拐点或峰值使用 `ax.annotate` 添加箭头和数值标注。
- **条形/柱状图**：
  - 柱子宽度 `width=0.55 ~ 0.65`，边框 `edgecolor='none'`。
  - 正负增长采用双色区分（绿色正、红色负）。
  - 在柱顶直接标注格式化数字（如 `¥12.5k` 或 `+15.2%`），避免读者来回对比 Y 轴。
- **结构对比（堆叠/面积/饼图）**：
  - 类别尽量控制在 5~7 个以内，其余归入“其他”。
  - 颜色使用同色调渐变或协调离散色系，避免高饱和度杂乱撞色。

---

## 4. 自包含 HTML 装配流程代码模板

通过 Python 脚本生成自包含 HTML 报告的推荐工作流：

```python
import base64
import os

def img_to_base64(img_path):
    with open(img_path, "rb") as f:
        return f"data:image/png;base64,{base64.b64encode(f.read()).decode('utf-8')}"

# 1. 绘制各图表并保存为临时 PNG
# plt.savefig('charts/fig1.png', bbox_inches='tight', dpi=200)

# 2. 转换为 base64
# fig1_b64 = img_to_base64('charts/fig1.png')

# 3. 填充进上述标准 HTML 模板中并写入 output
html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
...
  <div class="figure-card">
    <div class="figure-header">
      <div class="figure-title">图 1：日度 GMV 走势与 7 日移动均线</div>
      <div class="figure-desc">整体呈现高位震荡，周期性峰值主要受特定活动与大额日驱动</div>
    </div>
    <img src="{fig1_b64}" alt="日度GMV走势" />
    <div class="figure-footer">数据口径：全量支付成功订单 (paid_gmv_amount)</div>
  </div>
...
</html>"""

with open("report_v1.html", "w", encoding="utf-8") as f:
    f.write(html_content)
```

---

## 5. 完成前检查清单（Checklist）

交付 HTML 报告前，执行以下检查：
- [ ] **离线打开**：无网络连接或离线状态下，双击 HTML 文件所有样式、字体排版、图表均正常渲染。
- [ ] **视觉质感**：无大面积深蓝/粗黑边框、无密集无序排版、KPI 与卡片对齐整齐、阴影与圆角统一。
- [ ] **表格规范**：数字全部右对齐并带千分位，表头固定，无生硬垂直边框。
- [ ] **图表高清**：DPI ≥ 200，坐标轴无截断，中文标签清晰无方块乱码，无多余顶部/右侧黑色包围线。
- [ ] **路径追溯**：报告底部明确标明上游数据产物文件路径及指标计算版本。
