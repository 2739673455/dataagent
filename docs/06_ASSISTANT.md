# 06. Assistant 模块职责与实现

`assistant` 负责对话和多 Agent 分析流程，将元数据召回、SQL 查询、文件分析、审查和可视化组织成一轮可恢复的分析任务。

## 模块职责与边界

`assistant` 是用户问数流程的应用层入口。它管理 Conversation、消息和运行状态，创建 Planner 与专业 Agent Runtime，装配工具、模型、Checkpoint、Skill 和沙箱，并把一次分析过程投影为 SSE 事件、历史消息和可下载产物。

终端用户通过 `/api/v1/chat` 创建对话、提交问题、订阅事件、停止或恢复运行；Planner 使用委派和 Session 工具组织 Explorer、Analyst 与 Reviewer；专业 Agent 使用语义召回、查询、文件、Shell、图片、Skill 和 MCP 能力完成各自任务。

`assistant` 不直接维护数据权限、业务元数据、SQL 安全规则和 Docker 资源。它分别调用 `identity`、`metadata`、`query` 与 `sandbox` 的公开能力，并负责把这些能力装配到正确的用户、Conversation 和 Agent Session。

## 功能清单

```text
Assistant
→ 管理对话和消息
→ 执行一轮多 Agent 分析
→ 委派和恢复专业 Agent Session
→ 修补上游分析产物
→ 为 Explorer 提供召回和查询工具
→ 为专业 Agent 提供 Skill 和文件能力
→ 管理用户附件和 Agent 产物
→ 生成对话标题
→ 删除对话资源
```

## 1. 管理对话和消息

**实现目的**

为每次持续分析提供稳定的用户归属、标题、草稿状态和消息历史，使页面刷新、服务重启和多轮追问后仍能恢复同一个对话上下文。

**使用者与使用方式**

- 已具备分析资格的用户创建普通 Conversation 或草稿。
- 当前用户查询自己的对话列表、运行状态和历史消息。
- 用户可以修改标题、删除草稿或请求删除一个或多个 Conversation。
- 调试和审查场景可以查询指定专业 Agent Session 的活动消息。

**具体实现**

```text
用户创建对话
→ 创建绑定 user_id 的 Conversation
→ 根据 initial_message 生成临时标题
→ 非草稿且有 initial_message 时提交一次标题生成任务
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
→ 后台标题任务不能覆盖手动标题
```

每条模型返回消息都由时间戳中间件写入 `dataagent_created_at`。

### 设计细节：目录归属与消息状态使用两个事实来源

PostgreSQL 保存 Conversation 的用户归属、标题、草稿和删除状态。所有单条读取都同时带 `user_id` 与 `conversation_id`，默认排除删除中的记录：

```python
statement = select(Conversation).where(
    Conversation.user_id == user_id,
    Conversation.id == conversation_id,
)
if not include_deleting:
    statement = statement.where(
        Conversation.deletion_requested_at.is_(None)
    )
return await self._session.scalar(statement)
```

消息本体由 LangGraph Checkpoint 保存。读取历史时先通过运行时管理器定位当前用户的 Planner thread，再逐条做公开协议和附件投影：

```python
state = await agents.read_planner_state(user_id, conversation_id)
messages = state.values.get("messages", [])
if not isinstance(messages, list):
    return []

result: list[chat_schema.MessageResponse] = []
for message in messages:
    if not isinstance(message, BaseMessage):
        continue
    if schema := await langchain_message_to_schema_with_artifacts(
        message,
        files,
        user_id,
        conversation_id,
    ):
        result.append(schema)
return result
```

目录查询决定用户能否访问对话，Checkpoint 决定对话有哪些消息。消息投影会过滤内部消息类型，并重新验证其中引用的附件，避免把模型写出的任意路径直接公开给客户端。

## 2. 执行一轮多 Agent 分析

**实现目的**

把用户问题拆解为数据探索、分析和审查步骤，并在长时间运行期间持续输出可恢复、可订阅和可停止的事件流。

**使用者与使用方式**

- 用户通过 `/api/v1/chat/stream` 提交新消息并接收 SSE。
- 页面可以查询 `/{conversation_id}/run`，或通过 `/{conversation_id}/events` 重新订阅正在运行的回合。
- 用户通过 `/{conversation_id}/stop` 主动停止运行。
- Planner Checkpoint 仍有待执行节点时，客户端通过 `/{conversation_id}/resume` 恢复回合。

**具体实现**

```text
用户向 /api/v1/chat/stream 提交问题
→ 校验对话属于当前用户且未删除
→ 获取或创建 ConversationAgentRuntime
→ 使用 thread_id=user_{id}:conversation_{id}
→ 向 Planner 根 checkpoint 写入 HumanMessage
→ Planner 分解任务并调用 delegation
→ Explorer 检索目录并执行 SQL
→ Analyst 读取查询结果，完成分析、图表和自包含 HTML 报告
→ Reviewer 检查数据、方法、证据、结论和报告内容
→ Planner 汇总专业结果和附件
→ 通过 SSE 返回思考增量、消息增量、完整消息和专业 Agent 活动
→ 通过 SSE 返回错误和完成事件
```

流式连接每 15 秒发送 keep-alive。客户端断开只移除当前 SSE 订阅者，后台 Run 继续执行；只有显式 stop、对话删除或服务关闭才设置取消事件并停止后续工作。

Planner 可以在 QuickJS 中通过 Programmatic Tool Calling 调用白名单工具，并用 `Promise.all` 并行委派独立分支。Planner 还可通过 Conversation 级 Shell 查看工作区目录、附件和已知产物；Shell 在运行时缓存淘汰、会话删除或服务关闭时统一清理。并行 Session 和继续执行次数受配置限制。

每项模型配置通过 `api_protocol` 显式选择 `chat_completions` 或 `responses`。Responses 模型固定 `store=false` 和 `use_previous_response_id=false`，会话历史继续由 LangGraph checkpoint 管理。DeepSeek Responses 适配器会在工具续轮中完整重放无状态 API 要求的明文 reasoning item。OpenRouter Chat Completions 使用 `ChatOpenRouter`；`profile.structured_output=true` 时 Specialist 使用 Provider 原生 strict JSON Schema，最终 JSON 只用于恢复结构化状态，不进入公开消息。服务统一从 LangChain 标准 content blocks 读取 reasoning。协议调用失败时直接返回错误，不跨协议重试。


### 设计细节：共享模型资源与 Conversation 运行态分层创建

运行时工厂首次使用时只创建可以跨 Conversation 复用的模型、Explorer 工具定义、MCP 工具和 Specialist 定义。每个 Conversation 再单独创建 Sandbox Backend、Session Store、Session Service、Shell Job Runtime 和 Planner Graph。

```python
explorer_tools = [
    *create_semantic_recall_tools(),
    create_execute_sql_tool(build_query_execution_handler(self._sandbox)),
]
explorer_mcp_tools = await get_mcp_tools()

self._resources = _SharedAgentResources(
    planner_model=models[active_model_name],
    specialist_models=specialist_models,
    specialist_definitions=build_specialist_definitions(
        explorer_tools,
        explorer_mcp_tools,
    ),
)
```

Conversation 创建阶段绑定当前用户和对话：

```python
session_service = AgentSessionService(
    build_agent=specialist_factory.create,
    session_store=session_store,
    user_id=user_id,
    conversation_id=conversation_id,
    max_parallel_sessions=orchestration.max_parallel_sessions,
    max_sessions=orchestration.max_sessions,
)
planner_tools = [
    create_delegation_tool(session_service),
    create_list_sessions_tool(session_service),
    create_delete_session_tool(session_service),
]
```

Planner 只持有编排工具以及 Conversation 级文件、Shell 能力；Explorer 获得元数据召回、SQL 执行和 Explorer MCP；Analyst、Reviewer 获得各自定义的 Skill、文件与 Shell 能力。工具分配在创建 Graph 时固定，模型不能通过参数选择未授予的工具。


### 设计细节：Planner Run 独立于任意一条 SSE 连接

开始回合时，`ConversationRunService` 先在进程内注册后台 Task，再返回首个订阅生成器。浏览器断开只会从 `subscribers` 中移除对应队列，后台 Task 继续运行并保存 LangGraph Checkpoint。

```python
async with self._lock:
    existing = self._runs.get(key)
    if (
        existing is not None
        and existing.task is not None
        and not existing.task.done()
    ):
        raise ActiveConversationRunError
    self._runs[key] = run
    run.task = asyncio.create_task(
        self._execute(key, run, user_message),
        name=f"conversation-run:{user_id}:{conversation_id}",
    )
return self._consume(run, queue, ())
```

同一 Conversation 同时只允许一个 Run。重新订阅时，事件缓存快照和订阅登记共用同一把锁，事件必然进入 replay 或实时队列之一，不会落在两者之间。

Replay 同时限制为 512 个事件和 2 MiB。连续的正文或思考 delta 在消息标识一致时先合并，超过上限从最旧事件开始淘汰：

```python
run.events.append(event)
run.replay_bytes += self._event_size(event)
while run.events and (
    len(run.events) > _REPLAY_EVENT_LIMIT
    or run.replay_bytes > _REPLAY_BYTE_LIMIT
):
    run.replay_bytes -= self._event_size(run.events.popleft())
```

每个订阅者队列上限为 256。慢消费者队列满时只断开该订阅者，并提示重新连接；不会阻塞 Agent，也不会让其他客户端跟着变慢。显式 stop 才会设置 cancel event 并取消后台 Task。

Run 注册表、Replay 窗口和订阅队列都保存在当前 API 进程内。Run 结束后注册项立即删除，之后订阅只返回 `done`，历史消息从 LangGraph Checkpoint 读取。当前部署若运行多个 API 进程，Run 状态、events 订阅和 stop 请求需要被路由到启动该 Run 的进程；实现中没有跨进程事件总线。


### 设计细节：恢复的依据是 Checkpoint 待执行节点

SSE 断开且后台 Run 仍在当前进程时，客户端使用 events 订阅继续接收。进程中断或 Run 已不存在时，`resume` 先读取 Planner 最新 Checkpoint，只有 `next_nodes` 非空才创建恢复 Run：

```python
async def can_resume_agent_turn(
    agents: AgentRuntimeManager,
    user_id: int,
    conversation_id: UUID,
) -> bool:
    state = await agents.read_planner_state(user_id, conversation_id)
    return bool(state.next_nodes)
```

恢复调用向 LangGraph 传入空增量，让 Checkpointer 中已有状态继续推进。Planner 整个用户回合还持有 Conversation 生命周期 advisory lock，删除任务无法与正在持久化的新 Checkpoint 并发执行。

## 3. 委派和恢复专业 Agent Session

**实现目的**

为每个专业任务建立独立、可恢复和可并行的执行单元，使重复委派能够延续原上下文，新任务能够隔离运行，并让 Planner 获得稳定的结构化结果。

**使用者与使用方式**

- Planner 使用 `delegation` 指定 `analysis_id`、Agent 类型、`session_id` 和任务消息。
- Planner 使用 `list_sessions` 查看已建立的专业 Session。
- Planner 使用 `delete_session` 删除不再需要的 Session 及其沙箱文件。
- Explorer、Analyst 和 Reviewer 只在自己的 Session 中写文件，并通过结果协议返回结论、产物或修补请求。

**具体实现**

```text
Planner 调用 delegation
→ 提供 analysis_id、agent_type、session_id 和 message
→ 校验严格结构和 Planner 运行状态
→ 生成专业 Agent checkpoint namespace
→ 获取同 Session 的 PostgreSQL advisory lock
→ 获取或创建绑定独立沙箱目录的专业 Agent
→ 为本次 delegation 创建独占的 Shell Job Runtime
→ 调用 Agent 并解析结构化结果
→ 无可靠终答且格式无效时最多执行一次结构化修复
→ 校验每个 artifact 文件真实存在
→ 取消并清理仍在运行的 Shell Job
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
→ content 必须包含完整结论
→ artifacts 可选

needs_repair
→ 必须包含 repair_requests

failed
→ 必须包含 failure_reasons
```

三类 Agent 的职责和能力：

```text
Explorer
→ recall_context、list_recalls、get_recall、merge_recalls、delete_recalls
→ execute_sql
→ MCP 数据工具

Analyst
→ 读取 Explorer CSV
→ 执行数据质量、描述、对比、分解、下钻和根因分析
→ 生成图表、展示表格和自包含 HTML 报告
→ 按任务使用独立的 Analysis Skill 和 Visualization Skill

Reviewer
→ 审查上游数据、口径、计算、证据和结论
→ 发现问题时发起 RepairRequest
```

Planner 和三类 Specialist 都使用 `shell`、`list_shell_jobs`、`get_shell_job` 和 `cancel_shell_job`。Planner 的 Shell 限于查看 Conversation 工作区；Specialist 可在各自 Session 沙箱中处理文件和运行分析代码。`shell` 前台固定等待 60 秒：时限内结束时只返回合并 stdout/stderr；内联输出截断时在字符串末尾附加详细输出文件路径。此类前台命令不公开为 Shell Job。超时后任务留在当前 Agent 运行边界内继续运行并返回 `job_id`。`get_shell_job` 单次最多等待 60 秒，避免状态查询重新无限阻塞 Agent；它或 `cancel_shell_job` 返回终态结果时会消费该任务，后续列表和查询均不可见。后台任务首次出现后，`UserMessageContextMiddleware` 将当时可见任务的 `job_id` 和 `output_path` 写入最新真实用户消息的私有字段并随 Checkpoint 持久化。该中间件统一投影消息接收时间、附件、临时图片和 Shell Job 快照；快照写入后不再更新，任务状态继续由 Shell 工具结果表达，因此历史消息前缀保持稳定。

Shell 工具的 AIMessage 和 ToolMessage 沿用现有子 Agent 活动流。Specialist 返回最终结果前应处理运行中任务；Agent Run 的 `finally` 清理负责兜底终止遗留进程，完成后才释放 Session 锁。


### 设计细节：LangGraph thread、namespace 和 Sandbox 路径来自同一 SessionKey

一个 Conversation 只有一个 `thread_id`，Planner 使用根 namespace，专业 Session 使用 `subagents/{analysis_id}/{agent_type}/{session_id}`。同一组标识还生成 Sandbox Session 路径，因此 Checkpoint 和产物目录可以稳定对应。

```python
def get_thread_id(user_id: int, conversation_id: UUID) -> str:
    return f"user_{user_id}:conversation_{conversation_id}"


@property
def checkpoint_ns(self) -> str:
    return f"subagents/{self.analysis_id}/{self.agent_type}/{self.session_id}"
```

新 Session 在首次 Checkpoint 落库前不会出现在 namespace 列表中。跨进程容量限制会为这段空窗口占用 advisory-lock 槽位：

```python
namespaces = set(await self.list_namespaces(None))
if session_key.checkpoint_ns in namespaces:
    yield
    return
if len(namespaces) >= max_sessions:
    raise RuntimeError("当前 Conversation 的 Session 数量已达上限")

for slot in range(len(namespaces), max_sessions):
    try:
        async with self._persistence.advisory_lock(
            f"specialist-capacity:{self._thread_id}:{slot}"
        ):
            yield
            return
    except AdvisoryLockBusyError:
        continue
```

每个具体 Session 另有以 thread 和 namespace 命名的 advisory lock，保证两个 API/Worker 进程不会同时续接同一专业 Session。进程内 Semaphore 则限制同一 Conversation 同时运行的专业分析数量。


### 设计细节：委派结果使用严格协议，并允许一次结构修复

`DelegationRequest`、`SpecialistResult` 和 `DelegationResult` 都启用 `strict=True` 与 `extra="forbid"`。结果状态决定允许出现的字段：`needs_repair` 必须携带修补请求，`failed` 必须携带失败原因，其他状态不能夹带这些字段。

```python
@model_validator(mode="after")
def validate_status_payload(self) -> Self:
    if self.status == "needs_repair" and not self.repair_requests:
        raise ValueError("needs_repair 状态必须包含至少一个修补请求")
    if self.status != "needs_repair" and self.repair_requests:
        raise ValueError("修补请求仅在 needs_repair 状态下有效")
    if self.status == "failed" and not self.failure_reasons:
        raise ValueError("failed 状态必须包含至少一个失败原因 (failure_reasons)")
    if self.status != "failed" and self.failure_reasons:
        raise ValueError("失败原因仅在 failed 状态下有效")
    return self
```

专业 Agent 首次输出无法解析时，Service 先检查是否存在可以保留的纯文本终答；没有可靠终答才发送一次内部修复消息，让同一 Session 只修正结构。第二次仍不合法则委派失败，不继续无限重试。

产物跨 Agent 边界前会把相对路径解析为当前 Session 的绝对路径，过滤不属于该 Session 或实际不存在的文件，并把原因加入 warnings：

```python
out_of_scope = {
    path for path in artifact_paths if not path.startswith(session_prefix)
}
in_scope = artifact_paths - out_of_scope
missing = (
    set(await self._session_store.find_missing_files(in_scope))
    if in_scope
    else set()
)
invalid_paths = out_of_scope | missing
artifacts = [
    artifact
    for artifact in result.artifacts
    if artifact.path not in invalid_paths
]
```

修补请求只能指向同一 `analysis_id` 下已经存在的其他 Session，不能让 Agent 修补自身，也不能凭空创建一个未执行过的上游目标。

## 4. 修补上游分析产物

**实现目的**

让 Reviewer 或下游 Agent 发现数据、口径、计算或报告缺陷后，能够回到产生问题的原 Session 修正，并保留原 Checkpoint、脚本和中间文件。

**使用者与使用方式**

- 下游 Specialist 返回 `needs_repair` 和结构化 `repair_requests`。
- 每个修补请求明确目标 Agent、目标 Session、原因和期望结果。
- Planner 决定是否发起修补，并在完成后重新运行受影响的下游验证。

**具体实现**

```text
下游 Agent 发现上游问题
→ 返回 target_agent_type、target_session_id
→ 提供 reason 和 expected_result，问题依据写入 reason
→ Planner 将修补消息委派回原 Session
→ 原 Agent 读取原 checkpoint 和原文件
→ 原 Agent 修改自己的产物并返回新结果
→ Planner 重新运行依赖旧产物的下游 Session
```

Planner 根据修补结果和当前分析目标决定是否继续处理 Repair Request。

### 设计细节：修补只能回到同一 Analysis 的既有 Session

结构化结果返回后，服务用当前 `AgentSessionKey` 重建每个目标键。目标 namespace 与当前 Session 相同会被视为自修补；Checkpoint 中查不到目标则拒绝请求：

```python
for request in result.repair_requests:
    target_key = AgentSessionKey(
        user_id=session_key.user_id,
        conversation_id=session_key.conversation_id,
        analysis_id=session_key.analysis_id,
        agent_type=request.target_agent_type,
        session_id=request.target_session_id,
    )
    if target_key.checkpoint_ns == session_key.checkpoint_ns:
        raise ValueError("专业 Agent Session 不能请求修补自身")
    if not await self._is_existing_session(target_key):
        raise ValueError(
            "修补目标必须是同一分析中已存在的 Session: "
            f"{request.target_agent_type}/{request.target_session_id}"
        )
```

`user_id`、`conversation_id` 和 `analysis_id` 都取自当前 Session，模型只能提供目标 Agent 类型和 Session ID，因此不能把修补请求指向其他用户、Conversation 或 Analysis。复用原 namespace 让目标 Agent 保留原消息状态，复用原工作目录让它在已有脚本和产物上继续修改。

## 5. 为 Explorer 提供召回和查询工具

**实现目的**

让 Explorer 在受控接口内定位业务数据、积累查询上下文和执行 SQL，同时把大体量召回内容与完整查询结果保存在服务端，控制模型消息大小。

**使用者与使用方式**

- Explorer 使用 `recall_context`、`list_recalls`、`get_recall`、`merge_recalls` 和 `delete_recalls` 管理语义上下文。
- Explorer 使用 `execute_sql` 执行只读查询并获得 CSV 路径与摘要。
- 配置的 MCP 数据工具只提供给 Explorer。
- 其他 Specialist 通过上游文件消费数据，不直接调用语义召回或 Doris 查询工具。

**具体实现**

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
→ 投影为 created_at、updated_at、tables、columns、values、metrics 和 query_experiences
→ 临时替换本次 ModelRequest 中的工具消息
→ 不把展开内容写回 LangGraph 历史
```

`created_at` 固定为 query 持续上下文的首次创建时间；追加召回、合并或资源删除生成新快照时保留该值，并更新 `updated_at`。同一 HumanMessage 后的多轮 Assistant/Tool 循环持续看到本轮引用。新的 HumanMessage 开始后，Explorer 可以调用 `get_recall` 重新引用之前的 query。

```text
Explorer 调用 execute_sql
→ 模型提供 purpose 和 SQL
→ 工具从 runtime 读取 user、conversation、analysis、session 和 tool_call
→ 调用 QueryExecutionHandler
→ 成功时返回 CSV 路径和摘要
→ 失败时返回具体异常类型、原因和 Guard issues
```


### 设计细节：语义召回在 Checkpoint 中保存引用，在模型调用时临时展开

Explorer 工具把完整召回写入 Metadata PostgreSQL，ToolMessage 只保存稳定 query 引用。模型调用中间件定位当前用户回合产生的引用，重新加载记录并应用当前授权，然后只在请求副本中替换消息内容：

```python
references = _current_turn_references(request.messages)
if not references:
    return await handler(request)

records, missing_queries = await _load_recall_records(
    user_id,
    conversation_id,
    references,
)
messages = _replace_reference_content(
    list(request.messages),
    references,
    records,
    missing_queries,
)
return await handler(request.override(messages=messages))
```

Checkpoint 体积因此不会随反复召回完整目录持续膨胀。每次模型调用读取的都是最新累计快照和当前授权投影；管理员收窄权限后，旧引用也无法恢复被撤销的字段。公开消息读取使用相同展开逻辑，前端看到的 Tool 结果与模型实际获得的结构一致。

## 6. 为专业 Agent 提供 Skill 和文件能力

**实现目的**

为不同专业角色提供可复用的方法说明、脚本和文件处理能力，并保持 Skill 内容只读、Session 写入边界清晰。

**使用者与使用方式**

- Analyst 按任务读取 Analysis Skill 和 Visualization Skill。
- Specialist 使用文件工具读写当前 Session，并读取同一 Conversation 的上游产物。
- Planner 通过只读文件与 Shell 能力检查 Conversation 工作区。
- 支持图片输入的模型使用 `view_image` 查看工作区图片。

**具体实现**

```text
应用启动
→ 扫描 app/assistant/agents/{agent}/skills
→ 将存在的目录只读挂载到 /skills/{agent}
→ 为配置了 Skill 的 Agent 建立 CompositeBackend

Agent 使用 Skill
→ 读取 SKILL.md 和参考文件
→ 可以执行 Skill 目录中的脚本
→ 文件中间件和 Docker 挂载共同拒绝写入 Skill

Agent 使用工作区
→ 只能写 sessions/{analysis_id}/{agent_type}/{session_id}
→ 可以读取同一会话其他 Session 的产物
→ 下游 Agent 用代码继续处理上游文件
```

### 设计细节：CompositeBackend 将 Skill 只读路由与 Session 沙箱组合

每个 Agent 最多配置一个 Skill 根目录。Skill 使用本地只读 `FilesystemBackend` 路由，默认路由仍指向当前 Session 的 `DockerSandboxBackend`：

```python
routes[mount_path] = FilesystemBackend(
    root_dir=skill_directory,
    virtual_mode=True,
)
permissions.append(
    FilesystemPermission(
        operations=["write"],
        paths=[f"{mount_path}**"],
        mode="deny",
    )
)

resolved_backend = CompositeBackend(
    default=backend,
    routes=routes,
    artifacts_root=workspace_dir,
)
filesystem = FilesystemMiddleware(
    backend=resolved_backend,
    system_prompt=_filesystem_system_prompt(workspace_dir),
    tools=["read_file", "write_file", "edit_file"],
    _permissions=permissions,
)
```

中间件权限在工具调用层拒绝 Skill 写入，Docker 的只读 bind mount 在执行层再次约束。默认 Backend 自带当前 `SandboxSessionScope`，所以相对路径落入当前 Session；读取 Conversation 内其他目录时仍由 Sandbox 路径策略判断只读范围。文件工具、`view_image` 和 Shell 使用同一容器路径语义。

## 7. 管理用户附件和 Agent 产物

**实现目的**

让用户输入文件安全进入分析上下文，并把 Agent 生成的 CSV、图表和 HTML 报告转换为经过归属校验的可下载附件。

**使用者与使用方式**

- 用户通过 `/api/v1/chat/attachment/upload` 上传当前 Conversation 的附件。
- 用户通过附件获取和删除接口管理自己上传的文件。
- Agent 在 Session 中生成产物，并在结构化结果或最终回答中引用路径。
- 消息投影服务将合法路径转换为前端可消费的附件描述。

**具体实现**

```text
用户上传附件
→ 校验对话归属并获取生命周期锁
→ 规范化文件名和路径
→ 统一保存到 conversation/uploads/
→ 执行单文件和工作区容量限制
→ 返回附件路径

附件进入模型
→ `profile.image_inputs=true` 时，每次调用都重新加载当前上下文中所有 HumanMessage 的图片
→ Responses API 额外向 Agent 提供 view_image，用于读取工作区或仅保留路径引用的图片
→ view_image 与其他文件工具一致：相对路径从当前 Session 解析，绝对路径直接使用
→ view_image 只持久化工作区路径，下一次模型调用临时加载图片工具结果
→ `profile.image_inputs=false` 时只提供附件路径，并明确告知模型图片不会自动加载
→ 文档和数据文件以沙箱路径提供给 Agent

专业 Agent 返回 artifact
→ 校验绝对规范化路径
→ 校验文件位于当前会话且真实存在
→ delegation ToolMessage 暴露下载信息

Planner 最终交付文件
→ 只选择用户需要直接查看或下载的当前会话文件
→ 最终报告或综合报告只交付 Analyst 生成的自包含 HTML
→ Markdown 仅作为内部分析和审查证据，不作为最终报告
→ PNG、SVG、CSV、Parquet 等可以作为 HTML 报告的配套附件
→ 在最终回答中使用独占一行的 `[[DATAAGENT_ARTIFACT:<absolute_path>]]` 指令
→ 文件无需预先出现在 delegation 的 artifacts 中
→ 后端校验路径属于当前对话且文件真实可下载
→ 实时和历史消息统一投影为附件
→ 前端只渲染后端返回的附件，不解析模型文本

用户下载或删除附件
→ 校验对话归属
→ 下载时推断 MIME type
→ 删除只允许 uploads 下的用户文件
```


### 设计细节：附件和最终产物必须经过真实文件验证

用户消息写入 Checkpoint 前会规范化附件路径并确认文件存在。专业 Agent 产物由委派协议校验；Planner 最终回答使用独占行指令 `[[DATAAGENT_ARTIFACT:/...]]` 声明文件。投影层忽略代码块内的相似文本，只接受当前 Conversation 的 `sessions` 文件，并调用 Sandbox 再次确认可下载。

```python
try:
    relative_path = _normalized_directive_path(
        directive_path,
        conversation_id,
    )
except SandboxPathError:
    continue

downloadable = await files.is_downloadable_file(
    user_id,
    conversation_id,
    relative_path,
)
if not downloadable:
    continue
```

验证成功的指令从正文中移除并投影为结构化 Attachment；无效指令保留在文本中用于诊断，不会伪装成可下载文件。MIME 类型根据文件名推断，文件内容仍通过受权限保护的附件接口读取。

## 8. 生成对话标题

**实现目的**

在用户首次提问后立即提供可用标题，再用轻量模型异步生成更准确的标题，避免标题生成延长聊天请求时间。

**使用者与使用方式**

- 创建非草稿 Conversation 时可以通过 `initial_message` 触发标题生成。
- 草稿第一次提交真实消息时结束草稿状态并触发标题任务。
- 用户可以手工修改标题；后台任务不会覆盖已经变化的标题。

**具体实现**

```text
首条用户文本创建 Conversation 或结束草稿时
→ 在事务内写入即时标题并结束草稿状态
→ 提交 lightweight 标题任务
→ 标题模型读取对话文本
→ 只提取 response text
→ 清理并限制标题长度
→ 条件更新标题仍等于即时标题的对话

标题任务提交、生成或重试失败时保留即时标题并记录日志。
```

### 设计细节：标题更新使用比较后替换保护用户编辑

即时标题直接取首条文本的前 64 个字符，使创建 Conversation 不依赖模型响应。后台服务限制模型输入长度、清理包装符号，再提交条件更新：

```python
response = await self._model.ainvoke(
    [
        SystemMessage(content=_TITLE_PROMPT),
        HumanMessage(content=user_text[:_MAX_MODEL_INPUT_LENGTH]),
    ]
)
generated_title = _normalize_generated_title(response.text)
if not generated_title:
    return False

return await conversation_repo.replace_title_if_current(
    user_id,
    conversation_id,
    expected_title=expected_title,
    title=generated_title,
)
```

仓储更新条件同时包含用户、Conversation、原即时标题和“尚未删除”：

```python
update(Conversation)
.where(
    Conversation.user_id == user_id,
    Conversation.id == conversation_id,
    Conversation.title == expected_title,
    Conversation.deletion_requested_at.is_(None),
)
.values(title=title, update_at=datetime.now(UTC))
```

用户在后台任务完成前改过标题时，`rowcount` 为零，生成结果直接丢弃。相同机制也防止迟到任务更新已经进入删除流程的 Conversation。

## 9. 删除对话资源

**实现目的**

让 Conversation 在用户请求后立即停止运行并从列表隐藏，再通过可恢复任务清理分布在 PostgreSQL、LangGraph、元数据快照和沙箱中的资源。

**使用者与使用方式**

- 当前用户可以请求删除自己的普通 Conversation。
- 放弃草稿时调用幂等草稿删除接口。
- Celery Worker 执行物理清理，周期任务恢复丢失的删除任务并清理过期草稿。
- `workflows` 在用户注销时复用批量 Conversation 清理能力。

**具体实现**

```text
用户请求删除对话
→ 取消 AgentManager 当前运行态
→ 在 Conversation 写入 deletion_requested_at
→ 对话立即从列表隐藏
→ 提交 lifecycle 清理任务

Worker 物理清理
→ 获取会话生命周期 advisory lock
→ 在 Agent 状态存储中持久化 ConversationTombstone
→ 删除 Planner 和专业 Agent 的 LangGraph 状态
→ 删除该会话全部语义召回快照
→ 删除沙箱 conversation 目录
→ 删除 Conversation

用户注销清理
→ 删除用户残留的孤立 LangGraph 线程
→ 删除该用户全部 ConversationTombstone

草稿过期或删除任务丢失
→ Beat 扫描过期 draft 和带 deletion_requested_at 的 Conversation
→ 重新提交同一幂等清理流程
```

`AgentManager` 按用户和会话缓存最多 128 个运行时，并在创建和恢复前检查墓碑，防止已删除对话重新出现。


### 设计细节：Conversation 删除采用“隐藏 → 墓碑 → 物理清理”顺序

删除请求先取消当前进程 Run，在 Conversation 行写 `deletion_requested_at`。普通列表和读取接口立即隐藏该记录。Worker 获取 Conversation 生命周期锁后再次读取当前状态，并按固定顺序清理：

```python
await self._agents.delete_agent_under_lifecycle_lock(
    user_id,
    conversation_id,
)
async with self._recall_cleaner_factory() as recall_cleaner:
    await recall_cleaner.delete_all(user_id, conversation_id)
await self._sandbox.delete_conversation(user_id, conversation_id)
async with self._repository_factory() as repository:
    await repository.delete(user_id, conversation_id)
```

Agent 清理会先持久化 `ConversationTombstone`，再删除整个 LangGraph thread：

```python
await self._tombstones.save(user_id, conversation_id)
await self._persistence_manager.delete_thread(
    get_thread_id(user_id, conversation_id)
)
```

墓碑阻止其他进程在清理窗口重新构建运行时和 Checkpoint。语义召回、Sandbox 或 Conversation 删除任一步失败时任务重试；已完成的删除步骤允许目标不存在。普通 Conversation 清理保留 PostgreSQL 墓碑，用户级注销在删除全部孤立线程后统一删除该用户墓碑。

## 接口与任务

```text
/api/v1/chat
→ create、delete、draft delete、update、list 和 messages
→ stream、resume、run status、events subscribe 和 stop

/api/v1/chat/attachment
→ upload、get、delete

assistant Celery 任务
→ generate_conversation_title
→ delete_conversation_resources
→ cleanup_expired_drafts
```
