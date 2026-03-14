---
name: insight
description: 当任务涉及业务数据查询、数据清洗、统计分析、活动复盘、营销分析、用户画像分析、商品/优惠/地域表现分析、pandas 分析，或需要在会话工作区中编写 Python 数据处理脚本并最终输出结构化报告或 HTML 分析页面时使用此技能。优先通过 `db_query` 工具查询数据库；查询结果会保存到当前会话工作区，可继续用 pandas 读取、分析、汇总、导出，并生成 HTML 报告。工作区 Python 环境使用 uv 管理，执行脚本、单文件命令或安装依赖时都要使用 `uv run` 或 `uv add`，不要直接调用 `python`。
---

# 数据分析技能

这个技能适合两类任务：

- 基础数据处理：查数、清洗、聚合、透视、导出
- 业务分析交付：活动复盘、用户分层、商品表现、营销建议、生成 HTML 报告

当用户明确希望“多维度分析”“更完整洞察”“最终输出成品页面”时，不要只返回一段文字结论，应该产出可复用的数据文件和 HTML 文件。

## 快速参考

| 任务                 | 做法                                                          |
| -------------------- | ------------------------------------------------------------- |
| 查询业务数据库       | 调用 `db_query` 工具                                          |
| 读取查询结果文件     | 优先使用 pandas                                               |
| 执行 Python 脚本     | 使用 `uv run script.py`                                       |
| 执行单条 Python 命令 | 使用 `uv run python -c "..."`                                 |
| 安装分析依赖         | 使用 `uv add 包名`                                            |
| 生成 HTML 报告       | 优先输出结构化 JSON/CSV，再用 `scripts/render_report.py` 渲染 |
| 工作区内落分析结果   | 写入当前会话工作区，避免写到工作区外                          |

## 标准工作流

1. 先明确分析对象：活动、用户、商品、优惠、地域、渠道、时段，分别对应什么业务问题。
2. 需要数据库数据时调用 `db_query`，不要手写数据库连接或绕过现有工具。
3. 读取 `db_query` 返回的 `file_path`、`fields`、`preview_rows`，确认结果结构。
4. 在工作区中用 pandas 做字段检查、清洗、聚合、透视、分层。
5. 不只停留在单一维度统计，应至少补足关键对比维度与业务解释。
6. 先产出结构化结果文件，再生成最终结论和 HTML 报告。
7. 回复用户时明确给出：
   - 原始查询结果文件
   - 中间分析文件
   - 最终 HTML 文件
   - 关键洞察与建议

## 多维度分析要求

如果用户要做活动分析、营销复盘、618/双11等大促建议、粉丝活动复盘、经营分析，不要只做单张统计表。默认从下面维度中选择合适的项，至少覆盖 4 个以上维度：

- 用户画像：新老客、年龄、性别、会员等级、购买力、城市等级
- 用户行为：下单人数、订单数、件单价、客单价、连带率、复购率、转化率
- 地域表现：省份、城市、区域、城市等级、高潜区域
- 优惠表现：券类型、满减/折扣/赠品偏好、不同优惠对转化和客单价的影响
- 商品表现：品类、品牌、SPU/SKU、销量、销售额、转化、连带购买
- 时间表现：按天、小时、活动阶段、预热期/爆发期/返场期
- 渠道表现：来源渠道、投放渠道、私域/公域、直播/短视频/搜索
- 人群交叉：例如“新客 x 地域”“会员等级 x 商品品类”“优惠形式 x 客单价”
- 异常与机会：占比异常、转化异常、高潜未转化、高曝光低成交

如果数据不足以覆盖全部维度，应明确说明缺失字段，并优先完成最关键的维度分析。

营销活动类任务建议优先参考 [references/marketing_framework.md](./references/marketing_framework.md)。

## 洞察输出要求

分析结果不要只给“数据描述”，还要给“业务解释”和“可执行建议”：

- 数据描述：发生了什么
- 业务解释：为什么可能这样
- 影响判断：对增长、转化、客单价、复购有什么影响
- 行动建议：下一步怎么做

建议写法：

- 先写一句核心结论
- 再补关键数字
- 再补业务动作建议

例如：

- 老客贡献占比高达 75.14%，说明本次活动更偏向已有用户激活而非拉新。建议在下一轮活动中增加新客专属券、首单礼或站外引流素材，提升新客参与占比。

## db_query 工具

`db_query` 用于把自然语言查询发送给 data-agent。

工具返回重点关注这些字段：

- `status`: 是否成功
- `file_path`: 查询结果已保存到当前会话工作区的文件路径
- `file_format`: 结果文件格式，通常是 `csv` 或 `json`
- `pandas_read_hint`: 推荐的 pandas 读取方式
- `fields`: 结果字段名
- `preview_rows`: 前几行样例数据
- `row_count`: 结果行数，表格结果时提供

使用约定：

- 只把 `db_query` 当作数据库入口
- 如果 `status` 为 `error`，先基于错误信息调整查询，再决定是否重试
- 拿到结果后，优先基于工作区中的文件继续分析，而不是重复查询

## pandas 处理

常见读取方式：

```bash
uv run python -c "import pandas as pd; df = pd.read_csv('/abs/path/result.csv'); print(df.head())"
```

```bash
uv run python -c "import pandas as pd; df = pd.read_json('/abs/path/result.json'); print(df.head())"
```

建议：

- 表格结果优先读成 DataFrame 再处理
- 先看 `df.columns`、`df.dtypes`、`df.head()`，确认字段含义
- 做分组前先统一空值、类型、时间字段和枚举值
- 尽量产出可复用的中间表，例如：
  - `analysis/user_profile_summary.csv`
  - `analysis/region_summary.csv`
  - `analysis/promotion_preference.csv`
  - `analysis/product_performance.csv`
  - `analysis/report_payload.json`

## HTML 报告交付

如果用户希望“详细展示分析结果”“导出报告”“生成页面”，默认要生成 HTML 文件，而不是只在对话里贴结论。

推荐目录：

- `db_query_results/`: 原始查询结果
- `analysis/`: 中间结果和结构化汇总
- `outputs/`: 最终 HTML 报告

推荐 HTML 报告结构：

1. 报告标题区：活动名称、分析周期、数据范围、生成时间
2. 核心结论区：3 到 6 条摘要洞察
3. 指标卡片区：用户数、订单数、销售额、客单价、转化率等
4. 多维分析区：用户画像、地域、优惠、商品、时间/渠道等
5. 营销建议区：按优先级给出建议
6. 附录区：口径说明、字段说明、数据文件路径

在没有专业图表库的情况下，也要至少保证：

- 指标卡片清晰
- 表格结构完整
- 重点对比项有视觉层次
- 页面可直接在浏览器打开

## HTML 生成脚本

技能自带 `scripts/render_report.py`，用于把结构化 JSON 渲染成自包含 HTML 页面。

建议流程：

1. 先把分析结果整理成 JSON
2. 再执行：

```bash
uv run /home/kodey/agents/insight-agent/.deepagents/skills/insight/scripts/render_report.py \
  --input analysis/report_payload.json \
  --output outputs/marketing_report.html
```

支持的 JSON 结构见脚本头部说明。最少应包含：

- `title`
- `subtitle`
- `summary`
- `metrics`
- `sections`
- `recommendations`

如果任务是活动复盘，建议至少包含这些 section：

- 用户画像分析
- 地域表现分析
- 优惠形式偏好
- 商品表现分析
- 618 大促营销建议

## Python 执行规则

工作区 Python 环境由 `uv` 管理，默认规则如下：

- 不要直接运行 `python script.py`
- 不要直接运行 `pip install`
- 要用 `uv run script.py`
- 要用 `uv run python -c "..."` 执行单条 Python
- 要用 `uv add pandas openpyxl ...` 安装新增依赖

正确示例：

```bash
uv run analyze_sales.py
```

```bash
uv run python -c "import pandas as pd; df = pd.read_csv('db_query_results/result.csv'); print(df.describe())"
```

```bash
uv add seaborn
```

错误示例：

```bash
python analyze_sales.py
```

```bash
pip install seaborn
```

## 工作区约束

- 所有分析脚本、查询结果、清洗结果、导出文件都应保存在当前会话工作区
- 优先使用清晰目录名，例如 `db_query_results/`、`analysis/`、`outputs/`
- 不要把分析产物写到工作区外
- 回复用户时，如果生成了文件，明确给出工作区内文件路径和用途

## 输出要求

- 如果只是回答结论，给出结论同时引用关键字段和样例数据来源
- 如果做了进一步处理，说明读取了哪个结果文件、产出了哪些新文件
- 如果用户要交付件，不要只给文本，优先保留结构化文件和 HTML
- HTML 报告要能脱离对话单独阅读，至少包含摘要、分维度洞察、建议和数据口径
