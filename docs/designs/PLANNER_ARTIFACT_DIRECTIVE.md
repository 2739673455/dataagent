# Planner 最终产物交付指令

## 1. 目标

Planner 在最终回答中显式选择需要交付给用户的文件。专业 Agent 可以生成多份数据、脚本、审查记录和可视化产物，最终消息只附带对用户有直接价值的文件，避免自动收集并返回全部文件。

该能力复用现有消息附件和下载接口，不新增 `artifact_id`、数据库表或文件索引。

## 2. 指令格式

Planner 使用以下独占一行的指令引用一个沙箱文件：

```text
[[DATAAGENT_ARTIFACT:/sessions/{analysis_id}/{agent_type}/{session_id}/{relative_path}]]
```

示例：

```text
分析报告和趋势图已经生成：

[[DATAAGENT_ARTIFACT:/sessions/gmv_category_trend_30d/visualizer/visualize_gmv_growth/report.html]]
[[DATAAGENT_ARTIFACT:/sessions/gmv_category_trend_30d/visualizer/visualize_gmv_growth/charts/overall_trend.png]]
```

格式规则：

- 固定前缀为 `[[DATAAGENT_ARTIFACT:`，固定后缀为 `]]`。
- 中间内容必须是以 `/sessions/` 开头的规范化沙箱绝对路径。
- 每个文件使用一条独立指令；附件顺序与指令出现顺序一致。
- 指令必须独占一行，行首允许不超过三个空格，行尾允许空白字符。
- Markdown 行内代码、代码块、表格单元格和普通文本中的同类字符串不解析。
- 普通 `/sessions/...` 路径不自动转换为附件。

## 3. 可交付文件范围

指令中的路径属于模型输出，默认视为不可信输入。文件无需预先出现在 delegation 结果的 `artifacts` 中，只要同时满足以下条件即可交付：

- 路径是以 `/sessions/` 开头的规范化沙箱绝对路径；
- 路径解析后属于当前 Conversation 的工作区；
- 文件当前存在且是普通文件；
- 文件满足现有下载接口的文件大小、所有权和访问限制。

因此 Planner 可以交付专业 Agent 在 Session 中生成但未写入结构化 `artifacts` 的文件。普通文本中的路径和工具输出中偶然出现的路径仍不会自动转成附件，只有最终回答中的显式指令会触发解析。

模型自行编造的路径只有在恰好指向当前 Conversation 中真实、可下载的文件时才能生成附件，不能据此读取其他 Conversation 或沙箱外部的文件。

## 4. 后端解析流程

```text
读取最终 AIMessage 文本
→ 跳过 Markdown 代码块
→ 匹配独占一行的 DATAAGENT_ARTIFACT 指令
→ 规范化指令中的沙箱绝对路径
→ 校验路径属于当前 Conversation、文件存在且为普通文件
→ 推断媒体类型并投影为 MessageResponse.attachments
→ 从公开文本片段中移除已经成功解析的指令行
```

解析结果遵循以下规则：

- 同一路径重复出现时只生成一个附件，以第一次出现的位置排序。
- 成功解析的指令从展示文本中移除，避免用户同时看到内部路径和附件卡片。
- 无效、越权或缺失的指令不生成附件，并在展示文本中原样保留，便于发现模型引用错误。
- 单个文件的媒体类型使用现有附件服务的规则按路径推断；指令不携带说明文本。
- 工具消息已有的产物附件展示保持不变；该指令只控制 Planner 最终回答附带的文件。

## 5. 持久化与刷新一致性

最终 AIMessage 继续以包含指令的原始文本写入 LangGraph checkpoint。附件属于公开消息投影结果，不单独持久化。

实时 SSE 和历史消息接口必须调用同一个最终消息投影函数：

```text
原始 AIMessage + 当前 Conversation 沙箱
→ 指令解析
→ 当前 Conversation 文件校验
→ 清理后的 parts + attachments
```

因此页面实时接收、切换会话后返回和刷新页面后的展示结果保持一致。后续模型恢复对话时仍能看到原始指令和完整文件路径。

## 6. 各层职责

### Planner Prompt

- 告知 Planner 精确指令格式。
- 要求只选择用户需要直接查看或下载的最终文件。
- 数据中间件、复现脚本和审查证据只有在用户需要时才附带。
- 文件必须位于当前 Conversation 的 `/sessions/` 工作区，并确认路径准确。
- 最终回答中的结论文本正常书写，文件指令放在相关说明之后并独占一行。

### Analytics Service

- 解析最终 AIMessage 并生成 `MessageResponse.attachments`。
- 保证 SSE 与历史消息读取使用相同投影逻辑。
- 对无效指令记录结构化警告日志，但不阻断最终消息展示。

### Sandbox 与附件接口

- 继续负责 Conversation 路径隔离、文件存在性、普通文件、文件大小和下载权限校验。
- 不接受客户端通过指令绕过现有附件下载校验。

### Web 前端

- 只渲染后端返回的 `parts` 和 `attachments`。
- 不在浏览器中解析 `DATAAGENT_ARTIFACT` 指令，也不依据消息文本自行构造下载地址。

## 7. 安全边界

最终附件必须同时满足以下条件：

1. 指令位于最终 Assistant 文本的非代码块独占行。
2. 路径通过沙箱规范化和 Conversation 归属校验。
3. 文件在投影或下载时仍然存在，并满足现有文件服务限制。

即使模型输出其他 Conversation 的路径、用户在问题中植入伪造指令，或普通文本意外包含相同前缀，也不能得到可下载附件。

## 8. 实现位置

实现涉及以下位置：

```text
app/analytics/agents/planner/prompt.py
→ 增加最终产物选择和指令格式

app/analytics/services/chat.py
→ 解析最终 AIMessage
→ 校验当前 Conversation 文件并生成附件
→ 为实时和历史消息统一生成 parts 与 attachments

app/analytics/services/contracts.py
→ 定义消息投影需要的 ConversationFileInspector 协议

app/sandbox/archive.py
app/sandbox/manager.py
→ 校验文件归属、普通文件属性和下载大小限制

app/analytics/services/conversation_run.py
app/analytics/api/chat/router.py
→ 为实时运行和历史消息读取注入同一文件检查能力

tests/analytics/
→ 覆盖指令解析、Conversation 隔离、缺失文件、代码块忽略、去重和刷新一致性

tests/sandbox/
→ 覆盖可下载文件大小限制
```

现有 `Attachment` 和附件下载接口可以直接复用，无需修改公开 HTTP Schema。

## 9. 验收标准

- Planner 最终回答可以选择一份或多份当前 Conversation 文件作为附件。
- 未写入指令的文件不会自动附加到最终回答。
- 当前 Conversation 中未登记到 `artifacts` 的 Session 文件也可以通过指令交付。
- 实时消息和刷新后的历史消息展示相同的文本与附件。
- 跨 Conversation 路径、缺失文件和沙箱外路径不会生成附件。
- 代码块中的示例指令不会触发附件解析。
- 多条相同指令只产生一个附件。
- 前端无需增加模型文本解析逻辑。
