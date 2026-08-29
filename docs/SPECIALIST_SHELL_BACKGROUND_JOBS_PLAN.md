# Specialist Shell 后台任务实施方案

## 1. 目标

为四类 Specialist Agent 的 Shell `execute` 工具增加“前台等待一段时间，未结束则转为后台任务”的能力。

模型启动命令后可以继续处理其他工作，并在后续模型轮次中查看当前任务、等待结果或主动取消。后台任务完成不会主动触发新的模型调用；模型会在下一次正常调用前获知任务状态。

本方案覆盖：

- Analyst、Explorer、Reviewer、Visualizer 的 Shell 工具。
- 后台任务启动、查询、等待、列举和取消。
- 每次 Specialist 模型调用前的后台任务状态注入。
- Sandbox 容器、Session 删除和 Agent Run 结束时的生命周期处理。
- 子 Agent 活动流中的工具调用与工具结果展示。

## 2. 当前实现

当前四类 Specialist 都在 `app/analytics/agents/skills.py` 中通过：

```python
FilesystemMiddleware(
    backend=resolved_backend,
    tools="all",
    max_execute_timeout=cfg.sandbox.execute_timeout_seconds,
)
```

获得 `execute`。`DockerSandboxBackend._execute_unlocked()` 使用 Linux `timeout --signal=KILL` 执行命令，当前全局硬上限为 120 秒。工具调用会一直等待命令结束；超过 120 秒后进程被强制终止并返回失败结果。

当前 Specialist 的一个 Agent Run 对应一次 delegation 执行：

1. `SpecialistAgentFactory.create()` 创建绑定 Session Sandbox 的 Agent。
2. `AgentSessionService._stream_specialist()` 驱动 Agent 的模型和工具循环。
3. 本次 delegation 返回、失败或取消后，Agent Run 结束。

这个边界适合作为 Shell 后台任务的生命周期边界。

## 3. 核心决策

### 3.1 功能范围

- 只替换 Specialist 的 Shell `execute`。
- Planner 继续只使用只读文件工具，不增加 Shell 执行能力。
- 文件工具、SQL 工具和其他业务工具保持现状。
- 不建设通用异步工具平台。
- 不引入 Celery、异步 delegation 或跨进程任务队列。
- 不实现任务完成后自动回调模型。
- Shell 后台任务不跨 Agent Run 保留；同一个 Specialist Session 的下一次 delegation 不能继续使用上一次的 `job_id`。

### 3.2 执行语义

| ID    | 约束项       | 当前值 | 作用域       | 定义位置               | 处理结果 |
| ----- | ------------ | -----: | ------------ | ---------------------- | -------- |
| SB-09 | 运行容器     |      8 | 部署实例     | `conf/app_config.yaml` | 待定     |
| SB-10 | 容量等待请求 |  2,048 | 部署实例     | `conf/app_config.yaml` | 待定     |
| SB-11 | 容量等待超时 | 300 秒 | 单个等待请求 | `conf/app_config.yaml` | 待定     |

```text
命令在 wait_seconds 内结束
  → 直接返回最终结果

命令在 wait_seconds 后仍在运行
  → 命令继续在后台运行
  → 返回 job_id 和当前状态
  → Specialist 可以继续调用其他工具
```

`wait_seconds` 只控制本次工具调用等待多久，不是命令总执行时限，不会因为等待时间结束而终止命令。

后台 Shell 命令不设置固定总运行时限。它仍受 Docker 容器的 CPU、内存、PID、工作区容量以及 Agent Run 生命周期约束。

### 3.3 任务可见性

Shell Job Registry 由当前 Specialist Agent Run 独占。每次模型调用前，中间件读取 Registry，并把以下信息临时附加到本次模型请求的系统级指令内容中：

- 正在运行或正在取消的任务。
- 已结束但模型尚未查看最终结果的任务。

这些内容只存在于本次发给模型的请求中：

- 不写入 HumanMessage、AIMessage 或 ToolMessage。
- 不写入 LangGraph Checkpoint。
- 不通过 SSE 发送给前端。
- 不改变原始 System Prompt 常量。

后台任务完成只更新 Registry。它不会主动发起模型调用，模型会在下一次正常模型调用时看到新的状态。

## 4. 工具接口

### 4.1 `execute`

```python
execute(command: str, wait_seconds: float = 10) -> ShellJobResult
```

行为：

1. 创建 `job_id` 并立即启动命令。
2. 等待命令结束，最长等待 `wait_seconds`。
3. 在等待期内结束时，返回最终状态、退出码和命令输出，任务视为已查看。
4. 等待期结束后仍在运行时，返回后台任务句柄，任务继续执行。

前台完成示例：

```json
{
  "job_id": "job_7f3a9c12",
  "status": "completed",
  "exit_code": 0,
  "output": "done\n",
  "output_path": "large_tool_results/shell_jobs/job_7f3a9c12.log",
  "output_truncated": false
}
```

转入后台示例：

```json
{
  "job_id": "job_7f3a9c12",
  "status": "running",
  "started_at": "2026-08-29T09:30:00+00:00",
  "elapsed_seconds": 10.0,
  "output_path": "large_tool_results/shell_jobs/job_7f3a9c12.log"
}
```

命令从启动开始就把 stdout 和 stderr 合并写入日志文件。后台运行期间也可以通过 `read_file` 查看已经产生的部分输出。

前台完成结果保持当前 `execute` 的模型使用体验。小结果直接返回；大结果复用现有的大工具结果落盘规则，避免把大量输出直接塞入模型上下文。

### 4.2 `list_shell_jobs`

```python
list_shell_jobs(include_reviewed: bool = False) -> list[ShellJobSummary]
```

默认返回：

- `running`
- `cancelling`
- `completed`、`failed`、`cancelled`、`interrupted` 中尚未查看最终结果的任务

默认不返回已经查看过最终结果的终态任务。`include_reviewed=true` 时返回当前 Agent Run 中仍保留的全部任务记录。

每项包含：

```json
{
  "job_id": "job_7f3a9c12",
  "status": "running",
  "command": "python report.py",
  "started_at": "2026-08-29T09:30:00+00:00",
  "finished_at": null,
  "elapsed_seconds": 42.3,
  "exit_code": null,
  "output_path": "large_tool_results/shell_jobs/job_7f3a9c12.log",
  "output_truncated": false,
  "reviewed_at": null
}
```

调用 `list_shell_jobs` 只读取状态，不会把任何任务标记为已查看。

### 4.3 `get_shell_job`

```python
get_shell_job(job_id: str, wait_seconds: float = 0) -> ShellJobResult
```

行为：

- 任务已经结束时立即返回最终结果，并标记为已查看。
- 任务仍在运行且 `wait_seconds=0` 时立即返回当前状态。
- 任务仍在运行且 `wait_seconds>0` 时，最多等待指定时间。
- 等待期间结束时返回最终结果并标记为已查看。
- 等待结束后仍在运行时返回 `running`，不标记为已查看。
- `job_id` 不属于当前 Agent Run 时返回明确的 `job_not_found` 错误。

`get_shell_job` 同时承担“立即查看”和“等待单个任务”的职责，因此不再增加 `wait_shell_jobs`。两个独立等待接口会造成语义重叠。

对于已经转入后台的任务，最终结果以状态、退出码和 `output_path` 为主。模型使用 `read_file` 按需读取日志，避免一次把超大日志送入上下文。

### 4.4 `cancel_shell_job`

```python
cancel_shell_job(job_id: str) -> ShellJobResult
```

行为：

1. 向命令所属进程组发送 `SIGTERM`。
2. 等待一个很短的内部退出宽限期。
3. 进程组仍未退出时发送 `SIGKILL`。
4. 已确认结束时返回 `cancelled` 并标记为已查看。
5. 取消请求仍在处理中时返回 `cancelling`，继续保留在默认列表中。
6. 任务在取消请求前已经自然结束时，返回真实终态并标记为已查看。

取消整个进程组，避免只杀掉父 Shell 后留下子进程。

## 5. 状态和已查看规则

### 5.1 状态集合

| 状态          | 类型   | 含义                                                   |
| ------------- | ------ | ------------------------------------------------------ |
| `running`     | 运行态 | 命令仍在执行                                           |
| `cancelling`  | 运行态 | 已请求取消，等待进程组退出                             |
| `completed`   | 终态   | 命令正常结束且退出码为 0                               |
| `failed`      | 终态   | 命令结束且退出码非 0，或启动失败                       |
| `cancelled`   | 终态   | 由当前 Agent Run 主动取消                              |
| `interrupted` | 终态   | 容器丢失、运行时关闭或其他外部原因导致结果无法继续跟踪 |

不把“超过前台等待时间”定义为失败状态；它只会让任务从前台等待转为后台运行。

### 5.2 状态流转

```text
start
  ├─ 启动失败 ───────────────────────────────→ failed
  └─ 启动成功 → running
                 ├─ exit_code = 0 ───────────→ completed
                 ├─ exit_code != 0 ──────────→ failed
                 ├─ cancel request → cancelling ─→ cancelled
                 └─ container/runtime lost ──→ interrupted
```

### 5.3 已查看规则

任务是否已查看由独立的 `reviewed_at` 字段记录，不与执行状态混在一起。

| 场景                                 | 是否标记已查看           |
| ------------------------------------ | ------------------------ |
| `execute` 在前台等待期内返回终态     | 是                       |
| `execute` 返回 `running`             | 否                       |
| `list_shell_jobs` 展示任务           | 否                       |
| 模型请求中的自动状态附加信息展示任务 | 否                       |
| `get_shell_job` 返回运行态           | 否                       |
| `get_shell_job` 返回终态             | 是                       |
| `cancel_shell_job` 返回确认后的终态  | 是                       |
| 后台任务自行结束                     | 否，直到模型获取最终结果 |

终态且已查看的任务只在 `include_reviewed=true` 时展示。任务记录在当前 Agent Run 结束时统一清理。

## 6. 模型请求中的任务状态

新增 `ShellJobContextMiddleware`，在每次 Specialist 模型调用前读取当前 Registry，生成紧凑的结构化信息，并通过 `request.override(...)` 只修改本次模型请求副本。

模型看到的附加内容示例：

```xml
<shell_jobs>
{"running":[
  {"job_id":"job_7f3a9c12","status":"running","started_at":"2026-08-29T09:30:00+00:00","elapsed_seconds":42.3,"output_path":"large_tool_results/shell_jobs/job_7f3a9c12.log"}
],"finished_unreviewed":[
  {"job_id":"job_91bc45de","status":"completed","exit_code":0,"finished_at":"2026-08-29T09:31:06+00:00","output_path":"large_tool_results/shell_jobs/job_91bc45de.log","output_truncated":false}
]}
</shell_jobs>
```

注入规则：

- 没有运行中或未查看任务时不添加该区块。
- 只包含任务状态和定位信息，不包含日志正文。
- `running` 数组包含 `running` 和 `cancelling`。
- `finished_unreviewed` 只包含尚未查看的终态任务。
- 使用 UTC ISO 8601 时间。
- 使用服务端生成的数据，按系统级可信信息处理。
- 该区块不会出现在 Checkpoint 和前端消息中。

四类 Specialist 的 Prompt 同步增加以下行为要求：

- `execute` 返回 `running` 后，该任务仍由当前 Agent 负责。
- 可以先继续其他工作，再调用 `get_shell_job` 等待或查看结果。
- 不确定有哪些任务时调用 `list_shell_jobs`。
- 不再需要的任务调用 `cancel_shell_job`。
- 返回最终 `SpecialistResult` 前，必须处理所有运行中任务，并查看所有需要用于结论的终态结果。

## 7. 底层执行设计

### 7.1 组件边界

```text
Specialist Agent
  ├─ Shell tools
  │    └─ ShellJobRuntime（本次 Agent Run 的 Registry 和协调逻辑）
  │          └─ DockerSandboxBackend Shell Job 原语
  └─ ShellJobContextMiddleware
       └─ 读取同一个 ShellJobRuntime
```

职责划分：

- `DockerSandboxBackend`：负责容器内启动、观察和终止进程，处理日志文件和 Sandbox 生命周期保护。
- `ShellJobRuntime`：负责 `job_id`、状态、等待事件、已查看状态和 Agent Run 清理。
- Shell tools：只负责参数校验和把 Runtime 结果转换为模型可读结果。
- `ShellJobContextMiddleware`：只负责生成本次模型请求所需的状态区块。

Registry 不直接保存 Docker SDK 对象，只保存内部执行句柄、状态和时间等可控数据。模型只能接触服务端生成的 `job_id`，不能接触 Docker exec ID 或容器 ID。

### 7.2 容器内命令包装

每个任务使用一个受控包装进程：

1. 在当前 Session 工作目录中执行命令。
2. 使用 `setsid` 创建独立进程组。
3. 合并 stdout 和 stderr。
4. 一边写入任务日志，一边持续排空超过保留上限的输出，避免管道写满导致业务命令阻塞。
5. 记录进程组 PID、真实退出码和输出是否截断。
6. 包装进程最终使用业务命令的退出码退出。

日志路径固定为当前 Session 下：

```text
large_tool_results/shell_jobs/<job_id>.log
```

控制文件放在 Sandbox staging 区中，不通过模型工具公开。实际路径由 Backend 使用当前 Conversation 和 Session Linux UID 生成，不返回给模型：

```text
/workspace/.dataagent-staging/<conversation_id>/<session_uid>/shell_jobs/<job_id>.json
```

控制文件至少记录进程组 PID、启动状态和退出状态。所有路径都由服务端根据 `job_id` 生成，不接受模型传入任意日志路径或控制路径。

### 7.3 输出容量

任务日志复用现有 `sandbox.max_file_bytes`，当前为 100 MiB。不增加独立的 Shell 输出大小配置。

当输出超过 100 MiB 时：

- 日志保留前 100 MiB。
- 后续输出继续被读取并丢弃，保证命令可以正常结束。
- `output_truncated=true`。
- 退出码仍以业务命令的真实退出码为准。

日志同时受 Conversation 工作区总容量约束。启动任务前沿用当前工作区容量检查；任务结束后再次检查并在结果中报告容量超限。工作区硬配额能力保持现状。

### 7.4 Sandbox operation 生命周期

后台命令运行期间必须持续持有当前用户和 Conversation 的 Sandbox operation：

- Docker ownership operation 租约持续续期。
- 用户和 Conversation 的 `LifecycleGuard.operation()` 保持占用。
- 空闲清理无法在任务运行时停止容器。
- Conversation 或 Session 删除会等待当前 Agent Run 先完成清理。

实现上由每个后台任务的监控执行单元从启动到终态一直持有 operation。前台 `execute` 返回 `running` 时只结束工具等待，不结束监控执行单元和 operation。

当前预期并发规模较小，可以为每个运行任务保留一个异步监控任务及一个阻塞 Docker 监控线程。此处不增加人为的任务数量上限；现有容器 PID、CPU、内存和工作区约束继续生效。

### 7.5 Shell 硬超时配置调整

`sandbox.execute_timeout_seconds` 当前同时承担模型 Shell 命令和 Sandbox 内部辅助命令的硬超时。新增后台任务后拆分职责：

- Specialist Shell Job 不使用固定总超时。
- 将配置重命名为 `sandbox.internal_command_timeout_seconds`，只用于 `du`、限长文件读取、内部编辑脚本和产物存在性检查等同步辅助命令。
- `DockerSandboxBackend.execute/aexecute` 继续作为内部辅助执行原语，不再直接暴露为 Specialist 的 `execute` 工具。
- 同步更新 `docs/CURRENT_BUDGET_CONSTRAINTS.md`，移除“Shell 执行超时 120 秒”的描述；如仍需登记该配置，应明确写成“Sandbox 内部辅助命令超时”。

项目仍处于开发阶段，直接修改配置字段和所有调用方，不保留旧字段别名或兼容读取。

## 8. Agent Run 生命周期

### 8.1 正常结束

Specialist 准备返回最终结果时，Prompt 要求其先处理运行中任务。Runtime 在 Agent Run 结束阶段仍执行兜底清理：

1. 对所有 `running` 和 `cancelling` 任务发起取消。
2. 等待监控执行单元确认任务结束。
3. 将无法确认正常退出的任务记为 `interrupted` 并记录日志。
4. 释放所有 Sandbox operation。
5. 清空当前 Registry。

已经结束的日志文件保留在 Session 工作区中，供后续产物追溯；仅清除内存任务记录和内部控制文件。

### 8.2 Agent Run 失败或取消

模型异常、工具异常、结构化输出失败、上游取消和 SSE 断开都走同一套 `finally` 清理。清理本身使用受控的短时间终止流程，避免取消信号导致子进程遗留。

`AgentSessionService.execute_delegation()` 必须在释放 Session 锁和从 `_active_sessions` 移除记录前完成 Shell Job Runtime 清理。

### 8.3 API 进程异常退出

Registry 不跨进程持久化，API 进程被强制终止时无法执行正常 `finally`。Docker exec 进程可能暂时继续运行。现有运行时关闭、容器空闲停止和容器重建最终会终止这类进程。

第一版不为这一场景增加 Redis Job Registry。需要进一步提高进程崩溃后的即时回收能力时，可以给命令包装进程增加 Agent Run 租约文件和 Sandbox 启动时的孤儿扫描；这不属于本次实施范围。

## 9. 前端和活动流

现有 `AgentSessionService._stream_specialist()` 已经把 Specialist 的 AIMessage 和 ToolMessage 投影为子 Agent 活动。新增工具会自然出现在现有活动流中：

- `execute` 的调用参数和前台完成/后台句柄结果。
- `list_shell_jobs` 的调用和任务列表。
- `get_shell_job` 的调用和任务状态。
- `cancel_shell_job` 的调用和取消结果。

本次不新增 SSE 事件类型，也不修改前端消息结构。前端沿用现有工具调用组件展示工作细节。

模型请求前临时附加的 `<shell_jobs>` 区块不进入消息流，因此前端不会展示它。

## 10. 代码调整范围

### 10.1 Sandbox 层

`app/sandbox/backend.py`

- 增加启动、监控和取消 Shell Job 的底层原语。
- 增加受控进程组与日志捕获包装。
- 后台任务持续持有 `_operation()`。
- 将现有模型 Shell 硬超时与内部辅助命令超时分离。

`app/sandbox/scripts.py`

- 增加 Shell Job 包装脚本或脚本生成逻辑。
- 负责日志限长、输出排空、PID 和退出状态写入。

`app/shared/config/app_config.py`、`conf/app_config.yaml`

- `execute_timeout_seconds` 重命名为 `internal_command_timeout_seconds`。
- 不增加 Shell Job 总时限、最大任务数或独立输出容量配置。

### 10.2 Agent 层

新增 `app/analytics/agents/shell_jobs.py`

- `ShellJobStatus`、`ShellJobRecord` 和公开结果结构。
- `ShellJobRuntime`。
- 等待事件、状态刷新、reviewed 语义和 Run 结束清理。
- `ShellJobContextMiddleware`。

新增 `app/analytics/agents/tools/shell.py`

- `execute`
- `list_shell_jobs`
- `get_shell_job`
- `cancel_shell_job`

新增 `app/analytics/agents/tools/__init__.py`，统一导出 Specialist 通用工具构造函数。

`app/analytics/agents/skills.py`

- `FilesystemMiddleware` 的工具列表改为显式文件工具：`ls`、`read_file`、`write_file`、`edit_file`、`delete`、`glob`、`grep`。
- 移除内置 `execute`。
- 移除 `max_execute_timeout` 参数。

`app/analytics/agents/specialists.py`

- `SpecialistAgentFactory.create()` 在拿到 Session Backend 后创建本次 Run 的 `ShellJobRuntime`。
- 将 Shell 工具和中间件显式传给 Specialist 构造器。
- 新增 `SpecialistRun`，同时持有编译后的 Agent 和 `ShellJobRuntime`；Factory 返回该对象，避免 Runtime 被隐藏在 Agent 图内部。
- 将 `list_shell_jobs`、`get_shell_job`、`cancel_shell_job` 加入内置保留工具名检查。

四个 `app/analytics/agents/*/agent.py`

- 接收同一个 `ShellJobRuntime`。
- 把四个 Shell 工具加入 Agent tools。
- 把 `ShellJobContextMiddleware` 加入 middleware。
- 移除对 `cfg.sandbox.execute_timeout_seconds` 的依赖。

四个 `app/analytics/agents/*/prompt.py`

- 增加后台任务使用和结束前清理要求。

`app/analytics/agents/session_service.py`

- `_invoke_specialist()` 在结构化结果和修正轮次中始终复用同一个 `SpecialistRun`。
- 在包围完整 Specialist 执行过程的 `finally` 中调用 `SpecialistRun.shell_jobs.close()`，等待 Runtime 完成兜底清理。
- 确保清理完成后再释放 Session 锁。

### 10.3 文档

`docs/05_SANDBOX.md`

- 更新 Shell 命令执行、后台任务、进程组取消和生命周期说明。

`docs/04_ANALYTICS.md`

- 增加 Specialist Shell Job 工具和模型状态附加信息。

`docs/CURRENT_BUDGET_CONSTRAINTS.md`

- 删除 Specialist Shell 的 120 秒硬时限。
- 根据最终配置保留或重命名 Sandbox 内部辅助命令超时项。

## 11. 并发和一致性

- Registry 使用 `threading.RLock` 保护快照和状态提交，使异步工具、Docker 监控线程以及同步/异步模型中间件读取同一份一致状态。
- 每个任务拥有独立的完成事件，`execute` 和 `get_shell_job` 等待同一个事件。
- 工具等待被取消时不能连带取消后台监控任务；使用独立 Task 并通过 `asyncio.shield()` 等待。
- `get`、自然完成和 `cancel` 可能并发发生，终态只能写入一次。
- 取消与自然结束竞态时优先采用实际已观察到的命令终态；只有取消信号确实导致进程结束时标记 `cancelled`。
- `reviewed_at` 只在返回终态结果的工具调用提交结果时写入。
- 每个 `job_id` 只在当前 Runtime 中解析，无法访问其他用户、Conversation、Session 或 Agent Run 的任务。

## 12. 错误处理

| 场景                           | 对外结果                                         |
| ------------------------------ | ------------------------------------------------ |
| 命令为空或 `wait_seconds<0`    | 工具参数校验错误                                 |
| Docker exec 创建失败           | `failed`，包含安全化错误信息                     |
| Job 不属于当前 Run             | `job_not_found`                                  |
| 容器在运行中消失               | `interrupted`                                    |
| 命令退出码非 0                 | `failed`，保留真实退出码和日志路径               |
| 日志超过单文件上限             | 保留真实执行状态，同时 `output_truncated=true`   |
| 工作区容量超限                 | 拒绝新任务或在终态结果中报告超限                 |
| 取消时任务已自然结束           | 返回实际终态                                     |
| Agent Run 清理无法确认任务退出 | 记录错误并标记 `interrupted`，继续释放运行时资源 |

返回给模型的错误信息隐藏容器真实工作目录、Docker exec ID、容器 ID 和宿主机路径。

## 13. 测试计划

### 13.1 ShellJobRuntime 单元测试

- 命令在默认等待时间内成功结束。
- 命令在等待时间内以非 0 退出。
- 命令超过等待时间后返回 `running`，后台继续执行。
- `wait_seconds=0` 立即返回。
- `get_shell_job` 等待后获得终态。
- `get_shell_job` 超时返回运行态。
- 默认 list 只包含运行中和终态未查看任务。
- `include_reviewed=true` 包含已查看任务。
- list 和模型状态注入不会改变 `reviewed_at`。
- get 返回终态后设置 `reviewed_at`。
- cancel 终止整个进程组。
- cancel 与自然完成竞态保持单一真实终态。
- Agent Run cleanup 取消所有未结束任务。
- 工具等待协程被取消后后台监控仍可被 cleanup 回收。

### 13.2 Middleware 单元测试

- 无活跃任务时不修改模型请求。
- 正确区分 `running` 和 `finished_unreviewed`。
- 不注入已查看的终态任务。
- 注入内容不包含命令输出。
- 只修改请求副本，不修改消息和 Checkpoint State。
- 同步和异步模型调用路径行为一致。

### 13.3 Sandbox 集成测试

- stdout/stderr 正确合并到 Session 日志。
- 超过日志上限后命令仍能结束，且 `output_truncated=true`。
- 后台命令可以在下一次工具调用中观察到。
- 取消父 Shell 时子进程同时退出。
- 任务运行超过 `idle_stop_seconds` 时容器不会被空闲清理停止。
- Session 删除会等待任务清理，不会与工作区删除竞态。
- Container 被外部停止后任务转为 `interrupted`。
- 日志和控制文件权限属于当前 Session Linux 身份。
- 其他 Session 无法读取控制文件或操作 job ID。

### 13.4 Agent 和活动流测试

- 四类 Specialist 都拥有四个 Shell 工具。
- Planner 不拥有这些工具。
- 原内置 `execute` 不再注册，工具名没有冲突。
- `execute`、`get`、`list`、`cancel` 的 ToolMessage 通过现有子 Agent 活动流发送。
- `<shell_jobs>` 临时区块不进入 SSE 和 Checkpoint。
- Specialist 返回或异常后没有遗留运行进程。
- 结构化结果修正轮次复用同一个 Runtime。

## 14. 实施顺序

1. 在 Sandbox Backend 实现进程组启动、日志捕获、状态检查和取消原语。
2. 实现 `ShellJobRuntime`、状态模型和 Run 清理。
3. 实现四个 Shell 工具及参数/返回结构。
4. 实现 `ShellJobContextMiddleware`。
5. 从 `FilesystemMiddleware` 移除内置 `execute`，接入四个 Specialist。
6. 在 `AgentSessionService` 补齐 `finally` 清理顺序。
7. 更新四类 Specialist Prompt。
8. 完成单元测试、Docker 集成测试和活动流回归测试。
9. 更新 Sandbox、Analytics 和预算约束文档。

每一步完成后运行对应测试；接入四类 Specialist 后再执行完整的 Analytics 与 Sandbox 测试集。

## 15. 验收标准

- `execute("sleep 30; echo done")` 默认约 10 秒返回 `running`，命令没有被终止。
- 模型下一次调用前能看到该任务仍在运行。
- 任务结束后，模型下一次调用前能看到 `finished_unreviewed`。
- `get_shell_job(job_id)` 能返回终态和日志路径，并使该任务从默认 list 中消失。
- `list_shell_jobs(include_reviewed=true)` 仍能在当前 Agent Run 内看到它。
- `cancel_shell_job(job_id)` 能终止命令及其子进程。
- Specialist 的最终消息、Checkpoint 和前端消息中不包含临时 `<shell_jobs>` 区块。
- 子 Agent 活动区可以看到 Shell 工具的调用参数和结果。
- 命令运行超过当前 120 秒后仍可继续，不再因 Specialist Shell 固定时限被杀死。
- Agent Run 正常结束、异常结束或被取消后均无后台进程遗留。
- Planner 的工具集合和行为保持不变。
