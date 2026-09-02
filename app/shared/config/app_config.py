from datetime import time, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import dotenv
from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.shared.contracts.analysis import AGENT_TYPES

# 路径常量。
ROOT_DIR = Path(__file__).parents[3]
CONFIG_DIR = ROOT_DIR / "conf"
CONFIG_FILE = CONFIG_DIR / "app_config.yaml"


class AppConfigModel(BaseModel):
    """拒绝未知字段的应用配置基类。"""

    model_config = ConfigDict(extra="forbid")


# 应用基础配置。
class LogCfg(AppConfigModel):
    """日志配置。"""

    level: str = Field(min_length=1)
    rotation: str = Field(min_length=1)


# 数据连接与检索配置。
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


# 元数据索引配置。
class MetadataIndexConfig(AppConfigModel):
    """元数据索引同步策略配置。"""

    value_lookback_seconds: int = Field(gt=0)


# 后台任务配置。
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
        """校验后台任务软硬超时关系。"""
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


# 身份与生命周期配置。
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


# 查询与沙箱配置。
class QueryConfig(AppConfigModel):
    """只读分析查询配置。"""

    data_source: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)
    memory_limit_bytes: int = Field(gt=0)
    batch_size: int = Field(gt=0)
    sample_rows: int = Field(ge=0, le=100)
    query_experience_vector_score_threshold: float = Field(ge=0, le=1)


class SandboxOwnershipConfig(AppConfigModel):
    """沙箱跨进程所有权配置。"""

    redis_url: SecretStr = Field(min_length=1)
    lock_timeout_seconds: float = Field(gt=0)
    wait_timeout_seconds: float = Field(gt=0)
    lease_seconds: float = Field(gt=0)


class SandboxConfig(AppConfigModel):
    """本地 Docker 沙箱配置。"""

    deployment_namespace: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    )
    ownership: SandboxOwnershipConfig
    image: str = Field(min_length=1)
    network_mode: Literal["none", "bridge"]
    memory_limit: str = Field(min_length=1)
    nano_cpus: int = Field(gt=0)
    pids_limit: int = Field(gt=0)
    internal_command_timeout_seconds: int = Field(default=60, gt=0, le=600)
    max_file_bytes: int = Field(gt=0)
    max_user_storage_bytes: int = Field(gt=0)
    volume_driver: str = Field(min_length=1)
    volume_driver_options: dict[str, str]
    max_running_containers: int = Field(gt=0)
    idle_stop_seconds: int = Field(gt=0)
    idle_remove_seconds: int = Field(gt=0)
    cleanup_interval_seconds: int = Field(gt=0)
    cleanup_failure_alert_threshold: int = Field(gt=0)
    stop_containers_on_shutdown: bool

    @model_validator(mode="after")
    def validate_size_limits(self) -> "SandboxConfig":
        """校验沙箱容量限制之间的关系。"""
        if self.max_file_bytes > self.max_user_storage_bytes:
            raise ValueError("max_file_bytes 不能大于 max_user_storage_bytes")
        if self.idle_stop_seconds >= self.idle_remove_seconds:
            raise ValueError("idle_stop_seconds 必须小于 idle_remove_seconds")
        option_fields = {
            "deployment_namespace": self.deployment_namespace,
            "user_id": 1,
            "max_user_storage_bytes": self.max_user_storage_bytes,
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
        if self.volume_driver != "local" and not any(
            "{max_user_storage_bytes}" in value
            for value in self.volume_driver_options.values()
        ):
            raise ValueError("数据卷驱动选项必须包含 max_user_storage_bytes 占位符")
        return self


# 模型与智能体配置。
class ModelProfileCfg(AppConfigModel):
    """应用实际使用的语言模型能力。"""

    image_inputs: bool
    structured_output: bool
    max_input_tokens: int = Field(gt=0)


class ModelCfg(AppConfigModel):
    """语言模型配置。"""

    model_provider: str = Field(min_length=1)
    api_protocol: Literal["chat_completions", "responses"]
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: SecretStr = Field(min_length=1)
    params: dict[str, Any]
    profile: ModelProfileCfg

    @model_validator(mode="after")
    def validate_client_parameters(self) -> "ModelCfg":
        """禁止附加参数覆盖显式模型配置和协议选项。"""
        reserved = {
            "api_key",
            "api_protocol",
            "base_url",
            "max_retries",
            "model",
            "model_name",
            "model_provider",
            "openai_api_base",
            "openai_api_key",
            "output_version",
            "profile",
            "request_timeout",
            "store",
            "streaming",
            "timeout",
            "use_previous_response_id",
            "use_responses_api",
        }
        conflicts = sorted(reserved & self.params.keys())
        if conflicts:
            raise ValueError("params 不能覆盖模型配置字段: " + ", ".join(conflicts))
        if self.api_protocol == "responses" and self.model_provider not in {
            "deepseek",
            "openai",
        }:
            raise ValueError("Responses API 仅支持 model_provider: deepseek 或 openai")
        return self


class LMConfigCfg(AppConfigModel):
    """语言模型集合与激活项配置。"""

    active: str = Field(min_length=1)
    models: dict[str, ModelCfg]

    @model_validator(mode="after")
    def validate_active_model(self) -> "LMConfigCfg":
        """要求默认模型引用已声明的模型配置。"""
        if self.active not in self.models:
            raise ValueError(f"lm_config.active 引用了未知模型: {self.active}")
        return self


class OrchestrationConfig(AppConfigModel):
    """动态专业 Agent 编排限制。"""

    max_parallel_sessions: int = Field(gt=0)
    max_sessions: int = Field(gt=0)
    max_continuations: int = Field(ge=0)


class InterpreterConfig(AppConfigModel):
    """Planner 内嵌解释器配置。"""

    memory_limit_bytes: int = Field(gt=0)


class SpecialistConfig(AppConfigModel):
    """专业 Agent 模型选择。"""

    model: str = Field(min_length=1)


class AgentConfig(AppConfigModel):
    """多 Agent 运行时配置。"""

    orchestration: OrchestrationConfig
    interpreter: InterpreterConfig
    specialists: dict[
        Literal["explorer", "analyst", "reviewer"],
        SpecialistConfig,
    ]


# 外部工具配置。
class SSEMCPCfg(AppConfigModel):
    """SSE 传输方式的 MCP 服务配置。"""

    transport: Literal["sse"]
    url: SecretStr = Field(min_length=1)
    headers: dict[str, SecretStr] | None = None
    timeout: float | None = Field(default=None, gt=0)
    sse_read_timeout: float | None = Field(default=None, gt=0)
    session_kwargs: dict[str, Any] | None = None


class StdioMCPCfg(AppConfigModel):
    """标准输入输出传输方式的 MCP 服务配置。"""

    transport: Literal["stdio"]
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, SecretStr] | None = None
    cwd: str | None = None
    encoding: str | None = None
    encoding_error_handler: Literal["strict", "ignore", "replace"] | None = None
    session_kwargs: dict[str, Any] | None = None


class WebsocketMCPCfg(AppConfigModel):
    """WebSocket 传输方式的 MCP 服务配置。"""

    transport: Literal["websocket"]
    url: SecretStr = Field(min_length=1)
    session_kwargs: dict[str, Any] | None = None


class StreamableHttpMCPCfg(AppConfigModel):
    """可流式 HTTP 传输方式的 MCP 服务配置。"""

    transport: Literal["streamable_http"]
    url: SecretStr = Field(min_length=1)
    headers: dict[str, SecretStr] | None = None
    timeout: timedelta | None = Field(default=None, gt=timedelta(0))
    sse_read_timeout: timedelta | None = Field(default=None, gt=timedelta(0))
    terminate_on_close: bool | None = None
    session_kwargs: dict[str, Any] | None = None


MCPCfg = Annotated[
    SSEMCPCfg | StdioMCPCfg | WebsocketMCPCfg | StreamableHttpMCPCfg,
    Field(discriminator="transport"),
]


class Cfg(AppConfigModel):
    """应用全局配置。"""

    # 应用基础配置。
    port: int = Field(ge=1, le=65535)
    cors_origins: list[str]
    log: LogCfg

    # 数据连接与检索配置。
    doris: DBConfig
    auth_postgresql: DBConfig
    meta_postgresql: DBConfig
    langgraph_postgresql: DBConfig
    doris_credentials: DorisCredentialConfig
    elasticsearch: ESConfig
    embedding: EmbeddingConfig

    # 元数据索引配置。
    metadata_index: MetadataIndexConfig

    # 后台任务配置。
    task_queue: TaskQueueConfig

    # 身份与生命周期配置。
    auth: AuthConfig
    lifecycle: LifecycleConfig

    # 查询与沙箱配置。
    query: QueryConfig
    sandbox: SandboxConfig

    # 模型与智能体配置。
    lm_config: LMConfigCfg
    agent: AgentConfig

    # 外部工具配置。
    mcp: dict[str, MCPCfg]

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
