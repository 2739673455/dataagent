# 01. Shared 模块职责与实现

`shared` 提供没有单一业务归属的配置、外部客户端、数据库基础、错误协议、可观测性和 Celery 运行设施。

## 模块职责与边界

`shared` 是后端各业务模块共同依赖的基础层。它负责把配置、数据库和外部服务连接、跨模块值对象、错误响应、日志上下文与异步任务运行方式标准化，使上层模块可以专注于业务规则。

`shared` 不实现账号、元数据、查询、对话、沙箱或注销业务，也不保存这些模块的业务状态。业务模块通过 `shared` 提供的稳定基础能力访问外部系统，并在自己的 Service 和 Repository 中维护业务一致性。

主要使用者是 FastAPI 组合根、Celery Worker，以及 `identity`、`metadata`、`sandbox`、`query`、`assistant` 和 `workflows`。终端用户通常不会直接感知该模块，只有任务状态查询和统一错误响应会直接出现在 HTTP 接口中。

## 功能清单

```text
Shared
→ 加载和校验应用配置
→ 管理外部客户端生命周期
→ 提供数据库基础和跨模块契约
→ 统一 HTTP 错误
→ 统一日志和 Trace 上下文
→ 路由和执行后台任务
→ 查询后台任务状态
```

## 1. 加载和校验应用配置

**实现目的**

让所有进程使用同一份经过类型校验的运行参数，并在进程启动阶段发现缺失配置、拼写错误和互相矛盾的限制，避免错误配置进入请求处理或后台任务阶段。

**使用者与使用方式**

- FastAPI、Celery Worker 和 Celery Beat 在导入应用时读取全局 `cfg`。
- 运维或开发人员通过 `conf/.env` 提供密钥和部署差异，通过 `conf/app_config.yaml` 配置业务无关的运行参数。
- 各模块读取自己的强类型配置段，不直接解析 YAML 或环境变量。

**具体实现**

```text
进程启动
→ 读取 conf/.env
→ 读取 conf/app_config.yaml
→ 解析环境变量插值
→ 使用 Pydantic 构造 Cfg
→ 拒绝根配置和嵌套配置中的未知字段
→ 校验每个配置项的类型和范围
→ 校验跨配置约束
  → Celery soft timeout 小于 hard timeout
  → sandbox 单文件上限不超过工作区上限
  → idle_stop 小于 idle_remove
  → 默认模型和各 Specialist 引用已声明模型
  → 模型 Profile 只配置 image_inputs、structured_output 和 max_input_tokens
  → Responses 模型使用 LangChain OpenAI 原生客户端或 DeepSeek 专属适配
  → OpenRouter 模型使用 ChatOpenRouter 原生客户端
  → 工厂只为支持图片输入的 Responses 模型派生 image_tool_message
→ 配置无效时阻止进程启动
```

配置覆盖 PostgreSQL、Doris、Elasticsearch、Embedding、认证、查询、元数据索引、Celery、生命周期、沙箱、模型、Agent 和 MCP。每个聊天模型显式声明 `chat_completions` 或 `responses` 协议，调用失败时不自动切换协议。数据库密码、JWT 密钥、Embedding 密钥、模型密钥、Redis/Celery URL 以及 MCP URL、Header 和进程环境值使用 `SecretStr` 保存，仅在外部客户端边界解包。


### 设计细节：配置在模块导入时完成解析并形成不可缺项的对象图

配置入口只有 `_load_config()`。它按固定顺序加载 `.env`、解析 YAML 插值，再把完整结果交给根模型 `Cfg`：

```python
def _load_config() -> Cfg:
    """从 .env 和 app_config.yaml 加载配置。"""
    dotenv.load_dotenv(CONFIG_DIR / ".env")
    loaded_cfg = OmegaConf.load(CONFIG_FILE)
    primitive_cfg = OmegaConf.to_container(loaded_cfg, resolve=True)
    return Cfg.model_validate(cast(dict[str, Any], primitive_cfg))


cfg = _load_config()
```

这里的执行顺序决定了配置行为：

1. `load_dotenv()` 把 `conf/.env` 中的值补入进程环境。操作系统已经提供的同名环境变量优先，部署平台可以覆盖本地文件。
2. `OmegaConf.to_container(..., resolve=True)` 立即解析 YAML 中的 `${oc.env:JWT_SECRET}` 等插值。缺少环境变量时在这一阶段失败，不会把未解析字符串传给客户端。
3. `Cfg.model_validate()` 递归构造所有子配置。类型、数值范围、联合类型分支和跨字段约束在这里一次执行。
4. 模块级 `cfg = _load_config()` 使 API 进程和 Celery Worker 都在导入配置模块时完成校验。任何错误都会阻止进程继续创建数据库连接、任务队列或模型客户端。

`cfg` 是进程生命周期内共享的启动配置快照。项目没有运行时修改或热加载入口，修改 YAML 或环境变量后需要重启进程。

根配置还校验跨配置引用。专业 Agent 必须完整配置，且每个 Agent 选择的模型必须存在：

```python
@model_validator(mode="after")
def validate_agent_models(self) -> "Cfg":
    required_specialists = set(AGENT_TYPES)
    configured_specialists = set(self.agent.specialists)
    missing = sorted(required_specialists - configured_specialists)
    if missing:
        raise ValueError("agent.specialists 缺少配置: " + ", ".join(missing))
    for agent_type, specialist in self.agent.specialists.items():
        if specialist.model not in {"default", *self.lm_config.models}:
            raise ValueError(
                f"agent.specialists.{agent_type}.model 引用了未知模型: "
                f"{specialist.model}"
            )
    return self
```

这项校验解决的是引用完整性：`assistant` 创建运行时时可以直接按名称取模型，无需在每次委派时处理“Agent 未配置”或“模型名称不存在”。

MCP 配置使用 `transport` 作为判别字段。选中 `stdio` 后必须提供 `command`，选中 `sse` 后必须提供 `url`，其他传输方式的字段不会混入当前分支：

```python
MCPCfg = Annotated[
    SSEMCPCfg | StdioMCPCfg | WebsocketMCPCfg | StreamableHttpMCPCfg,
    Field(discriminator="transport"),
]

class Cfg(AppConfigModel):
    mcp: dict[str, MCPCfg]
```

所有配置模型的 `extra="forbid"` 负责捕获拼写错误；`SecretStr` 负责避免密码和密钥随对象打印或校验错误直接泄露。秘密值只在创建外部客户端时调用 `get_secret_value()` 解包。模型 `params` 还禁止覆盖 `model`、`base_url`、`api_protocol` 和 `timeout` 等显式字段，确保一个行为只有一个配置来源。

## 2. 管理外部客户端生命周期

**实现目的**

统一连接池的创建、复用和关闭顺序，防止每个业务模块自行创建客户端造成连接泄漏、跨事件循环复用或 Celery fork 后连接失效。

**使用者与使用方式**

- 组合根在应用 lifespan 中初始化和关闭各 Client Manager。
- Repository 和 Service 通过 Manager 获取短生命周期 Session、Connection 或客户端。
- Celery 任务在 Worker 进程中初始化本任务需要的客户端。

**具体实现**

```text
FastAPI lifespan 启动
→ 初始化认证、元数据和助手 PostgreSQL manager
→ 初始化 Doris 管理连接和角色查询连接 registry
→ 初始化 Elasticsearch 和 Embedding client
→ 初始化 LangGraph PostgreSQL
→ 上层初始化 sandbox 和 AgentManager

请求或 Service 使用外部资源
→ 从 manager 获取 request-scoped session 或 connection
→ Service 通过 session.begin() 明确事务范围
→ context manager 结束时关闭 Session 或释放连接

应用关闭
→ 先关闭 Agent 和 sandbox
→ 再关闭 LangGraph、Embedding、Elasticsearch
→ 关闭 PostgreSQL 和 Doris 连接池
```

Celery Worker 在任务进程中重新初始化所需客户端，不复用 fork 前连接。


### 设计细节：数据库 Manager 只管理连接生命周期，事务由用例持有

PostgreSQL Manager 创建连接池和 `AsyncSession`，并把三个数据域绑定到不同的 SQLAlchemy Base。`get_session()` 只负责关闭请求级 Session，不会隐式提交业务事务；Service 或 Repository 调用方必须显式使用 `session.begin()`，从而让事务边界与业务用例边界一致。

```python
def init(self) -> None:
    self._engine = create_async_engine(
        self._url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=30,
    )
    self._session_maker = async_sessionmaker(
        self._engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

async def get_session(self) -> AsyncIterator[AsyncSession]:
    async with self.session() as db_session:
        yield db_session
```

`pool_pre_ping` 在借出连接前检查连接有效性，`pool_recycle` 避免长期连接超过数据库或网络设备的存活时间。`expire_on_commit=False` 允许 Service 在事务提交后读取刚写入对象，但不会让 ORM 对象跨请求继续承担数据访问职责。

## 3. 提供数据库基础和跨模块契约

**实现目的**

明确不同 PostgreSQL 数据域的模型归属，并为跨模块调用提供与 ORM、Repository 解耦的数据结构，避免业务模块直接依赖其他模块的持久化实现。

**使用者与使用方式**

- 各模块的 SQLAlchemy Model 继承所属数据域的声明基类。
- 模块间传递 Agent Session、资产标识、Doris 限制、检索结果和查询经验时使用 `app/shared/contracts` 中的值对象。
- 新增跨模块数据时，只在确实有多个模块共同使用时放入 `shared`。

**具体实现**

```text
SQLAlchemy 模型选择 Base
→ identity 使用 AuthBase
→ metadata、语义召回快照和 query 使用 MetaBase
→ assistant 使用 AssistantBase
→ LangGraph 使用自身 schema

跨模块传递数据
→ Agent 身份和 Session 使用 shared/contracts/analysis.py
→ 查询经验召回结果使用 shared/contracts/query_experience.py
→ 契约只包含消费者需要的字段
→ 契约不暴露 ORM 和 Repository
```

跨 PostgreSQL 数据域只通过稳定 ID 关联，不建立跨数据库外键。


### 设计细节：跨模块标识同时约束持久化和沙箱命名空间

`AgentSessionKey` 是 Assistant、Query 和 Sandbox 共用的会话标识。标识符只能使用受控字符，并从同一对象生成 LangGraph namespace，避免各模块分别拼接路径后产生歧义。

```python
@dataclass(frozen=True, slots=True)
class AgentSessionKey:
    user_id: int
    conversation_id: UUID
    analysis_id: str
    agent_type: AgentType
    session_id: str

    def __post_init__(self) -> None:
        if isinstance(self.user_id, bool) or self.user_id <= 0:
            raise ValueError("user_id 必须为正整数")
        _validate_identifier(self.analysis_id, "analysis_id")
        validate_agent_type(self.agent_type)
        _validate_identifier(self.session_id, "session_id")

    @property
    def checkpoint_ns(self) -> str:
        return f"subagents/{self.analysis_id}/{self.agent_type}/{self.session_id}"
```

该对象是不可变值对象。跨模块传递时只携带定位一次专业分析所需的字段，不携带 ORM Session、Docker Container 或 LangGraph Checkpointer。

## 4. 统一 HTTP 错误

**实现目的**

为前端和其他调用方提供稳定、可机器处理的错误结构，同时避免未处理异常泄露数据库、文件路径、密钥或服务内部细节。

**使用者与使用方式**

- 业务 Service 通过继承 `ProblemError` 定义可公开的错误类型和状态码。
- FastAPI 路由无需重复拼装错误响应，由全局异常处理器统一转换。
- HTTP 调用方根据 `type`、`status`、`detail` 和扩展字段展示或处理错误。

**具体实现**

```text
业务 Service 抛出 ProblemError
→ FastAPI 异常处理器读取 type、title、status、detail 和 extensions
→ 输出 application/problem+json

请求参数校验失败
→ 转换 Pydantic/FastAPI 错误
→ 返回结构化字段位置和原因

出现未处理异常
→ 服务端日志记录完整堆栈
→ 客户端只收到安全的通用错误
```

Agent 工具错误属于模型工具协议，由各工具保留异常类别、字段位置和可修正详情。


### 设计细节：HTTP 错误公开信息与服务端诊断信息分离

业务异常先转换为 RFC 9457 响应。未捕获异常仍记录完整堆栈，但响应只使用通用 `ProblemError`，避免将 SQL、文件路径或第三方客户端错误原样返回。

```python
def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    problem = ProblemError()
    _log_problem(problem, exc)
    return _build_response(request, problem)


def _build_response(
    request: Request,
    exc: ProblemError,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=exc.to_problem(instance=request.url.path),
        headers=headers,
        media_type="application/problem+json",
    )
```

限流异常可通过扩展字段携带 `retry_after_seconds`，全局处理器会同步设置 `Retry-After` 响应头。参数错误会被投影为稳定的 `type/location/message` 列表。

## 5. 统一日志和 Trace 上下文

**实现目的**

让一次请求及其后续 Agent、工具和后台任务日志能够按同一组标识检索，降低跨模块、跨协程问题的定位成本。

**使用者与使用方式**

- HTTP 中间件为请求创建或继承 Trace 标识。
- Chat 路由在已解析用户后补充 `user_id` 上下文。
- 业务代码把 Conversation、Analysis、Session 和 Tool Call 标识写入具体日志消息或额外字段。
- 开发和运维人员通过 Trace 标识和业务标识串联完整执行链路。

**具体实现**

```text
HTTP 请求进入 Trace middleware
→ 创建或读取 trace 标识
→ 绑定 request_id、trace_id、client_ip、method 和 path

进入 Chat 用户边界
→ 继续绑定 user_id

业务代码写日志
→ 自动携带 ContextVar 中已有的请求字段
→ Conversation、Analysis、Session 等标识按业务日志显式记录
```


### 设计细节：请求上下文必须在 finally 中恢复

Trace 中间件通过 `ContextVar` 让协程链上的日志自动获得请求标识。每次 `set()` 都保存 token，并在请求结束时逆序 `reset()`；这对测试环境和可能复用同一 Task 的 ASGI 调度器同样有效。

```python
request_id_token = context.request_id_ctx.set(request_id)
trace_id_token = context.trace_id_ctx.set(trace_id)
user_id_token = context.user_id_ctx.set(None)
try:
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Trace-ID"] = trace_id
    return response
finally:
    context.user_id_ctx.reset(user_id_token)
    context.trace_id_ctx.reset(trace_id_token)
    context.request_id_ctx.reset(request_id_token)
```

转发头中的 IP 只进入日志。认证限流使用 ASGI peer 地址，不把客户端可伪造的 `X-Forwarded-For` 当作安全依据。

## 6. 路由和执行后台任务

**实现目的**

把耗时、可重试或需要周期恢复的工作移出 HTTP 请求，并通过固定队列隔离不同负载，防止索引任务、生命周期任务和轻量任务互相阻塞。

**使用者与使用方式**

- `metadata` 提交语义索引和字段取值索引任务。
- `query` 提交查询经验索引与修复任务。
- `assistant` 提交标题生成、对话删除和草稿清理任务。
- `workflows` 提交用户注销及恢复任务。
- 运维人员分别启动 Worker 和 Beat，并按队列配置消费任务。

**具体实现**

```text
Celery 启动
→ 使用 Redis broker 和 result backend
→ 加载 assistant、metadata、query 和 workflows tasks
→ 只接受 JSON 参数和结果
→ 声明 default、metadata-index、lifecycle、lightweight 队列
→ 禁止自动创建未知队列

提交任务
→ metadata.* 和 query.* 路由 metadata-index
→ assistant.generate_conversation_title 路由 lightweight
→ 其他 assistant.* 和 workflows.* 路由 lifecycle
→ 未匹配任务路由 default

Worker 执行任务
→ started 状态写入 result backend
→ 使用 task_acks_late
→ Worker 丢失时重新投递
→ 应用 soft 和 hard time limit
→ 结果按配置时间过期
```

周期调度：

```text
每日 value_index_sync_time
→ metadata.dispatch_value_indexes

每 lifecycle_schedule_seconds
→ assistant.cleanup_expired_drafts

每 user_deletion_retry_seconds
→ workflows.dispatch_due_user_deletions 原子领取并提交到期注销任务

每 query_experience_repair_seconds
→ query.repair_indexes
```

需要可靠恢复的业务任务会在业务 PostgreSQL 保存状态，Celery result backend 只承担运行状态观察。


### 设计细节：Celery 的可靠性参数只提供投递语义，业务状态仍落 PostgreSQL

任务启用晚确认、Worker 丢失重投和可见性超时，并禁止动态创建未知队列：

```python
TASK_VISIBILITY_TIMEOUT_SECONDS = cfg.task_queue.task_time_limit_seconds + 300

celery_app.conf.update(
    broker_transport_options={
        "visibility_timeout": TASK_VISIBILITY_TIMEOUT_SECONDS,
    },
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_create_missing_queues=False,
    task_track_started=True,
    worker_prefetch_multiplier=cfg.task_queue.worker_prefetch_multiplier,
)
```

这些设置允许消息在 Worker 异常退出后再次投递，所以业务任务必须幂等。查询经验索引通过 revision 收敛，Conversation 删除通过墓碑收敛，用户注销通过数据库任务状态和租约收敛。Celery Result Backend 的过期不会影响这些流程的最终状态。

## 7. 查询后台任务状态

**实现目的**

让提交异步任务的管理界面能够查询任务是否开始、成功或失败，而无需与具体业务任务实现耦合。

**使用者与使用方式**

- 后台任务提交接口返回 `task_id`。
- 管理员通过 `/api/v1/tasks/{task_id}` 轮询运行状态。
- 需要可靠恢复的业务流程仍查询所属模块的 PostgreSQL 状态，不能把 Celery Result Backend 当作业务事实来源。

**具体实现**

```text
API 提交后台任务
→ 返回 TaskAcceptedResponse.task_id

管理员请求 /api/v1/tasks/{task_id}
→ 从 Celery AsyncResult 读取状态
→ 返回 PENDING、STARTED、SUCCESS 或 FAILURE
→ 完成时返回 result
→ 失败时返回 error
```

### 设计细节：任务状态是 Celery 运行视图

状态接口要求管理员身份，并直接把 `AsyncResult` 投影为稳定响应。任务未结束时 `successful` 返回空值；成功结果和失败文本不会同时出现：

```python
@router.get("/{task_id}")
async def get_task_status(
    task_id: str,
    _: AdminUserDep,
) -> TaskStatusResponse:
    task = AsyncResult(task_id, app=celery_app)
    result = task.result if task.successful() else None
    error = str(task.result) if task.failed() else None
    return TaskStatusResponse(
        task_id=task_id,
        state=task.state,
        ready=task.ready(),
        successful=task.successful() if task.ready() else None,
        result=result,
        error=error,
    )
```

Redis Result Backend 中不存在或已经过期的任务通常表现为 `PENDING`。该接口无法据此证明业务任务从未提交；需要可靠恢复的流程仍以所属模块的 PostgreSQL 状态为准。
