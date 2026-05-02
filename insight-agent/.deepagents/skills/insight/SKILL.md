---
name: insight
description: 当任务涉及归因分析、业务诊断、活动复盘、营销分析、渠道/用户/商品/优惠/地域/漏斗分析，或需要先通过 `db_query` 查询业务数据库、再用 pandas 在工作区完成多维对比分析并最终输出 HTML 报告时使用此技能。
---

# 归因分析技能
## 什么时候进入归因分析模式
出现以下任一情况时，不要只做单点查数：
- 用户问“为什么”
- 用户问“谁贡献了增长/下滑”
- 用户问“活动/渠道/商品/地域/人群表现如何”
- 用户问“帮我复盘”“帮我详细分析”“输出报告”
- 用户只给了一个核心指标，但本质是经营诊断问题

## 核心要求
- 数据库数据只能通过 `db_query` 获取
- 不要停留在单点查数；默认做多维拆解和归因判断
- 查询结果要保存为 pandas 可直接加载的文件
- 中间结果写入 `analysis/`
- 最终输出 `outputs/*.html`
- Python 一律使用 `uv run`，安装依赖一律使用 `uv add`

## 默认工作流
1. 明确分析对象、目标指标、对比口径、归因目标
2. 用 `db_query` 获取基础数据
3. 用 pandas 清洗、聚合、对比、分层
4. 默认补齐关键维度并做交叉分析
5. 形成归因判断、异常点和机会点
6. 输出中间文件、报告 payload 和最终 HTML

## 必须补齐的分析动作
- 基线对比：同比、环比、活动前后或对照组
- 规模拆解：流量、曝光、访问、下单人数
- 结构拆解：渠道、人群、商品、地域、优惠、时间结构
- 效率拆解：转化率、客单价、件单价、复购率
- 贡献拆解：哪些维度拉动增长，哪些维度拖累结果
- 异常识别：高流量低转化、高转化低覆盖、高曝光低成交

如果数据不足，至少完成：
- 规模变化分析
- 结构变化分析
- 关键效率指标分析

## 默认分析维度
默认至少覆盖 4 个维度；活动复盘、经营诊断、增长分析建议覆盖 6 个以上。
- 用户：新老客、会员等级、生命周期、购买力
- 渠道：自然/付费、私域/公域、搜索/直播/短视频
- 商品：品类、品牌、SPU、SKU、价格带、爆款/长尾
- 优惠：券类型、折扣力度、满减门槛、补贴
- 地域：省份、城市、区域、城市等级
- 时间：天、周、小时、活动阶段
- 行为：曝光、点击、访问、加购、下单、支付、复购

常用交叉维度：
- 新老客 × 渠道
- 新老客 × 优惠
- 渠道 × 商品
- 地域 × 商品
- 活动阶段 × 渠道表现

## 归因结论的写法
不要只写“谁最高”“谁最低”，要明确：
- 指标变了什么，幅度多少
- 变化主要来自哪些维度
- 哪个因素是主因，哪些只是表象
- 对业务动作意味着什么

每个重要结论尽量包含：
1. 一句话结论
2. 关键数字
3. 归因判断
4. 业务建议

## 数据与文件产物
推荐目录：
- `db_query_results/`: 原始查询结果
- `analysis/`: 清洗结果、汇总表、归因拆解表、图表数据
- `outputs/`: HTML 报告和其他交付文件

推荐中间文件：
- `analysis/base_cleaned.csv`
- `analysis/metric_summary.csv`
- `analysis/channel_contribution.csv`
- `analysis/user_segment_summary.csv`
- `analysis/product_mix_summary.csv`
- `analysis/region_summary.csv`
- `analysis/time_trend.csv`
- `analysis/chart_data.json`
- `analysis/report_payload.json`

## pandas 与 uv 规则
优先从 `db_query` 返回的文件继续分析，不要重复查库。

正确示例：
```bash
uv run python -c "import pandas as pd; df = pd.read_csv('/abs/path/result.csv'); print(df.head())"
```

```bash
uv run python -c "import pandas as pd; df = pd.read_json('/abs/path/result.json'); print(df.head())"
```

```bash
uv add seaborn
```

禁止：
- `python script.py`
- `python -c "..."`
- `pip install ...`

## HTML 报告要求
如果用户要详细分析、汇报页、可分享结果，默认输出 HTML，而不是只在对话里贴结论。

HTML 至少应包含：
- 标题区：主题、时间范围、数据口径、生成时间
- 核心摘要区：3 到 6 条核心发现
- 指标卡片区：核心指标与对比值
- 归因总览区：增长/下滑由哪些因素驱动
- 多维拆解区：用户、渠道、商品、优惠、地域、时间
- 异常与机会区
- 行动建议区
- 附录区：口径说明、数据文件路径、限制说明

## 图表要求
归因分析不要只输出表格和文字。只要数据具备可视化价值，默认应生成图表并放入 HTML。

优先出图场景：
- 时间趋势
- 维度 TopN 对比
- 结构变化
- 归因贡献拆解
- 漏斗变化

默认优先图表方案：
- 时间趋势：`echarts` 折线图
- 维度对比：`echarts` 柱状图
- 贡献拆解：`echarts` 柱状图或瀑布图
- 指标总览：`metrics`
- 结论摘要：`cards`
- 明细口径：`table`

图表原则：
- 一张图只表达一个核心问题
- 标题写清楚指标、维度、时间范围
- 图下要有一句解释
- 优先展示最支持结论的 1 到 3 张图
- 默认优先使用 `echarts` block，把图表 `option` 直接写入 HTML 渲染
- 只有在不适合用 `echarts` 或需要快速兜底时，再使用 `line-chart`、`bar-chart`

## HTML 渲染脚本
使用 [scripts/render_report.py](./scripts/render_report.py) 把 `analysis/report_payload.json` 渲染为 HTML：
```bash
uv run python /home/kodey/agents/insight-agent/.deepagents/skills/insight/scripts/render_report.py \
  --input analysis/report_payload.json \
  --output outputs/insight_report.html
```

payload 顶层字段：
- `meta`
- `blocks`

支持的 block 类型：
- `section`
- `prose`
- `list`
- `metrics`
- `cards`
- `table`
- `echarts`
- `bar-chart`
- `line-chart`
- `ranking`
- `callout`
- `columns`

常用字段：
- `title`
- `summary`
- `items`
- `option`
- `series`
- `columns`
- `rows`
- `blocks`

`echarts` block 示例：
```json
{
  "type": "echarts",
  "title": "渠道 GMV 对比",
  "height": 360,
  "option": {
    "tooltip": {"trigger": "axis"},
    "legend": {"data": ["2024", "2023"]},
    "xAxis": {"type": "category", "data": ["私域", "搜索", "直播"]},
    "yAxis": {"type": "value"},
    "series": [
      {"name": "2024", "type": "bar", "data": [520, 330, 260]},
      {"name": "2023", "type": "bar", "data": [480, 360, 210]}
    ]
  }
}
```

## 最终回复用户时必须包含
- 分析的业务问题
- 使用了哪些数据文件
- 补充了哪些维度分析
- 核心归因结论
- 生成了哪些文件
- 最终 HTML 文件路径

如果数据不足以完成完整归因，要明确说明缺失了哪些字段、当前结论有哪些局限、下一步还需要补什么数据。
