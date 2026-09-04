# 01. Shared：从应用骨架到统一运行基础

## 功能说明

`app/shared` 是系统的基础运行支撑层，负责提供跨模块通用的强类型配置、外部系统客户端生命周期管理、多数据库声明基类、跨业务域数据契约、统一错误协议、调用链追踪以及后台任务基础设施。表达业务决策的代码归属各业务域；提供通用运行能力与稳定数据契约的代码全部收口在 `shared` 中。

本模块的核心职责与底层实现细节如下。

### 1. 配置加载与强类型校验系统

系统配置由 `app/shared/config/app_config.py` 统一管理，采用“导入即校验”的强约束机制：在 Python 模块导入阶段立即完成所有配置项的读取、解析、校验与实例化。任何配置项缺失、格式错误、超出范围或跨字段冲突都会在进程启动阶段立即抛出异常并阻止服务启动，确保进程绝不以部分可用或配置不一致的状态运行。

配置加载与校验机制包含以下关键技术细节：

- **分层加载顺序与环境变量插值**：首先通过 `dotenv.load_dotenv(CONFIG_DIR / ".env")` 读取本地环境变量文件，随后使用 `OmegaConf.load(CONFIG_FILE)` 读取 `conf/app_config.yaml`。YAML 文件中通过 `${oc.env:KEY, DEFAULT}` 引用环境变量。解析完成后转换为原始字典，最后由 Pydantic 模型 `Cfg` 统一完成类型转换与深度校验。系统环境变量具有最高优先级，可直接覆盖 YAML 中的对应默认值。
- **未知字段全面拒绝**：所有配置类均继承自基础配置模型 `AppConfigModel`，该模型配置了 `model_config = ConfigDict(extra="forbid")`。任何拼写错误、废弃字段或未经定义的额外配置项都会直接触发校验失败，防止无效配置静默残留。
- **敏感信息安全封装**：数据库密码、JWT 密钥、模型 API Key、Redis URL 以及凭据加解密密钥全部使用 Pydantic 的 `SecretStr` 类型封装。`SecretStr` 在进行日志输出、控制台打印、异常回溯以及 `model_dump()` 序列化时自动脱敏为 `**********`，防止凭据意外泄漏到日志与可观测系统中。仅在外部客户端初始化边界显式调用 `.get_secret_value()` 解包。
- **严格数值范围与局部校验**：网络端口强制限定在 `1 <= port <= 65535`；数据库与外部服务主机名必须非空；超时时间、连接池上限、内存限额、重试次数等均通过 `Field(gt=0)` 或 `Field(ge=...)` 进行下界与上界约束；JWT 签名密钥长度强制要求至少 32 字符；Doris 凭据加密密钥长度强制要求为 44 字符（对应 Base64 编码的 256 位 AES 密钥）。
- **跨字段与关系约束校验**：
  - Celery 任务软超时 `task_soft_time_limit_seconds` 必须严格小于硬超时 `task_time_limit_seconds`；
  - 字段值同步时间 `value_index_sync_time` 必须为规范的 `HH:MM` 本地时间（秒与微秒必须为 0，无时区信息）；
  - 本地 Docker 沙箱中单文件上限 `max_file_size_bytes` 必须小于等于用户卷配额 `user_storage_quota_bytes`；
  - 容器超时停止宽限时间必须小于容器强制删除清理时间。
- **专业 Agent 模型与引用完整性校验**：根配置模型 `Cfg` 包含模型后置校验器 `validate_agent_models`，强制要求系统声明的三类专业智能体（`explorer`、`analyst`、`reviewer`）必须全部在 `agent.specialists` 中显式配置，且每个智能体配置的 `model` 字段必须存在于 `lm_config.models` 配置列表中或为 `"default"`。
- **MCP 传输协议判别联合类型**：外部 MCP 服务配置采用基于 `transport` 字段的判别联合类型（`Annotated[Union[StdioMCPCfg, SSEMCPCfg, WebSocketMCPCfg, StreamableHTTPMCPCfg], Field(discriminator="transport")]`），每种传输协议拥有专属的配置模型，严格杜绝异构协议字段混用。

### 2. 多数据库声明域与客户端生命周期管理

系统采用多数据源架构，不同的数据模型存储于不同的逻辑或物理数据库中，各数据库的元数据声明与连接池彼此完全隔离。

- **三套独立的 SQLAlchemy 声明基类**：
  - `AuthBase`（定义于 `app/shared/database/base.py`）：承载用户账号、密码摘要、Refresh Token、Doris 查询凭据映射、资产授权投影以及用户注销任务。
  - `MetaBase`（定义于 `app/shared/database/base.py`）：承载 Doris 物理元数据资产、业务目录（表、字段、指标、关系）、字段值同步状态以及查询经验。
  - `AssistantBase`（定义于 `app/shared/database/base.py`）：承载对话会话（Conversation）元数据、删除墓碑与附件记录。
  - 三套 Base 各自维护独立的 `MetaData` 集合，物理隔离，严禁建立跨库的外键约束；跨业务域关联一律通过不可变的业务稳定标识符（如 `user_id`、`conversation_id`）进行显式关联。
- **PostgresClientManager 连接池与事务生命周期**：
  - 管理器使用 `create_async_engine` 结合 `postgresql+psycopg` 异步驱动构建进程级连接池，配置参数包括 `pool_size=10`、`max_overflow=20`、`pool_pre_ping=True`（自动探测失效连接）、`pool_recycle=1800`（30分钟回收长连接）。
  - 连接池是进程级的长生命周期单例，而数据库事务属于业务用例执行级别。`PostgresClientManager` 仅提供获取会话的接口 `session()` 和依赖生成器 `get_session()`，不负责自动提交事务。事务的开启、提交与回滚全部由业务层通过 `async with session.begin():` 显式控制，保证业务原子性与一致性边界清晰可见。
- **Doris 双层连接架构**：
  - 管理员连接池（`DorisClientManager`）：基于 `mysql+asyncmy` 驱动构建长生命周期连接池，专门用于执行 Doris 角色管理、授权变更、Row Policy 维护及物理元数据探测等系统级操作。
  - 角色查询连接注册表（`DorisQueryClientRegistry`）：业务 SQL 执行严禁使用管理员连接。注册表按 `role_name` 缓存各业务角色对应的专用连接池，并通过 `query_user` 与查询密码的 SHA-256 摘要生成凭据指纹。当用户所属角色的查询凭据或密码发生变更时，指纹改变，旧连接池自动从注册表中淘汰并异步释放，新连接池被即时创建并挂载，保证权限与凭据实时生效。
- **Elasticsearch 与 Embedding 客户端**：
  - `ESClientManager` 基于 `elasticsearch.AsyncElasticsearch` 提供全文检索与向量混合检索连接池；
  - `EmbeddingClientManager` 基于 `httpx.AsyncClient` 管理与远程向量化服务的连接池与超时控制，均支持应用启动时显式初始化与应用退出时异步安全关闭。
- **LangGraph Checkpoint 与咨询锁管理器（LangGraphPostgresManager）**：
  - 基于 `psycopg_pool.AsyncConnectionPool` 维护双独立连接池：Checkpointer 连接池（默认最大 20 连接，用于写入和读取 Agent 状态图的 Checkpoint 数据）与 Advisory Lock 连接池（固定最大 12 连接，用于 Conversation 生命周期锁）。
  - Advisory Lock 必须绑定在底层单一物理连接上。`advisory_lock(name)` 采用双层防重机制：首先通过进程内 `asyncio.Lock` 拦截单实例内的并发竞争，随后在独占物理连接上调用 PostgreSQL 内置函数 `SELECT pg_try_advisory_lock(:key)` 拦截跨多实例进程的并发执行。锁名称通过 SHA-256 哈希稳定映射为 64 位带符号整数（PostgreSQL `bigint`）。
- **应用生命周期（Lifespan）拓扑管理**：
  - FastAPI 组合根 `main.py` 的生命周期上下文管理器按照严密的依赖拓扑顺序进行资源初始化：
    ```text
    启动阶段（正序）：EmbeddingClient -> ESClient -> LangGraphPostgres -> Sandbox -> Agent -> AuthPG -> MetaPG -> AssistantPG -> AdminDoris -> Doris 角色凭据与只读范围预检
    退出阶段（逆序）：ConversationRun -> Agent -> Sandbox -> LangGraphPostgres -> EmbeddingClient -> ESClient -> AssistantPG -> MetaPG -> AuthPG -> AdminDoris -> QueryDorisRegistry
    ```
  - 启动过程中的任何未捕获异常都会触发 `finally` 块，按逆序释放已经成功创建的全部资源，避免资源泄漏。

### 3. 跨模块契约体系

为了防止模块间产生循环依赖或将持久化实现细节泄漏到其他领域，`app/shared/contracts` 严格定义了跨模块共享的纯数据契约（Data Transfer Objects）与协议（Protocols）。

- **契约隔离设计原则**：契约对象只能是不可变的 Python `dataclass(frozen=True)` 或 Pydantic 模型，只传递纯粹的数据快照与业务标识符。严禁在契约中携带 SQLAlchemy ORM 实体对象、活动数据库会话（Session）或外部客户端实例。
- **AgentSessionKey 核心分析契约**：
  - `AgentSessionKey`（定义于 `app/shared/contracts/analysis.py`）是打通 Assistant 编排、Query 执行、Sandbox 隔离环境与日志可观测性的唯一关键身份凭据。
  - 包含字段：`user_id: int`、`conversation_id: UUID`、`analysis_id: str`、`agent_type: AgentType`（`"explorer" | "analyst" | "reviewer"`）、`session_id: str`。
  - 它统一派生三个关键边界：
    1. **Checkpoint 命名空间**：`checkpoint_ns = f"subagents/{analysis_id}/{agent_type}/{session_id}"`，为 LangGraph 子图状态持久化提供完全隔离的命名空间；
    2. **沙箱文件隔离路径**：沙箱根据此 Key 在容器内部自动映射 `workspaces/{conversation_id}/sessions/{analysis_id}_{agent_type}_{session_id}/` 隔离目录；
    3. **日志追踪上下文**：自动注入结构化日志上下文，使单次分析中所有子智能体的调用链路完全可审计。
- **数据资产契约**：
  - `AssetIdentity` 与 `AssetAccessPolicy`（定义于 `app/shared/contracts/assets.py`）：表达四层数据资产层级（`data_source.database.table.column`），并向上层提供 `allows(asset)`（完全读取许可）与 `is_visible(asset)`（部分可见性判断）计算接口。
  - `DorisQueryIdentitySnapshot`（定义于 `app/shared/contracts/doris.py`）：向 Query 模块传递执行 SQL 所需的最小角色、Workload Group、内存与超时限制。
  - `SemanticRecallHit` 与 `QueryExperienceRecallHit`：传递 Elasticsearch 检索匹配项与其业务元数据快照。

### 4. 统一错误协议与请求追踪

系统对外暴露的 HTTP 错误响应全面遵循 RFC 9457 Problem Details 标准，杜绝向客户端泄露数据库异常或服务器内部堆栈信息。

- **RFC 9457 Problem Details 数据结构**：
  - 规范字段包括：`type`（错误标识 URI/短名称）、`title`（人类可读错误摘要）、`status`（HTTP 状态码）、`detail`（针对当前错误的具体描述）、`instance`（触发错误的请求 URI 路径），以及允许通过 `extra="allow"` 动态扩展的附加元数据字典（例如限流等待秒数 `retry_after_seconds`、表单校验错误列表 `errors`）。
- **四级全局异常处理体系**（位于 `app/shared/errors/exc_handlers.py`）：
  1. `ProblemError`（自定义业务异常基类）：直接转换为对应的 Problem Details JSON 响应。状态码为 4xx 时在服务端记录 WARNING 日志；状态码为 5xx 时记录完整异常堆栈。若包含 `retry_after_seconds` 则自动在响应中注入标准 `Retry-After` HTTP 头部；
  2. `RequestValidationError`（Pydantic 请求体/查询参数校验失败）：捕获后转换为统一的 422 Unprocessable Entity 响应，提取每个字段的定位路径（`loc`）、错误类型（`type`）与友好提示信息（`msg`）写入 `extensions["errors"]`；
  3. `Starlette / FastAPI HTTPException`：统一转换为带有标准 HTTP 状态码的 Problem Details 响应；
  4. 未捕获的系统兜底异常（`Exception`）：向客户端统一返回静态安全的 500 Internal Server Error 响应，绝不向客户端暴露真实异常类名、SQL 语句或报错细节；真实异常堆栈完整输出至服务端 ERROR 级别日志以供排查。
- **TraceMiddleware 请求全链路追踪**：
  - 位于 `app/shared/observability/trace.py`，负责拦截每个入站 HTTP 请求。
  - 从 HTTP 头部读取 `X-Request-ID`，若客户端未提供则自动生成新的 UUID4；同时读取 `X-Trace-ID`（默认等于 `request_id`）。
  - 将 `request_id`、`trace_id`、`client_ip`、`method`、`path` 写入基于 Python `contextvars.ContextVar` 维护的请求上下文变量中，并在 HTTP 响应头中回传 `X-Request-ID` 与 `X-Trace-ID`。
  - **ContextVar 严格成对重置**：中间件在 `finally` 代码块中严格按照 LIFO（后进先出）逆序调用 `token.reset()`。在 FastAPI/Starlette 异步事件循环架构下，底层协程或任务可能被并发复用，显式在请求结束时重置所有 ContextVar，彻底杜绝请求间的上下文污染与身份串流。
  - **客户端 IP 判定安全性**：中间件读取 `X-Forwarded-For` 的首个 IP 仅用于日志可观测性记录；在涉及高安全等级的认证密码暴力破解限流时，强制使用底层原始 ASGI 连接的 peer host，防止攻击者通过伪造 HTTP 请求头绕过 IP 限流。

### 5. 后台任务通道与最终一致性

系统使用 Celery 配合 Redis 作为异步后台任务执行通道。

- **Celery 核心配置参数**：
  - 序列化格式严格限制为 JSON（`accept_content=["json"]`，`task_serializer="json"`，`result_serializer="json"`），彻底杜绝 Pickle 带来的反序列化远程代码执行安全隐患；
  - 开启晚确认与崩溃重投机制：`task_acks_late=True`，任务仅在执行成功后才向 Broker 发送 ACK；配合 `task_reject_on_worker_lost=True`，当 Worker 节点因 OOM 或断电异常终止时，未完成的任务自动被 Broker 重新投递；
  - 超时控制：配置软超时 `task_soft_time_limit`（触发 Python `SoftTimeLimitExceeded` 异常供任务优雅收尾）与硬超时 `task_time_limit`（强制杀死子进程），Redis visibility timeout 设置为硬超时时间加 300 秒，防止执行中的长任务被误判为超时而被其他 Worker 重复领取；
  - 关闭自动动态建队列：`task_create_missing_queues=False`，防止因任务队列名称拼写错误导致在 Broker 中静默创建垃圾队列。
- **四类固定队列划分与任务路由**：
  - `metadata-index`：处理高吞吐的元数据结构索引同步、字段采样值索引同步与查询经验向量化任务；
  - `lightweight`：处理耗时短的轻量级任务（如会话首轮提问后异步调用小模型生成会话标题）；
  - `lifecycle`：处理长生命周期的跨存储资源清理任务（如会话物理删除、过期草稿定期扫描、用户全量资产注销工作流）；
  - `default`：处理默认未归类的通用任务。
- **Celery Beat 定时调度机制**：
  - `value-index-daily-dispatch`：每天在指定时间（`cfg.task_queue.value_index_sync_time`）按时触发 Doris 字段值全量同步分发；
  - `lifecycle-periodic-dispatch`：定期扫描并清理超过保留时间（`draft_ttl_minutes`）的未激活草稿会话；
  - `user-deletion-recovery`：定期扫描因 Worker 故障或网络分区而中断的已超期注销任务；
  - `query-experience-index-repair`：定期比对数据库与 Elasticsearch 中的查询经验版本，自动修复同步延迟。
- **持久化事实源与幂等原则**：
  - 任务的最终完成事实只保存在对应的业务 PostgreSQL 数据库中（如 `UserDeletionTask`、`meta_version` 与 `index_version`），Redis Result Backend 仅作为易失的单次运行状态指示器（PENDING / STARTED / SUCCESS / FAILURE），不可作为业务流程判断的最终依据。
  - 所有投递的任务均支持安全重复执行。各业务模块通过数据版本代次比较、数据库行级排他锁或状态机墓碑机制实现幂等收敛。

---

## 核心实现代码与模块架构

### 1. 数据库声明基类实现

代码定义了认证、元数据与智能体三大数据域独立的 DeclarativeBase：

```python
# app/shared/database/base.py
"""应用关系型模型的声明基类。"""

from sqlalchemy.orm import DeclarativeBase


class AuthBase(DeclarativeBase):
    """认证与权限 ORM 声明基类。"""


class MetaBase(DeclarativeBase):
    """元数据 ORM 声明基类。"""


class AssistantBase(DeclarativeBase):
    """助手运行数据 ORM 声明基类。"""
```

### 2. 强类型配置系统实现

核心配置模型、范围校验器、跨字段约束以及基于 OmegaConf + Pydantic 的加载器实现如下：

```python
# app/shared/config/app_config.py
from datetime import time
from pathlib import Path
from typing import Annotated, Any, Literal, Union, cast

import dotenv
from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.shared.contracts.analysis import AGENT_TYPES

ROOT_DIR = Path(__file__).parents[3]
CONFIG_DIR = ROOT_DIR / "conf"
CONFIG_FILE = CONFIG_DIR / "app_config.yaml"


class AppConfigModel(BaseModel):
    """拒绝未知字段的应用配置基类。"""

    model_config = ConfigDict(extra="forbid")


class LogCfg(AppConfigModel):
    """日志配置。"""

    level: str = Field(min_length=1)
    rotation: str = Field(min_length=1)


class DBConfig(AppConfigModel):
    """数据库连接配置。"""

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    user: str = Field(min_length=1)
    password: SecretStr = Field(min_length=1)
    database: str = Field(min_length=1)


class DorisCredentialConfig(AppConfigModel):
    """Doris 查询身份凭据加密配置。"""

    encryption_key: SecretStr = Field(min_length=44, max_length=44)


class ESConfig(AppConfigModel):
    """Elasticsearch 连接与索引配置。"""

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    column_index: str = Field(min_length=1)
    metric_index: str = Field(min_length=1)
    value_index: str = Field(min_length=1)
    query_experience_index: str = Field(min_length=1)
    embedding_size: int = Field(gt=0)


class EmbeddingConfig(AppConfigModel):
    """嵌入模型服务配置。"""

    base_url: str = Field(min_length=1)
    api_key: SecretStr | None
    model: str = Field(min_length=1)
    timeout: float = Field(gt=0)


class TaskQueueConfig(AppConfigModel):
    """Celery 任务队列配置。"""

    broker_url: SecretStr = Field(min_length=1)
    result_backend: SecretStr = Field(min_length=1)
    result_expires_seconds: int = Field(gt=0)
    task_time_limit_seconds: int = Field(gt=0)
    task_soft_time_limit_seconds: int = Field(gt=0)
    worker_prefetch_multiplier: int = Field(gt=0)
    value_index_sync_time: time
    lifecycle_schedule_seconds: int = Field(gt=0)
    query_experience_repair_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_time_limits(self) -> "TaskQueueConfig":
        """校验后台任务软硬超时关系及定时任务格式。"""
        if self.task_soft_time_limit_seconds >= self.task_time_limit_seconds:
            raise ValueError(
                "task_soft_time_limit_seconds 必须小于 task_time_limit_seconds"
            )
        if (
            self.value_index_sync_time.second != 0
            or self.value_index_sync_time.microsecond != 0
            or self.value_index_sync_time.tzinfo is not None
        ):
            raise ValueError("value_index_sync_time 必须是 HH:MM 格式的本地时间")
        return self


class AuthConfig(AppConfigModel):
    """认证令牌与密码策略配置。"""

    rate_limit_redis_url: SecretStr = Field(min_length=1)
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    issuer: str = Field(min_length=1)
    access_token_minutes: int = Field(gt=0)
    refresh_token_days: int = Field(gt=0)
    password_min_length: int = Field(ge=6, le=128)


class LifecycleConfig(AppConfigModel):
    """跨存储资源生命周期配置。"""

    draft_ttl_minutes: int = Field(gt=0)
    cleanup_batch_size: int = Field(gt=0, le=1000)
    user_deletion_retry_seconds: int = Field(gt=0)


class QueryConfig(AppConfigModel):
    """只读分析查询配置。"""

    data_source: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)
    memory_limit_bytes: int = Field(gt=0)
    batch_size: int = Field(gt=0)
    sample_rows: int = Field(ge=0, le=100)
    query_experience_vector_score_threshold: float = Field(ge=0, le=1)


class SandboxConfig(AppConfigModel):
    """本地 Docker 沙箱配置。"""

    image: str = Field(min_length=1)
    base_dir: Path
    user_storage_quota_bytes: int = Field(gt=0)
    max_file_size_bytes: int = Field(gt=0)
    archive_max_total_bytes: int = Field(gt=0)
    archive_max_file_count: int = Field(gt=0)
    cpu_limit: float = Field(gt=0)
    memory_limit_bytes: int = Field(gt=0)
    pids_limit: int = Field(gt=0)
    command_timeout_seconds: int = Field(gt=0)
    container_idle_ttl_minutes: int = Field(gt=0)
    container_capacity: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_storage_limits(self) -> "SandboxConfig":
        """校验沙箱文件限制与存储配额关系。"""
        if self.max_file_size_bytes > self.user_storage_quota_bytes:
            raise ValueError(
                "max_file_size_bytes 不能大于 user_storage_quota_bytes"
            )
        return self


class SpecialistConfig(AppConfigModel):
    """专业 Agent 静态配置。"""

    model: str = Field(min_length=1)
    prompt_file: Path
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    supports_vision: bool = False


class AgentConfig(AppConfigModel):
    """Agent 运行时配置。"""

    planner_model: str = Field(min_length=1)
    planner_prompt_file: Path
    title_model: str = Field(min_length=1)
    max_turns: int = Field(gt=0)
    specialists: dict[str, SpecialistConfig]


class ModelCfg(AppConfigModel):
    """大语言模型提供方与协议配置。"""

    base_url: str = Field(min_length=1)
    api_key: SecretStr = Field(min_length=1)
    model: str = Field(min_length=1)
    api_protocol: Literal["openai_chat_completions", "openai_responses", "openrouter"]
    timeout_seconds: float = Field(gt=0)


class LMConfigCfg(AppConfigModel):
    """模型集合配置。"""

    default_model: str = Field(min_length=1)
    models: dict[str, ModelCfg]


class Cfg(AppConfigModel):
    """应用全局配置顶层模型。"""

    port: int = Field(ge=1, le=65535)
    cors_origins: list[str]
    log: LogCfg

    doris: DBConfig
    auth_postgresql: DBConfig
    meta_postgresql: DBConfig
    langgraph_postgresql: DBConfig
    doris_credentials: DorisCredentialConfig
    elasticsearch: ESConfig
    embedding: EmbeddingConfig

    task_queue: TaskQueueConfig
    auth: AuthConfig
    lifecycle: LifecycleConfig
    query: QueryConfig
    sandbox: SandboxConfig

    lm_config: LMConfigCfg
    agent: AgentConfig

    @model_validator(mode="after")
    def validate_agent_models(self) -> "Cfg":
        """要求所有专业 Agent 均显式配置且引用可用模型。"""
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


def _load_config() -> Cfg:
    """从 .env 和 app_config.yaml 加载配置。"""
    dotenv.load_dotenv(CONFIG_DIR / ".env")
    loaded_cfg = OmegaConf.load(CONFIG_FILE)
    primitive_cfg = OmegaConf.to_container(loaded_cfg, resolve=True)
    return Cfg.model_validate(cast(dict[str, Any], primitive_cfg))


cfg = _load_config()
```

### 3. PostgreSQL 客户端管理器实现

管理异步 Engine、连接池和会话工厂，生命周期与事务边界完全分离：

```python
# app/shared/clients/postgres_client_manager.py
"""PostgreSQL 客户端管理。"""

from collections.abc import AsyncIterator

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.shared.config.app_config import DBConfig, cfg
from app.shared.database.base import AssistantBase, AuthBase, MetaBase


class PostgresClientManager:
    """PostgreSQL 客户端管理器。"""

    def __init__(
        self,
        db_config: DBConfig,
        base: type[DeclarativeBase],
    ) -> None:
        """初始化 PostgreSQL 客户端管理器。"""
        self._db_config = db_config
        self._base = base
        self._engine: AsyncEngine | None = None
        self._session_maker: async_sessionmaker[AsyncSession] | None = None

    @property
    def _url(self) -> URL:
        """获取异步数据库连接 URL。"""
        return URL.create(
            drivername="postgresql+psycopg",
            username=self._db_config.user,
            password=self._db_config.password.get_secret_value(),
            host=self._db_config.host,
            port=self._db_config.port,
            database=self._db_config.database,
        )

    def init(self) -> None:
        """初始化数据库引擎和会话工厂。"""
        self._engine = create_async_engine(
            self._url,
            echo=False,
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

    def _get_session_maker(self) -> async_sessionmaker[AsyncSession]:
        """获取数据库会话工厂。"""
        if self._session_maker is None:
            raise RuntimeError("PostgreSQL 客户端管理器尚未初始化")
        return self._session_maker

    def session(self) -> AsyncSession:
        """创建数据库会话。"""
        return self._get_session_maker()()

    async def get_session(self) -> AsyncIterator[AsyncSession]:
        """获取 FastAPI 请求级数据库会话（仅负责资源释放，不自动提交事务）。"""
        async with self.session() as db_session:
            yield db_session

    async def close(self) -> None:
        """关闭数据库引擎并释放连接池。"""
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._session_maker = None

    async def init_tables(self) -> None:
        """根据当前 ORM 模型创建尚未存在的数据表。"""
        if self._engine is None:
            raise RuntimeError("PostgreSQL 客户端管理器尚未初始化")
        async with self._engine.begin() as connection:
            await connection.run_sync(self._base.metadata.create_all)


# 进程级单例实例定义
auth_postgres_client_manager = PostgresClientManager(
    cfg.auth_postgresql,
    AuthBase,
)
meta_postgres_client_manager = PostgresClientManager(
    cfg.meta_postgresql,
    MetaBase,
)
assistant_postgres_client_manager = PostgresClientManager(
    cfg.langgraph_postgresql,
    AssistantBase,
)
```

### 4. Doris 客户端管理器与动态角色连接池注册表实现

管理系统管理员连接池与按角色和凭据指纹动态维护的用户查询连接池：

```python
# app/shared/clients/doris_client_manager.py
"""Doris 客户端管理。"""

import asyncio
import hashlib
from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.shared.config.app_config import DBConfig, cfg


class DorisClientManager:
    """Doris 客户端管理器。"""

    def __init__(self, db_config: DBConfig) -> None:
        """初始化 Doris 客户端管理器。"""
        self._db_config = db_config
        self._engine: AsyncEngine | None = None

    @property
    def _url(self) -> URL:
        """获取 Doris 异步连接 URL。"""
        return URL.create(
            drivername="mysql+asyncmy",
            username=self._db_config.user,
            password=self._db_config.password.get_secret_value(),
            host=self._db_config.host,
            port=self._db_config.port,
            database=self._db_config.database,
        )

    def init(self) -> None:
        """初始化 Doris 连接池。"""
        self._engine = create_async_engine(
            self._url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=30,
        )

    def _get_engine(self) -> AsyncEngine:
        """获取 Doris 数据库引擎。"""
        if self._engine is None:
            raise RuntimeError("Doris 客户端管理器尚未初始化")
        return self._engine

    def connection(self) -> AsyncConnection:
        """创建 Doris 数据库连接。"""
        return self._get_engine().connect()

    async def close(self) -> None:
        """关闭 Doris 连接池并释放资源。"""
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None


@dataclass(slots=True)
class _QueryClientEntry:
    """缓存的角色查询连接池条目。"""

    fingerprint: str
    manager: DorisClientManager


class DorisQueryClientRegistry:
    """按数据库中的稳定查询身份动态管理 Doris 连接池。"""

    def __init__(self, endpoint: DBConfig) -> None:
        """初始化查询端点和按角色隔离的连接池注册表。"""
        self._endpoint = endpoint
        self._entries: dict[str, _QueryClientEntry] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        role_name: str,
        query_user: str,
        password: str,
    ) -> DorisClientManager:
        """读取或创建与当前查询凭据一致的连接池，凭据变化时淘汰旧池。"""
        fingerprint = hashlib.sha256(f"{query_user}\0{password}".encode()).hexdigest()
        stale: DorisClientManager | None = None
        async with self._lock:
            entry = self._entries.get(role_name)
            if entry is not None and entry.fingerprint == fingerprint:
                return entry.manager
            if entry is not None:
                stale = entry.manager
            db_config = DBConfig(
                host=self._endpoint.host,
                port=self._endpoint.port,
                user=query_user,
                password=SecretStr(password),
                database=self._endpoint.database,
            )
            manager = DorisClientManager(db_config)
            manager.init()
            self._entries[role_name] = _QueryClientEntry(
                fingerprint=fingerprint,
                manager=manager,
            )
        if stale is not None:
            await stale.close()
        return manager

    async def close(self) -> None:
        """释放所有角色的查询连接池。"""
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            await entry.manager.close()


admin_doris_client_manager = DorisClientManager(cfg.doris)
query_doris_client_registry = DorisQueryClientRegistry(cfg.doris)
```

### 5. LangGraph Checkpoint 与专用 Advisory Lock 管理器实现

管理基于 `psycopg_pool` 的 Checkpointer 连接池与独占会话的 advisory lock 连接池：

```python
# app/shared/clients/langgraph_postgres_manager.py
"""LangGraph PostgreSQL 持久化客户端管理。"""

import asyncio
import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.shared.config.app_config import DBConfig, cfg

_ADVISORY_POOL_MAX_SIZE = 12


class AdvisoryLockBusyError(RuntimeError):
    """指定 advisory lock 已被其他执行单元占用。"""


def _advisory_lock_key(name: str) -> int:
    """把业务锁名称稳定映射为 PostgreSQL bigint。"""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class LangGraphPostgresManager:
    """LangGraph PostgreSQL Checkpointer 和咨询锁生命周期管理器。"""

    def __init__(self, db_config: DBConfig) -> None:
        """初始化 PostgreSQL 持久化配置。"""
        self._db_config = db_config
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._advisory_pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._checkpointer: AsyncPostgresSaver | None = None
        self._advisory_locks: dict[str, asyncio.Lock] = {}

    @property
    def _conninfo(self) -> str:
        """构造 PostgreSQL 连接信息。"""
        return make_conninfo(
            host=self._db_config.host,
            port=self._db_config.port,
            user=self._db_config.user,
            password=self._db_config.password.get_secret_value(),
            dbname=self._db_config.database,
        )

    async def init(self) -> None:
        """初始化 Checkpoint 连接池和专用 Advisory Lock 连接池。"""
        pool = AsyncConnectionPool[AsyncConnection[DictRow]](
            conninfo=self._conninfo,
            min_size=1,
            max_size=20,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        advisory_pool = AsyncConnectionPool[AsyncConnection[DictRow]](
            conninfo=self._conninfo,
            min_size=1,
            max_size=_ADVISORY_POOL_MAX_SIZE,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        try:
            await pool.open(wait=True)
            await advisory_pool.open(wait=True)
        except Exception:
            await advisory_pool.close()
            await pool.close()
            raise

        checkpointer = AsyncPostgresSaver(pool)
        try:
            await checkpointer.setup()
        except Exception:
            await advisory_pool.close()
            await pool.close()
            raise

        self._pool = pool
        self._advisory_pool = advisory_pool
        self._checkpointer = checkpointer

    def get_checkpointer(self) -> AsyncPostgresSaver:
        """获取 LangGraph Checkpointer 实例。"""
        if self._checkpointer is None:
            raise RuntimeError("LangGraph PostgreSQL 管理器尚未初始化")
        return self._checkpointer

    @asynccontextmanager
    async def advisory_lock(self, name: str) -> AsyncGenerator[None]:
        """在专用数据库连接上持有 PostgreSQL 会话级咨询锁。"""
        if self._advisory_pool is None:
            raise RuntimeError("LangGraph PostgreSQL 管理器尚未初始化")

        local_lock = self._advisory_locks.setdefault(name, asyncio.Lock())
        if local_lock.locked():
            raise AdvisoryLockBusyError(f"本地咨询锁已被占用: {name}")

        async with local_lock:
            lock_key = _advisory_lock_key(name)
            async with self._advisory_pool.connection() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "SELECT pg_try_advisory_lock(%s) AS acquired",
                        (lock_key,),
                    )
                    row = await cursor.fetchone()
                    acquired = bool(row["acquired"]) if row is not None else False
                    if not acquired:
                        raise AdvisoryLockBusyError(f"PostgreSQL 咨询锁已被占用: {name}")
                    try:
                        yield
                    finally:
                        await cursor.execute(
                            "SELECT pg_advisory_unlock(%s)",
                            (lock_key,),
                        )

    async def close(self) -> None:
        """释放持久化连接池资源。"""
        self._checkpointer = None
        if self._advisory_pool is not None:
            await self._advisory_pool.close()
            self._advisory_pool = None
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


langgraph_postgres_manager = LangGraphPostgresManager(cfg.langgraph_postgresql)
```

### 6. 跨模块契约：AgentSessionKey 实现

统一约束跨模块会话身份，并自动生成 Checkpoint 命名空间与隔离路径：

```python
# app/shared/contracts/analysis.py
"""跨模块共享的分析任务与 Agent Session 标识。"""

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

type AgentType = Literal[
    "explorer",
    "analyst",
    "reviewer",
]

AGENT_TYPES: tuple[AgentType, ...] = (
    "explorer",
    "analyst",
    "reviewer",
)

IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_agent_type(value: str) -> AgentType:
    """校验并收窄专业 Agent 类型。"""
    if value not in AGENT_TYPES:
        raise ValueError(f"未知的智能体类型: {value}")
    return value


def _validate_identifier(value: str, field_name: str) -> str:
    """校验 Analysis 和 Session 标识。"""
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} 必须以字母或数字开头，且仅包含 1-64 位小写字母、数字、下划线或连字符"
        )
    return value


@dataclass(frozen=True, slots=True)
class AgentSessionKey:
    """定位一个可续接的专业 Agent Session。"""

    user_id: int
    conversation_id: UUID
    analysis_id: str
    agent_type: AgentType
    session_id: str

    def __post_init__(self) -> None:
        """校验用户及专业 Agent Session 标识。"""
        if isinstance(self.user_id, bool) or self.user_id <= 0:
            raise ValueError("user_id 必须为正整数")
        _validate_identifier(self.analysis_id, "analysis_id")
        validate_agent_type(self.agent_type)
        _validate_identifier(self.session_id, "session_id")

    @property
    def checkpoint_ns(self) -> str:
        """生成受控的 Checkpoint namespace。"""
        return f"subagents/{self.analysis_id}/{self.agent_type}/{self.session_id}"
```

### 7. 错误协议与全局异常处理器实现

```python
# app/shared/errors/base.py
"""应用异常与 RFC 9457 Problem Details 响应模型。"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from pydantic import BaseModel, ConfigDict


class ProblemDetails(BaseModel):
    """RFC 9457 错误响应协议。"""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


class ProblemError(Exception):
    """可由全局处理器转换为 Problem Details 响应的应用业务异常。"""

    type: str = "internal-server-error"
    title: str = "服务器内部错误"
    status: int = HTTPStatus.INTERNAL_SERVER_ERROR

    def __init__(
        self,
        title: str | None = None,
        *,
        detail: str | None = None,
        type: str | None = None,
        status: int | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> None:
        """构造可序列化为 Problem Details 的应用异常。"""
        self.title = title or self.title
        self.detail = detail
        self.extensions = dict(extensions or {})
        if type is not None:
            self.type = type
        if status is not None:
            self.status = status

        super().__init__(detail or self.title)

    def to_problem(
        self,
        *,
        instance: str | None = None,
    ) -> dict[str, Any]:
        """转换为 Problem Details 响应字典。"""
        payload: dict[str, Any] = dict(self.extensions)
        payload.update(
            {
                "type": self.type,
                "title": self.title,
                "status": self.status,
                "detail": self.detail,
                "instance": instance,
            }
        )
        return payload
```

```python
# app/shared/errors/exc_handlers.py
"""FastAPI 全局异常处理器。"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException
from starlette.types import ExceptionHandler

from app.shared.errors.base import ProblemError


def _build_response(
    request: Request,
    exc: ProblemError,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """构造 Problem Details 错误响应。"""
    return JSONResponse(
        status_code=exc.status,
        content=exc.to_problem(instance=request.url.path),
        headers=headers,
        media_type="application/problem+json",
    )


def _log_problem(exc: ProblemError, source: Exception) -> None:
    """按响应状态记录异常。"""
    context = {
        "problem_type": exc.type,
        "status": exc.status,
        "exc_type": type(source).__name__,
        "detail": exc.detail,
    }
    if exc.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        logger.opt(exception=source).error(exc.title, **context)
    else:
        logger.warning(exc.title, **context)


def _problem_error_handler(request: Request, exc: ProblemError) -> JSONResponse:
    """处理应用预期业务异常。"""
    _log_problem(exc, exc)
    retry_after = exc.extensions.get("retry_after_seconds")
    headers = (
        {"Retry-After": str(retry_after)}
        if exc.status == HTTPStatus.TOO_MANY_REQUESTS
        and isinstance(retry_after, int)
        and retry_after > 0
        else None
    )
    return _build_response(request, exc, headers=headers)


def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理请求参数校验异常。"""
    errors: list[dict[str, Any]] = [
        {
            "type": error["type"],
            "location": list(error["loc"]),
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    problem = ProblemError(
        title="参数校验失败",
        detail="请求参数不符合接口要求",
        type="validation-error",
        status=HTTPStatus.UNPROCESSABLE_ENTITY,
        extensions={"errors": errors},
    )
    _log_problem(problem, exc)
    return _build_response(request, problem)


def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """统一处理 FastAPI 与 Starlette HTTP 异常。"""
    detail = exc.detail if isinstance(exc.detail, str) else "请求处理失败"
    try:
        title = HTTPStatus(exc.status_code).phrase
    except ValueError:
        title = "HTTP 请求错误"
    problem = ProblemError(
        title=title,
        detail=detail,
        status=exc.status_code,
        type=f"http-{exc.status_code}",
        extensions=(
            {"errors": exc.detail} if isinstance(exc.detail, (list, dict)) else None
        ),
    )
    _log_problem(problem, exc)
    return _build_response(request, problem, headers=exc.headers)


def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理所有未捕获异常且不向客户端泄露内部堆栈信息。"""
    problem = ProblemError()
    _log_problem(problem, exc)
    return _build_response(request, problem)


def register_exception_handlers(app: FastAPI) -> None:
    """向 FastAPI 注册全局异常处理器。"""
    app.add_exception_handler(
        ProblemError,
        cast(ExceptionHandler, _problem_error_handler),
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, _validation_error_handler),
    )
    app.add_exception_handler(
        HTTPException,
        cast(ExceptionHandler, _http_exception_handler),
    )
    app.add_exception_handler(
        Exception,
        cast(ExceptionHandler, _unhandled_exception_handler),
    )
```

### 8. 追踪中间件与上下文恢复实现

```python
# app/shared/observability/context.py
"""请求与追踪上下文变量。"""

from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")
client_ip_ctx: ContextVar[str] = ContextVar("client_ip", default="")
method_ctx: ContextVar[str] = ContextVar("method", default="")
path_ctx: ContextVar[str] = ContextVar("path", default="")
user_id_ctx: ContextVar[int | None] = ContextVar("user_id", default=None)
```

```python
# app/shared/observability/trace.py
"""追踪中间件。"""

import uuid
from collections.abc import Callable

from fastapi import Request, Response

from app.shared.observability import context


def _get_client_ip(request: Request) -> str:
    """获取客户端 IP（转发头仅用于日志，认证限流使用 ASGI peer host）。"""
    if forwarded := request.headers.get("X-Forwarded-For"):
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def middleware(request: Request, call_next: Callable) -> Response:
    """追踪中间件：透传请求头并在 finally 块中逆序重置上下文。"""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    trace_id = request.headers.get("X-Trace-ID", request_id)

    request_id_token = context.request_id_ctx.set(request_id)
    trace_id_token = context.trace_id_ctx.set(trace_id)
    client_ip_token = context.client_ip_ctx.set(_get_client_ip(request))
    method_token = context.method_ctx.set(request.method)
    path_token = context.path_ctx.set(request.url.path)
    user_id_token = context.user_id_ctx.set(None)

    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = trace_id
        return response
    finally:
        # ContextVar 会被子协程继承，请求结束必须显式 LIFO 逆序恢复，防止连接复用时上下文污染
        context.user_id_ctx.reset(user_id_token)
        context.path_ctx.reset(path_token)
        context.method_ctx.reset(method_token)
        context.client_ip_ctx.reset(client_ip_token)
        context.trace_id_ctx.reset(trace_id_token)
        context.request_id_ctx.reset(request_id_token)
```

### 9. Celery 任务系统与调度表实现

```python
# app/shared/tasks/celery_app.py
"""Celery 应用与队列配置。"""

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

from app.shared.config.app_config import cfg

TASK_VISIBILITY_TIMEOUT_SECONDS = cfg.task_queue.task_time_limit_seconds + 300

celery_app = Celery(
    "dataagent",
    broker=cfg.task_queue.broker_url.get_secret_value(),
    backend=cfg.task_queue.result_backend.get_secret_value(),
    include=[
        "app.assistant.tasks",
        "app.metadata.tasks",
        "app.query.tasks",
        "app.workflows.tasks",
    ],
)

celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": TASK_VISIBILITY_TIMEOUT_SECONDS,
    },
    enable_utc=True,
    result_accept_content=["json"],
    result_expires=cfg.task_queue.result_expires_seconds,
    result_serializer="json",
    task_acks_late=True,
    task_create_missing_queues=False,
    task_default_exchange="dataagent",
    task_default_exchange_type="direct",
    task_default_queue="default",
    task_default_routing_key="default",
    task_queues=(
        Queue("default", routing_key="default"),
        Queue("metadata-index", routing_key="metadata-index"),
        Queue("lifecycle", routing_key="lifecycle"),
        Queue("lightweight", routing_key="lightweight"),
    ),
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 3,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 1,
    },
    task_reject_on_worker_lost=True,
    task_routes={
        "dataagent.assistant.generate_conversation_title": {
            "queue": "lightweight",
            "routing_key": "lightweight",
        },
        "dataagent.assistant.*": {
            "queue": "lifecycle",
            "routing_key": "lifecycle",
        },
        "dataagent.metadata.*": {
            "queue": "metadata-index",
            "routing_key": "metadata-index",
        },
        "dataagent.query.*": {
            "queue": "metadata-index",
            "routing_key": "metadata-index",
        },
        "dataagent.workflows.*": {
            "queue": "lifecycle",
            "routing_key": "lifecycle",
        },
    },
    task_serializer="json",
    task_soft_time_limit=cfg.task_queue.task_soft_time_limit_seconds,
    task_time_limit=cfg.task_queue.task_time_limit_seconds,
    task_track_started=True,
    timezone="Asia/Shanghai",
    worker_prefetch_multiplier=cfg.task_queue.worker_prefetch_multiplier,
)

celery_app.conf.beat_schedule = {
    "value-index-daily-dispatch": {
        "task": "dataagent.metadata.dispatch_value_indexes",
        "schedule": crontab(
            hour=cfg.task_queue.value_index_sync_time.hour,
            minute=cfg.task_queue.value_index_sync_time.minute,
        ),
    },
    "lifecycle-periodic-dispatch": {
        "task": "dataagent.assistant.cleanup_expired_drafts",
        "schedule": cfg.task_queue.lifecycle_schedule_seconds,
    },
    "user-deletion-recovery": {
        "task": "dataagent.workflows.dispatch_due_user_deletions",
        "schedule": cfg.lifecycle.user_deletion_retry_seconds,
    },
    "query-experience-index-repair": {
        "task": "dataagent.query.repair_indexes",
        "schedule": cfg.task_queue.query_experience_repair_seconds,
    },
}
```

后台任务提交与状态接口数据结构：

```python
# app/shared/tasks/submission.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskSubmission:
    """已提交的 Celery 任务标识。"""

    task_id: str


# app/shared/tasks/schemas.py
from typing import Any
from pydantic import BaseModel


class TaskAcceptedResponse(BaseModel):
    """后台任务已受理响应。"""

    task_id: str


class TaskStatusResponse(BaseModel):
    """后台任务执行状态响应。"""

    task_id: str
    state: str
    ready: bool
    successful: bool | None
    result: Any | None = None
    error: str | None = None
```

### 10. FastAPI 组合根 Lifespan 装配实现

```python
# main.py（核心装配片段）
from contextlib import asynccontextmanager
from fastapi import FastAPI
from loguru import logger

from app.providers import agent_manager, conversation_run_service, sandbox_manager
from app.shared.clients.doris_client_manager import (
    admin_doris_client_manager,
    query_doris_client_registry,
)
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.langgraph_postgres_manager import langgraph_postgres_manager
from app.shared.clients.postgres_client_manager import (
    assistant_postgres_client_manager,
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.errors.exc_handlers import register_exception_handlers
from app.shared.observability import trace


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """初始化并释放应用进程持有的共享资源（严格依赖正序初始化与逆序释放）。"""
    try:
        logger.info("开始初始化应用资源")
        embedding_client_manager.init()
        es_client_manager.init()
        await langgraph_postgres_manager.init()
        await sandbox_manager.init()
        await agent_manager.init()

        auth_postgres_client_manager.init()
        await auth_postgres_client_manager.init_tables()
        meta_postgres_client_manager.init()
        await meta_postgres_client_manager.init_tables()
        assistant_postgres_client_manager.init()
        await assistant_postgres_client_manager.init_tables()

        admin_doris_client_manager.init()
        logger.info("应用资源初始化完成")
        yield
    finally:
        logger.info("开始释放应用资源")
        await conversation_run_service.close()
        await agent_manager.close()
        await sandbox_manager.close()
        await langgraph_postgres_manager.close()
        await embedding_client_manager.close()
        await es_client_manager.close()
        await assistant_postgres_client_manager.close()
        await meta_postgres_client_manager.close()
        await auth_postgres_client_manager.close()
        await admin_doris_client_manager.close()
        await query_doris_client_registry.close()
        logger.info("应用资源释放完成")


app = FastAPI(lifespan=lifespan)
app.middleware("http")(trace.middleware)
register_exception_handlers(app)
```

---

## 阶段学习与验证要点

### 阶段 1：验证配置加载与启动阻断

1. **正常配置启动验证**：确保 `conf/app_config.yaml` 配置完整合法，启动应用后访问 `/docs` 验证 OpenAPI 正常展示。
2. **缺失必填项阻断验证**：删除配置文件中的 `doris.host`，重启应用验证是否在启动导入阶段立即抛出 `pydantic_core.ValidationError` 并终止进程。
3. **未知冗余字段拦截验证**：在 `log` 节点下增加多余字段 `unknown_key: "value"`，启动应用验证是否被 `extra="forbid"` 拒绝。
4. **凭据输出脱敏验证**：调用 `print(cfg.model_dump())` 或日志打印，验证敏感配置字段输出为脱敏后的 `'**********'`。
5. **跨字段约束验证**：将 `task_soft_time_limit_seconds` 设置为大于 `task_time_limit_seconds` 的数值，启动应用验证是否被 `validate_time_limits` 拒绝。

### 阶段 2：验证数据库会话生命周期与异常回滚

1. **事务原子性与回滚验证**：在用例中调用 `session = auth_postgres_client_manager.session()` 并通过 `async with session.begin():` 写入测试数据后抛出异常，验证数据自动回滚且连接安全归还。
2. **Doris 角色连接池指纹淘汰验证**：先后使用相同角色名称但不同的密码调用 `DorisQueryClientRegistry.get_or_create`，验证旧连接池被正确关闭且新连接池建立。
3. **LangGraph 咨询锁排他性验证**：在第一个异步任务中持有 `langgraph_postgres_manager.advisory_lock("test-lock")`，启动第二个并发任务尝试获取同名锁，验证其立即抛出 `AdvisoryLockBusyError`。

### 阶段 3：验证追踪与错误隔离

1. **统一错误响应协议验证**：向应用发送格式错误的 JSON 请求，验证响应状态码为 422 且 Content-Type 为 `application/problem+json`，响应体符合 `ProblemDetails` 规范。
2. **内部堆栈隐藏验证**：在测试接口中故意触发未捕获异常（如除以零），验证客户端仅收到标准 500 Problem Details 结构，服务端日志记录包含完整堆栈信息。
3. **ContextVar 逆序恢复验证**：并发发起多个不同 `X-Request-ID` 的请求，验证请求完成时各个 ContextVar 均被正确 reset，后续复用连接不会串染上一请求的 trace 标识。
