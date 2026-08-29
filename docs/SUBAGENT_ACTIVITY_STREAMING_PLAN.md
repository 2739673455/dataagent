# 子 Agent 工作细节展示实施计划

## 1. 决策

本期保留现有同步 `delegation`，增加子 Agent 活动流和历史查询能力。

Planner 发起 `delegation` 后继续等待 Specialist 的最终结构化结果。Specialist 执行期间产生的消息、工具调用和工具结果通过 Planner 当前执行流写入聊天 SSE，前端在对应的 delegation 卡片内实时展示。

本期不引入 Deep Agents 异步 delegation。异步 delegation 适合后台运行、跨用户回合管理和执行中追加指令，对工作细节展示仍然需要单独的事件订阅链路。当前需求可以直接利用现有 SSE、Session Checkpoint 和同步执行生命周期完成。

## 2. 目标

- Planner 发出 delegation 工具调用后，前端立即显示对应 Specialist 正在运行。
- 前端实时显示 Specialist 的可见消息、工具调用和工具结果。
- 多个并行 delegation 的事件能够准确归属到各自的工具卡片。
- Specialist 最终结果继续经过现有结构化协议、修补限制和产物验证。
- 刷新页面后可以按需恢复某次 delegation 的工作详情。
- 保持 Planner 消息和 Specialist 消息的层级关系。
- SSE 断开或用户停止生成时，沿用当前同步执行的取消语义。

## 3. 本期边界

前端展示以下内容：

- Specialist 输出的普通文本消息。
- Specialist 发起的工具名称和参数。
- 工具执行结果。
- 工具产生的可下载产物。
- Specialist 类型、Session 标识和当前状态。

以下内容不发送前端：

- System Prompt。
- 模型隐藏 reasoning 或思维链。
- 用户消息和内部消息的私有元数据。
- `_STRUCTURED_RETRY_MESSAGE` 等内部结构化修正指令。
- 模型供应商原始响应对象。
- 可能包含凭据的内部配置和异常上下文。

本期不支持：

- Specialist 脱离当前聊天请求继续后台运行。
- 用户在 Specialist 运行期间追加任务指令。
- 单独暂停或取消某一个 Specialist。
- Planner 在等待 Specialist 时继续下一轮模型决策。
- Token 级文本增量展示。

## 4. 现状

当前执行链路为：

```text
Planner model
  -> delegation tool
    -> AgentSessionService.execute_delegation
      -> Specialist agent.ainvoke
        -> SpecialistResult
      <- DelegationResult
    <- tool result
  -> Planner model continues
```

当前行为存在以下限制：

- `AgentSessionService._invoke_specialist` 使用 `agent.ainvoke()`，只有执行完成后才能取得结果。
- Planner `astream()` 只消费根图的 `model` 和 `tools` 更新。
- Specialist 是由 delegation 工具单独调用的 CompiledStateGraph，不属于 Planner 图中可自动透传事件的原生嵌套 subgraph。
- 聊天 SSE 只有根消息、错误和完成事件。
- 前端把所有根消息保存在一个扁平消息数组中。
- Specialist 消息已经保存在独立 Checkpoint namespace，但消息列表接口只读取 Planner 根 namespace。

## 5. 总体链路

调整后的执行链路为：

```text
Planner 发出 delegation tool_call
  -> 前端创建 delegation 卡片
  -> delegation tool 将 tool_call_id 和 StreamWriter 传给 SessionService
  -> Specialist agent.astream 执行
      -> model 更新
      -> tools 更新
      -> SessionService 生成内部 SubagentActivity
      -> StreamWriter 写入 Planner custom stream
  -> chat service 将 custom stream 转换为 SSE
  -> 前端按 delegation_id 更新卡片内的消息和工具运行
  -> Specialist 完成并返回 SpecialistResult
  -> 现有验证逻辑生成 DelegationResult
  -> Planner 继续执行
```

`delegation_id` 使用 Planner delegation 工具调用的 `tool_call_id`。这个值同时承担以下职责：

- 关联 Planner 工具调用和 Specialist 工作详情。
- 区分同一个 Session 的多次恢复执行。
- 区分同一轮中的多个并行 Specialist。
- 支持刷新后按某次 delegation 查询历史详情。

## 6. 内部活动协议

在 Agent 层定义内部活动对象，保持 Agent 层不依赖聊天 API Schema：

```python
@dataclass(frozen=True, slots=True)
class SubagentMessageActivity:
    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    message: BaseMessage


@dataclass(frozen=True, slots=True)
class SubagentStatusActivity:
    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    status: Literal[
        "running",
        "completed",
        "needs_repair",
        "failed",
        "cancelled",
    ]
```

活动对象只存在于当前 Python 执行流中，不写入 Planner 消息历史。聊天服务负责把 `BaseMessage` 转换为公开 `MessageResponse`。

定义明确的活动写入协议：

```python
type SubagentActivityWriter = Callable[[SubagentActivity], None]
```

`AgentSessionService` 接收这个回调，不直接依赖 LangGraph `ToolRuntime` 或聊天 SSE。

## 7. Specialist 流式执行

### 7.1 调用方式

将 Specialist 的 `ainvoke()` 调整为 `astream()`：

```python
async for part in agent.astream(
    input,
    config=config,
    stream_mode=["updates", "values"],
    version="v2",
):
    ...
```

- `updates` 用于读取每个 `model` 和 `tools` 节点新增的消息。
- `values` 用于保留最终完整状态并解析 `structured_response`。
- 每次调用维护已发送的 `message.id` 集合，避免同一消息重复发送。
- 只发送 `AIMessage` 和 `ToolMessage`。
- delegation 的任务输入由根工具调用参数展示，不作为 Specialist 对话消息重复发送。

### 7.2 结构化修正

第一次输出无法通过 `SpecialistResult` 校验时，继续执行现有一次结构化修正。

修正过程遵循以下规则：

- 内部修正 `HumanMessage` 不发送前端。
- 修正阶段产生的结构化协议工具调用不展示为业务工具调用。
- 最终结果继续执行 repair target 和 artifact 验证。
- 最终状态由验证后的 `SpecialistResult.status` 决定。

### 7.3 取消和异常

- 收到 `asyncio.CancelledError` 时发送 `cancelled` 状态，然后继续抛出取消异常。
- Specialist 执行失败时发送 `failed` 状态，现有 delegation 错误协议继续返回给 Planner。
- SSE 已经断开时不保证最终状态能够送达前端；前端将仍在运行的卡片标记为已中断。

## 8. delegation 工具接线

`create_delegation_tool` 从 `ToolRuntime` 取得：

```python
delegation_id = runtime.tool_call_id
activity_writer = runtime.stream_writer
```

调用 SessionService：

```python
result = await service.execute_delegation(
    request,
    parent_config,
    delegation_id=delegation_id,
    activity_writer=activity_writer,
)
```

SessionService 在真正取得 Session 执行锁并进入并发额度后发送 `running`，在结果完成验证后发送最终状态。

等待 Session 锁和并发额度期间不发送 `running`。如需表达排队状态，可以后续增加 `queued`，本期不增加。

## 9. Planner 流与聊天 SSE

### 9.1 Planner 流

Planner 调用改为：

```python
runtime.planner.astream(
    input={"messages": input_messages},
    config=config,
    stream_mode=["updates", "custom"],
    version="v2",
)
```

聊天服务分别处理：

- `updates`：沿用当前 Planner `model`、`tools` 消息转换。
- `custom`：只接受受信任的 `SubagentMessageActivity` 和 `SubagentStatusActivity`。
- 其他 custom payload 不通过聊天 API。

### 9.2 SSE Schema

保留现有事件：

```json
{"type":"message","message":{}}
{"type":"error","content":"..."}
{"type":"done"}
```

新增子 Agent 消息事件：

```json
{
  "type": "subagent_message",
  "delegation_id": "call_123",
  "analysis_id": "sales-analysis",
  "agent_type": "explorer",
  "session_id": "sales-source",
  "message": {
    "message_id": "...",
    "created_at": "2026-08-29T12:00:00Z",
    "role": "assistant",
    "parts": []
  }
}
```

新增状态事件：

```json
{
  "type": "subagent_status",
  "delegation_id": "call_123",
  "analysis_id": "sales-analysis",
  "agent_type": "explorer",
  "session_id": "sales-source",
  "status": "running"
}
```

事件 Schema 使用判别联合，不增加带大量可选字段的通用事件对象。

## 10. 前端状态和展示

### 10.1 状态结构

在聊天 Store 中增加：

```typescript
type SubagentRun = {
  delegationId: string;
  analysisId: string;
  agentType: AgentType;
  sessionId: string;
  status: SubagentRunStatus;
  messages: MessageResponse[];
  historyLoaded: boolean;
};

type SubagentRunsByConversation = Record<
  string,
  Record<string, SubagentRun>
>;
```

子 Agent 消息不追加到根 `messagesByConversation`。

收到事件时：

- `subagent_status` 创建或更新对应 Run。
- `subagent_message` 按 `message_id` 幂等追加。
- `done` 只表示当前聊天 SSE 完成，不覆盖每个 Specialist 的最终状态。
- SSE 异常或用户停止生成时，把仍为 `running` 的 Run 标记为 `cancelled` 或 `interrupted`。

### 10.2 UI 结构

现有 `delegation` ToolRun 卡片通过 `tool_call_id` 查找对应 `SubagentRun`。

展示建议：

```text
Explorer · sales-source                         运行中
  ├─ 正在定位销售数据
  ├─ execute_sql(purpose=..., sql=...)
  │    └─ 查询完成，返回 128 行
  ├─ write_file(path=...)
  │    └─ 文件写入成功
  └─ 已完成数据探索
```

- 运行中的 delegation 默认展开。
- 已完成的 delegation 默认收起，保留摘要和产物入口。
- 子 Agent 内部继续使用现有消息、工具调用和工具结果配对逻辑。
- 多个并行 delegation 分别维护展开状态和运行状态。
- 工具结果过长时显示截断预览，完整结果通过展开区域或产物文件查看。

## 11. 历史恢复

实时事件不能覆盖页面刷新和重新进入 Conversation 的场景。历史详情从 Specialist Checkpoint 恢复，不新增独立活动日志表。

### 11.1 delegation 边界标记

送入 Specialist 的任务 `HumanMessage.additional_kwargs` 增加私有标记：

```python
{
    "dataagent_delegation_context": {
        "delegation_id": delegation_id,
    }
}
```

Session 身份继续从受控 `RunnableConfig` 获取。内部修正消息使用现有 `dataagent_internal_retry` 标记。

同一个 Session 多次恢复时，Checkpoint 消息顺序为：

```text
delegation A 输入标记
delegation A 的 AI / Tool 消息
delegation B 输入标记
delegation B 的 AI / Tool 消息
```

查询某次 delegation 时，从匹配标记之后读取，遇到下一个 delegation 输入标记时停止。

### 11.2 历史接口

增加按需查询接口：

```text
GET /chat/{conversation_id}/subagents/{analysis_id}/{agent_type}/{session_id}/runs/{delegation_id}/messages
```

接口要求：

- 验证 Conversation 属于当前用户。
- 使用受控 Session 身份读取对应 Checkpoint namespace。
- 只返回当前 delegation 分段内的 `AIMessage` 和 `ToolMessage`。
- 使用与实时 SSE 相同的公开消息转换规则。
- 找不到 Session 或 delegation 时返回明确的 404 业务错误。

前端只在用户展开历史 delegation 卡片时请求详情，避免消息列表接口一次加载所有 Specialist 工具历史。

## 12. 消息转换和数据控制

子 Agent 公开消息复用现有 `MessageResponse` 和 MessagePart：

- `AIMessage.content` -> `TextContent` / `ImageContent`
- `AIMessage.tool_calls` -> `ToolCallPart`
- `ToolMessage` -> `ToolResultPart`

增加以下限制：

- 只转换明确支持的 LangChain 消息类型。
- 不序列化任意 `additional_kwargs`。
- 不发送 `SystemMessage` 和内部 `HumanMessage`。
- 工具参数和结果设置单事件大小上限。
- 超限结果返回截断标记，并优先引导用户查看产物。
- 附件路径继续经过当前 DelegationResult 产物验证，不信任工具结果中的任意路径。

## 13. 文件调整

### Agent 层

- `app/analytics/agents/contracts.py`
  - 增加内部 Subagent Activity 类型和 writer 协议。
- `app/analytics/agents/planner/tools/delegation.py`
  - 传递 delegation tool call ID 和 StreamWriter。
- `app/analytics/agents/planner/tools/list_sessions.py`
  - 提供专业 Session 查询工具。
- `app/analytics/agents/planner/tools/delete_session.py`
  - 提供专业 Session 删除工具。
- `app/analytics/agents/session_service.py`
  - 将 Specialist 调用改为流式执行。
  - 发送消息和状态活动。
  - 写入 delegation 私有边界标记。
  - 提供按 delegation 读取 Session 消息的方法。
- `app/analytics/agents/session_store.py`
  - 按现有 Checkpoint 抽象支持读取消息状态，不暴露 Postgres 细节给上层。

### API 和服务层

- `app/analytics/services/chat.py`
  - 同时处理 Planner updates 和 custom stream。
  - 将内部活动转换为公开 SSE Schema。
  - 提供 Specialist 历史消息转换。
- `app/analytics/api/chat/schemas.py`
  - 增加 `SubagentMessageEvent`、`SubagentStatusEvent` 和历史响应 Schema。
- `app/analytics/api/chat/router.py`
  - 序列化新增 SSE 事件。
  - 增加 Specialist 历史查询接口。

### 前端

- `web/src/stores/chatStore.ts`
  - 增加按 Conversation 和 delegation ID 管理的子 Agent Run 状态。
- `web/src/pages/Chat/index.tsx`
  - 消费新增 SSE 事件。
  - 处理断线和停止生成时的 Run 状态。
- `web/src/pages/Chat/components/ChatMessages.tsx`
  - 在 delegation 工具卡片内展示 Specialist 消息和工具调用。
- `web/src/api/chat.ts`
  - 增加 Specialist 历史查询方法。
- OpenAPI 生成类型
  - 随后端 Schema 重新生成，不手工维护兼容类型。

## 14. 测试计划

### Agent 和 Service

- Specialist `model` 更新被转换为一条 SubagentMessageActivity。
- Specialist 工具调用和结果按原顺序发送。
- 同一消息 ID 不重复发送。
- 内部任务输入和结构化修正消息不发送。
- 多个并行 delegation 使用不同 delegation ID。
- 最终结果仍执行 repair 和 artifact 验证。
- Specialist 异常和取消发送正确状态。
- `runtime.stream_writer` 未配置时不影响 delegation 的正常结果。

### SSE 和 API

- Planner 根消息保持现有 `message` 事件。
- custom 活动转换为 `subagent_message` 和 `subagent_status`。
- 未识别的 custom payload 不返回前端。
- Heartbeat 在 Specialist 长时间运行时继续发送。
- 历史接口只能读取当前用户 Conversation。
- 同一 Session 的多次 delegation 能按边界标记正确分段。
- API 不返回 System Prompt、私有元数据和内部修正消息。

### 前端

- 收到 running 状态后创建并展开对应 delegation 卡片。
- 多个 delegation 的消息不会串线。
- 子 Agent tool call 和 tool result 正确配对。
- 页面停止生成后未完成 Run 显示已中断。
- 展开历史 delegation 时按需加载并去重消息。
- 根 Planner 消息列表顺序保持不变。

## 15. 验收标准

1. Planner 发出 delegation 后，前端能够在 Specialist 完成前看到运行状态。
2. Specialist 的文本消息、工具调用和工具结果实时出现在对应 delegation 卡片中。
3. 并行 Specialist 的消息不会互相混合。
4. Planner 在收到最终 DelegationResult 后继续执行，现有业务结果不变。
5. 刷新页面后展开历史 delegation 可以恢复同一次执行的工作详情。
6. System Prompt、私有消息元数据、内部修正消息和隐藏 reasoning 不出现在 API 中。
7. 用户停止生成或 SSE 断开时，当前同步 Specialist 随 Planner 执行一起取消。
8. 完整测试覆盖实时流、历史恢复、并行隔离和前端展示。

## 16. 异步 delegation 的引入条件

满足以下一个或多个明确需求时，再引入 Deep Agents `AsyncSubAgent`：

- 用户离开页面或 SSE 断开后，Specialist 仍需继续执行。
- Specialist 需要跨多个用户回合运行。
- 用户需要向运行中的 Specialist 追加指令。
- 用户需要单独取消某个 Specialist。
- Planner 需要启动 Specialist 后立即继续其他模型决策。
- Specialist 需要独立部署和扩缩容。

引入异步 delegation 时还需要设计：

- Agent Protocol 服务和 graph 注册。
- 持久化异步任务登记表或等价状态通道。
- 独立于聊天请求的事件订阅接口。
- Conversation 删除时的远程任务取消。
- Planner run 结束后的预算和 repair depth 生命周期。
- Session 锁、工作区租约和任务恢复策略。

本期定义的 `delegation_id`、Subagent Run 前端结构和 SSE 活动类型可以继续使用。未来异步任务增加 `task_id`，并关联到原始 `delegation_id`。

## 17. 参考

- Deep Agents Streaming：https://docs.langchain.com/oss/python/deepagents/streaming
- Deep Agents Async Subagents：https://docs.langchain.com/oss/python/deepagents/async-subagents
- LangGraph Streaming：https://docs.langchain.com/oss/python/langgraph/streaming
