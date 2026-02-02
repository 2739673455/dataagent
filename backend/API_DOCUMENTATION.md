# DeepAgents 项目接口文档

本文档描述了 **DeepAgents 多智能体系统** 的后端服务接口。该系统通过 HTTP 接口接收用户任务，并通过 WebSocket 协议实时反馈多智能体的协作过程、工具调用详情及最终结果。

## 1. HTTP 接口

### 1.1 提交任务
启动一个新的智能体任务。任务将在后台异步执行，实时进度通过 WebSocket 推送。

- **URL**: `/api/task`
- **Method**: `POST`
- **Content-Type**: `application/json`

**请求参数**

| 参数名 | 类型 | 必选 | 描述 |
| :--- | :--- | :--- | :--- |
| `query` | string | 是 | 用户输入的自然语言任务指令 |

**请求示例**

```json
{
    "query": "从网络查询小米汽车的信息，并保存到md文档中"
}
```

**响应参数**

| 参数名 | 类型 | 描述 |
| :--- | :--- | :--- |
| `status` | string | 任务状态，固定为 "started" |
| `thread_id` | string | 任务唯一标识 ID |
| `message` | string | 提示信息 |

**响应示例**

```json
{
    "status": "started",
    "thread_id": "550e8400-e29b-41d4-a716-446655440000",
    "message": "Task started in background. Please connect to WebSocket for updates."
}
```

---

## 2. WebSocket 实时通讯

WebSocket 用于前端实时接收智能体执行过程中的状态反馈、工具调用详情及最终结果。

- **URL**: `/ws`
- **协议**: `ws://<host>:<port>/ws` (本地默认为 `ws://localhost:8000/ws`)

### 2.1 消息通用结构 (Server -> Client)

所有服务端推送的消息均为 JSON 格式，具备以下统一结构：

```json
{
    "type": "monitor_event",
    "event": "事件类型枚举值",
    "message": "人类可读的提示信息",
    "data": {
        // 事件特定的附加数据
    },
    "timestamp": "2024-01-26T12:00:00.000000"
}
```

### 2.2 事件类型定义 (`event` 字段)

以下是 `event` 字段可能出现的枚举值及其对应的 `data` 结构，**前端可根据这些事件类型展示不同的 UI 交互**：

#### (1) `session_created`
**描述**: 任务工作目录创建成功时触发。
**用途**: **提供给前端一个工作目录路径**。前端可记录此路径，便于后续允许用户浏览、查看或下载在此目录下生成的各类文件（如 Word、Markdown、PDF 等）。
**Data 结构**:
```json
{
    "path": "e:\\LLM\\DeepAgents\\0.2\\DeepAgentsProject\\output\\run_20260126_..."
}
```

#### (2) `tool_start`
**描述**: 智能体开始调用某个具体的工具（如搜索、文件写入等）。
**用途**: **用于流式输出时查看进度**。前端可展示当前智能体正在使用的工具及参数，让用户感知系统正在执行具体的操作。
**Data 结构**:
```json
{
    "tool_name": "工具名称 (e.g., Markdown文档生成工具)",
    "args": {
        "filename": "小米汽车.md",
        "content": "..."
    }
}
```

#### (3) `assistant_call`
**描述**: 主智能体（Main Agent）将任务拆解并委托给子智能体（Sub Agent）时触发。
**用途**: **用于流式输出时查看进度**。前端可展示系统正在切换或调用特定的垂直领域助手（如搜索助手、文档助手），体现多智能体协作的过程。
**Data 结构**:
```json
{
    "assistant_name": "子智能体名称 (e.g., 搜索助手, Markdown助手)",
    "args": {
        "任务描述": "具体的子任务指令"
    }
}
```

#### (4) `task_result`
**描述**: 整个任务执行完成，返回最终结果。
**用途**: **提供给用户查看最终反馈**。前端应将此内容展示在对话框中作为 AI 的最终回复，并**标识此次对话（或任务）已结束**。
**Data 结构**:
```json
{
    "result": "任务已完成！我已经成功：\n1. 使用网络搜索...\n2. 生成了文档..."
}
```

#### (5) `error`
**描述**: 执行过程中发生异常。
**用途**: 提示用户任务执行失败。
**Data 结构**: (通常为空，错误详情在 `message` 字段中)
```json
{}
```

#### (6) `System`
**描述**: 系统级通知（如目录创建失败的降级提示等）。
**Data 结构**: (视情况而定，通常包含系统状态信息)

### 2.3 心跳保活 (Client -> Server)
客户端可以发送任意文本消息，服务端将回显 pong 消息以保持连接活跃。

**Client 发送**: `ping`
**Server 响应**: `{"type": "pong", "message": "received: ping"}`
