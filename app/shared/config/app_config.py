from datetime import time, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import dotenv
from omegaconf import OmegaConf
from pydantic import BaseModel, Field, SecretStr, model_validator

# 路径常量
ROOT_DIR = Path(__file__).parents[3]
CONFIG_DIR = ROOT_DIR / "conf"
CONFIG_FILE = CONFIG_DIR / "app_config.yaml"


# 应用基础配置
class LogCfg(BaseModel):
    """日志配置"""

    level: str
    rotation: str


# 数据连接与检索配置
class DBConfig(BaseModel):
    """数据库连接配置"""

    host: str
    port: int
    user: str
    password: str
    database: str


class DorisCredentialConfig(BaseModel):
    """Doris 查询身份凭据加密配置"""

    encryption_key: SecretStr = Field(min_length=44, max_length=44)


class ESConfig(BaseModel):
    """Elasticsearch 连接与索引配置"""

    host: str
    port: int
    column_index: str
    metric_index: str
    value_index: str
    query_experience_index: str
    embedding_size: int


class EmbeddingConfig(BaseModel):
    """嵌入模型服务配置"""

    base_url: str
    api_key: str | None
    model: str
    timeout: float


# 元数据索引配置
class MetadataIndexConfig(BaseModel):
    """元数据索引同步策略配置"""

    value_lookback_seconds: int = Field(gt=0)


# 后台任务配置
class TaskQueueConfig(BaseModel):
    """Celery 任务队列配置"""

    broker_url: str = Field(min_length=1)
    result_backend: str = Field(min_length=1)
    result_expires_seconds: int = Field(gt=0)
    task_time_limit_seconds: int = Field(gt=0)
    task_soft_time_limit_seconds: int = Field(gt=0)
    worker_prefetch_multiplier: int = Field(gt=0)
    value_index_sync_time: time
    lifecycle_schedule_seconds: int = Field(gt=0)
    query_experience_repair_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_time_limits(self) -> "TaskQueueConfig":
        """校验后台任务软硬超时关系"""
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


# 身份与生命周期配置
class AuthConfig(BaseModel):
    """认证令牌与密码策略配置"""

    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    issuer: str = Field(min_length=1)
    access_token_minutes: int = Field(gt=0)
    refresh_token_days: int = Field(gt=0)
    password_min_length: int = Field(ge=6, le=128)


class LifecycleConfig(BaseModel):
    """跨存储资源生命周期配置"""

    draft_ttl_minutes: int = Field(gt=0)
    cleanup_interval_seconds: int = Field(gt=0)
    cleanup_batch_size: int = Field(gt=0, le=1000)
    user_deletion_retry_seconds: int = Field(gt=0)


# 查询与沙箱配置
class QueryConfig(BaseModel):
    """只读分析查询配置"""

    data_source: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)
    memory_limit_bytes: int = Field(gt=0)
    max_rows: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    batch_size: int = Field(gt=0)
    sample_rows: int = Field(ge=0, le=100)
    query_experience_vector_score_threshold: float = Field(ge=0, le=1)


class SandboxOwnershipConfig(BaseModel):
    """沙箱跨进程所有权配置"""

    redis_url: str = Field(min_length=1)
    lock_timeout_seconds: float = Field(gt=0)
    wait_timeout_seconds: float = Field(gt=0)
    lease_seconds: float = Field(gt=0)


class SandboxConfig(BaseModel):
    """本地 Docker 沙箱配置"""

    deployment_namespace: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    )
    ownership: SandboxOwnershipConfig
    image: str
    network_mode: Literal["none", "bridge"]
    memory_limit: str
    nano_cpus: int = Field(gt=0)
    pids_limit: int = Field(gt=0)
    execute_timeout_seconds: int = Field(default=120, gt=0, le=600)
    max_output_bytes: int = Field(ge=4 * 1024 * 1024)
    max_capture_bytes: int = Field(gt=0)
    max_file_bytes: int = Field(gt=0)
    max_workspace_bytes: int = Field(gt=0)
    workspace_quota_mode: Literal["application", "volume_driver"]
    volume_driver: str = Field(min_length=1)
    volume_driver_options: dict[str, str]
    max_running_containers: int = Field(gt=0)
    max_capacity_waiters: int = Field(gt=0)
    capacity_wait_timeout_seconds: float = Field(gt=0)
    idle_stop_seconds: int = Field(gt=0)
    idle_remove_seconds: int = Field(gt=0)
    cleanup_interval_seconds: int = Field(gt=0)
    cleanup_failure_alert_threshold: int = Field(gt=0)
    stop_containers_on_shutdown: bool

    @model_validator(mode="after")
    def validate_size_limits(self) -> "SandboxConfig":
        """校验沙箱容量限制之间的关系"""
        if self.max_output_bytes > self.max_capture_bytes:
            raise ValueError("max_output_bytes 不能大于 max_capture_bytes")
        if self.max_capture_bytes > self.max_file_bytes:
            raise ValueError("max_capture_bytes 不能大于 max_file_bytes")
        if self.max_file_bytes > self.max_workspace_bytes:
            raise ValueError("max_file_bytes 不能大于 max_workspace_bytes")
        if self.idle_stop_seconds >= self.idle_remove_seconds:
            raise ValueError("idle_stop_seconds 必须小于 idle_remove_seconds")
        option_fields = {
            "deployment_namespace": self.deployment_namespace,
            "user_id": 1,
            "max_workspace_bytes": self.max_workspace_bytes,
        }
        try:
            rendered_options = {
                key: value.format_map(option_fields)
                for key, value in self.volume_driver_options.items()
            }
        except (KeyError, ValueError) as exc:
            placeholder = exc.args[0] if exc.args else "格式无效"
            raise ValueError(f"数据卷驱动选项模板无效: {placeholder}") from exc
        if any(not key or not value for key, value in rendered_options.items()):
            raise ValueError("数据卷驱动选项不能包含空键或空值")
        if self.workspace_quota_mode == "volume_driver" and not any(
            "{max_workspace_bytes}" in value
            for value in self.volume_driver_options.values()
        ):
            raise ValueError(
                "volume_driver 配额模式要求选项中包含 max_workspace_bytes 占位符"
            )
        if (
            self.workspace_quota_mode == "volume_driver"
            and self.volume_driver == "local"
        ):
            raise ValueError("volume_driver 配额模式要求使用支持配额的外部驱动")
        return self


# 模型与智能体配置
class ModelCfg(BaseModel):
    """语言模型配置"""

    model_provider: str
    model: str
    base_url: str
    api_key: str
    params: dict[str, Any]
    profile: dict[str, Any]


class LMConfigCfg(BaseModel):
    """语言模型集合与激活项配置"""

    active: str
    models: dict[str, ModelCfg]


class OrchestrationConfig(BaseModel):
    """动态专业 Agent 编排限制"""

    mode: Literal["dynamic_subagents"]
    max_parallel_sessions: int = Field(gt=0)
    max_delegations_per_run: int = Field(gt=0)
    max_continuations: int = Field(ge=0)
    max_session_resumes: int = Field(gt=0)
    max_repair_rounds: int = Field(ge=0)
    max_repair_depth: int = Field(ge=0)
    session_lock_timeout: float = Field(gt=0)


class InterpreterConfig(BaseModel):
    """Planner 内嵌解释器配置"""

    mode: Literal["thread"]
    ptc: list[Literal["delegation"]]
    timeout_seconds: float = Field(gt=0)
    memory_limit_bytes: int = Field(gt=0)


class SpecialistConfig(BaseModel):
    """专业 Agent 模型选择"""

    model: str = Field(min_length=1)


class AgentConfig(BaseModel):
    """多 Agent 运行时配置"""

    orchestration: OrchestrationConfig
    interpreter: InterpreterConfig
    specialists: dict[
        Literal["explorer", "analyst", "reviewer", "visualizer"],
        SpecialistConfig,
    ]


# 外部工具配置
class SSEMCPCfg(BaseModel):
    """SSE 传输方式的 MCP 服务配置"""

    transport: Literal["sse"]
    url: str
    headers: dict[str, str] | None = None
    timeout: float | None = None
    sse_read_timeout: float | None = None
    session_kwargs: dict[str, Any] | None = None


class StdioMCPCfg(BaseModel):
    """标准输入输出传输方式的 MCP 服务配置"""

    transport: Literal["stdio"]
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    encoding: str | None = None
    encoding_error_handler: Literal["strict", "ignore", "replace"] | None = None
    session_kwargs: dict[str, Any] | None = None


class WebsocketMCPCfg(BaseModel):
    """WebSocket 传输方式的 MCP 服务配置"""

    transport: Literal["websocket"]
    url: str
    session_kwargs: dict[str, Any] | None = None


class StreamableHttpMCPCfg(BaseModel):
    """可流式 HTTP 传输方式的 MCP 服务配置"""

    transport: Literal["streamable_http"]
    url: str
    headers: dict[str, str] | None = None
    timeout: timedelta | None = None
    sse_read_timeout: timedelta | None = None
    terminate_on_close: bool | None = None
    session_kwargs: dict[str, Any] | None = None


MCPCfg = Annotated[
    SSEMCPCfg | StdioMCPCfg | WebsocketMCPCfg | StreamableHttpMCPCfg,
    Field(discriminator="transport"),
]


class Cfg(BaseModel):
    """应用全局配置"""

    # 应用基础配置
    port: int
    cors_origins: list[str]
    log: LogCfg

    # 数据连接与检索配置
    doris: DBConfig
    auth_postgresql: DBConfig
    meta_postgresql: DBConfig
    langgraph_postgresql: DBConfig
    doris_credentials: DorisCredentialConfig
    elasticsearch: ESConfig
    embedding: EmbeddingConfig

    # 元数据索引配置
    metadata_index: MetadataIndexConfig

    # 后台任务配置
    task_queue: TaskQueueConfig

    # 身份与生命周期配置
    auth: AuthConfig
    lifecycle: LifecycleConfig

    # 查询与沙箱配置
    query: QueryConfig
    sandbox: SandboxConfig

    # 模型与智能体配置
    lm_config: LMConfigCfg
    agent: AgentConfig

    # 外部工具配置
    mcp: dict[str, MCPCfg]

    @model_validator(mode="after")
    def validate_cross_component_invariants(self) -> "Cfg":
        """校验查询、沙箱、目录和数据连接之间的全局约束"""
        if self.query.max_output_bytes > self.sandbox.max_file_bytes:
            raise ValueError("query.max_output_bytes 不能大于 sandbox.max_file_bytes")
        return self


def _load_config() -> Cfg:
    """从 .env 和 app_config.yaml 加载配置"""
    dotenv.load_dotenv(CONFIG_DIR / ".env")
    loaded_cfg = OmegaConf.load(CONFIG_FILE)
    primitive_cfg = OmegaConf.to_container(loaded_cfg, resolve=True)
    return Cfg.model_validate(cast(dict[str, Any], primitive_cfg))


cfg = _load_config()
