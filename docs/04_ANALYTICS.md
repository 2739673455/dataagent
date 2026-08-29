# Analytics 模块功能

`analytics` 负责对话和多 Agent 分析流程，将元数据召回、SQL 查询、文件分析、审查和可视化组织成一轮可恢复的分析任务。

## 功能清单

```text
Analytics
→ 管理对话和消息
→ 执行一轮多 Agent 分析
→ 委派和恢复专业 Agent Session
→ 修补上游分析产物
→ 为 Explorer 提供召回和查询工具
→ 为专业 Agent 提供 Skill 和文件能力
→ 管理用户附件和 Agent 产物
→ 生成和修复对话标题
→ 删除对话资源
```

## 1. 管理对话和消息

```text
用户创建对话
→ 创建绑定 user_id 的 Conversation
→ 根据 initial_message 生成临时标题
→ 可选择创建 draft

用户查看对话列表
→ 只查询当前用户的对话
→ 隐藏已经进入删除流程的对话

用户查看历史消息
→ 校验对话归属
→ 从 LangGraph Planner state 读取消息
→ 返回文本、工具结果、附件和消息时间戳

用户手动修改标题
→ 更新 Conversation.title
→ 取消 title_pending
→ 后台标题任务不能覆盖手动标题
```

每条模型返回消息都由时间戳中间件写入 `dataagent_created_at`。

## 2. 执行一轮多 Agent 分析

```text
用户向 /api/v1/chat/stream 提交问题
→ 校验对话属于当前用户且未删除
→ 获取或创建 ConversationAgentRuntime
→ 使用 thread_id=user_{id}:conversation_{id}
→ 向 Planner 根 checkpoint 写入 HumanMessage
→ Planner 分解任务并调用 delegation
→ Explorer 检索目录并执行 SQL
→ Analyst 读取查询结果并完成分析
→ Reviewer 检查数据、方法、证据和结论
→ Visualizer 生成图表、表格或报告
→ Planner 汇总专业结果和附件
→ 通过 SSE 返回 message、error 和 done 事件
```

流式连接每 15 秒发送 keep-alive。客户端断开后设置取消事件，运行时在安全边界停止后续工作。

Planner 可以在 QuickJS 中通过 Programmatic Tool Calling 调用白名单工具，并用 `Promise.all` 并行委派独立分支。并行 Session 和继续执行次数受配置限制。

## 3. 委派和恢复专业 Agent Session

```text
Planner 调用 delegation
→ 提供 analysis_id、agent_type、session_id 和 message
→ 校验严格结构和 Planner 运行状态
→ 生成专业 Agent checkpoint namespace
→ 获取同 Session 的进程内锁和 PostgreSQL advisory lock
→ 获取或创建绑定独立沙箱目录的专业 Agent
→ 调用 Agent 并解析结构化结果
→ 格式无效时执行有限次结构化重试
→ 校验每个 artifact 文件真实存在
→ 返回稳定 DelegationResult
```

```text
同一个 analysis_id + agent_type + session_id 再次委派
→ 恢复原 LangGraph checkpoint
→ 复用原沙箱 Session 目录
→ 在原上下文上继续工作

使用新的 session_id
→ 创建独立 checkpoint namespace
→ 创建独立可写目录
→ 可以与其他 Session 并行执行
```

专业 Agent 结果状态：

```text
completed
→ 必须包含 findings
→ 必须包含 artifacts

needs_repair
→ 必须包含 repair_requests

failed
→ 必须包含 limitations
```

四类 Agent 的职责和能力：

```text
Explorer
→ recall_context、list_recalls、get_recall、merge_recalls、delete_recalls
→ execute_sql
→ MCP 数据工具

Analyst
→ 读取 Explorer CSV
→ 执行数据质量、描述、对比、分解、下钻和根因分析
→ 使用 Analyst 数据分析 Skill

Reviewer
→ 审查上游数据、口径、计算、证据和结论
→ 发现问题时发起 RepairRequest

Visualizer
→ 读取已审查数据和结论
→ 生成图表、表格和下载报告
```

## 4. 修补上游分析产物

```text
下游 Agent 发现上游问题
→ 返回 target_agent_type、target_session_id
→ 提供 reason、evidence 和 expected_result
→ Planner 将修补消息委派回原 Session
→ 原 Agent 读取原 checkpoint 和原文件
→ 原 Agent 修改自己的产物并返回新结果
→ Planner 重新运行依赖旧产物的下游 Session
```

系统限制 repair depth、重复 repair fingerprint 和 Session 恢复次数，避免循环修补。

## 5. 为 Explorer 提供召回和查询工具

```text
Explorer 调用 recall_context
→ 模型提供 query、resource_types、terms 和 limit_per_type
→ LangGraph 注入 ToolRuntime
→ 工具规范化 query 并获取当前授权
→ metadata 执行语义资源召回
→ query 独立执行查询经验召回
→ 查询经验失败时使用空列表
→ metadata 保存或更新 query 持续上下文
→ 工具只返回轻量 SemanticRecallReference

模型准备下一次调用
→ SemanticRecallExpansionMiddleware 查找当前 HumanMessage 后的召回引用
→ 按 query 读取最新 SemanticRecallRecord
→ 使用当前用户授权再次过滤
→ 投影为 tables、columns、values、metrics 和 query_experiences
→ 临时替换本次 ModelRequest 中的工具消息
→ 不把展开内容写回 LangGraph 历史
```

同一 HumanMessage 后的多轮 Assistant/Tool 循环持续看到本轮引用。新的 HumanMessage 开始后，Explorer 可以调用 `get_recall` 重新引用之前的 query。

```text
Explorer 调用 execute_sql
→ 模型提供 purpose 和 SQL
→ 工具从 runtime 读取 user、conversation、analysis、session 和 tool_call
→ 调用 QueryExecutionHandler
→ 成功时返回 CSV 路径和摘要
→ 失败时返回具体异常类型、原因和 Guard issues
```

## 6. 为专业 Agent 提供 Skill 和文件能力

```text
应用启动
→ 扫描 app/analytics/agents/{agent}/skills
→ 将存在的目录只读挂载到 /skills/{agent}
→ 为配置了 Skill 的 Agent 建立 CompositeBackend

Agent 使用 Skill
→ 读取 SKILL.md 和参考文件
→ 可以执行 Skill 目录中的脚本
→ 文件中间件和 Docker 挂载共同拒绝写入 Skill

Agent 使用工作区
→ 只能写 analyses/{analysis_id}/sessions/{agent_type}/{session_id}
→ 可以读取同一会话其他 Session 的产物
→ 下游 Agent 用代码继续处理上游文件
```

## 7. 管理用户附件和 Agent 产物

```text
用户上传附件
→ 校验对话归属并获取生命周期锁
→ 规范化文件名和路径
→ 统一保存到 conversation/uploads/
→ 执行单文件和工作区容量限制
→ 返回附件路径

附件进入模型
→ 图片按需要转换为模型图像内容
→ 文档和数据文件以沙箱路径提供给 Agent

专业 Agent 返回 artifact
→ 校验绝对规范化路径
→ 校验文件位于当前会话且真实存在
→ delegation ToolMessage 暴露下载信息

用户下载或删除附件
→ 校验对话归属
→ 下载时推断 MIME type
→ 删除只允许 uploads 下的用户文件
```

## 8. 生成和修复对话标题

```text
对话首次有效交互
→ 标记 title_pending
→ 提交 lightweight 标题任务
→ 标题模型读取对话文本
→ 只提取 response text
→ 清理并限制标题长度
→ 条件更新仍处于 title_pending 的对话

标题任务丢失或失败
→ Beat 扫描长期 title_pending 对话
→ 重新提交标题任务
```

## 9. 删除对话资源

```text
用户请求删除对话
→ 写 ConversationTombstone
→ 标记沙箱会话已删除
→ 取消 AgentManager 当前运行态
→ 对话立即从列表隐藏
→ 提交 lifecycle 清理任务

Worker 物理清理
→ 获取会话生命周期 advisory lock
→ 删除 Planner 和专业 Agent 的 LangGraph 状态
→ 删除该会话全部语义召回快照
→ 删除沙箱 conversation 目录
→ 删除 Conversation
→ 删除 Tombstone

草稿过期或删除任务丢失
→ Beat 扫描过期 draft 和未完成 tombstone
→ 重新提交同一幂等清理流程
```

`AgentManager` 按用户和会话缓存最多 128 个运行时，并在创建和恢复前检查墓碑，防止已删除对话重新出现。

## 接口、任务和代码

```text
/api/v1/chat
→ create、delete、draft delete、update、list、messages、stream

/api/v1/chat/attachment
→ upload、get、delete

analytics Celery 任务
→ generate_conversation_title
→ repair_conversation_titles
→ delete_conversation_resources
→ cleanup_expired_drafts

代码
→ app/analytics/agents
→ app/analytics/api/chat
→ app/analytics/api/attachment
→ app/analytics/models、repositories、services
→ app/analytics/tasks.py
```
