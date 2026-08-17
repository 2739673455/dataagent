from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import dotenv
from omegaconf import OmegaConf
from pydantic import BaseModel, Field, model_validator

# 路径常量
ROOT_DIR = Path(__file__).parents[2]
CONFIG_DIR = ROOT_DIR / "conf"
CONFIG_FILE = CONFIG_DIR / "app_config.yaml"


class LogCfg(BaseModel):
    """日志配置"""

    level: str
    rotation: str


class DBConfig(BaseModel):
    """数据库连接配置"""

    host: str
    port: int
    user: str
    password: str
    database: str


class ESConfig(BaseModel):
    """Elasticsearch 连接与索引配置"""

    host: str
    port: int
    column_index: str
    metric_index: str
    value_index: str
    embedding_size: int


class EmbeddingConfig(BaseModel):
    """嵌入模型服务配置"""

    base_url: str
    api_key: str | None
    model: str
    timeout: float


class SandboxConfig(BaseModel):
    """本地 Docker 沙盒配置"""

    deployment_namespace: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    )
    image: str
    build_context: str
    build_network_mode: str
    rebuild_image: bool
    node_version: str
    node_download_base: str
    pypi_index_url: str
    npm_registry: str
    memory_limit: str
    nano_cpus: int = Field(gt=0)
    pids_limit: int = Field(gt=0)
    network_mode: Literal["none", "bridge"]
    max_output_bytes: int = Field(ge=4 * 1024 * 1024)
    max_capture_bytes: int = Field(gt=0)
    max_file_bytes: int = Field(gt=0)
    max_workspace_bytes: int = Field(gt=0)
    workspace_quota_mode: Literal["application", "volume_driver"]
    volume_driver: str = Field(min_length=1)
    volume_driver_options: dict[str, str]
    idle_stop_seconds: int = Field(gt=0)
    idle_remove_seconds: int = Field(gt=0)
    cleanup_interval_seconds: int = Field(gt=0)
    cleanup_failure_alert_threshold: int = Field(gt=0)
    max_running_containers: int = Field(gt=0)
    max_capacity_waiters: int = Field(gt=0)
    capacity_wait_timeout_seconds: float = Field(gt=0)
    stop_containers_on_shutdown: bool

    @model_validator(mode="after")
    def validate_size_limits(self) -> "SandboxConfig":
        """校验沙盒容量限制之间的关系"""
        if self.max_output_bytes > self.max_capture_bytes:
            raise ValueError("max_output_bytes must not exceed max_capture_bytes")
        if self.max_capture_bytes > self.max_file_bytes:
            raise ValueError("max_capture_bytes must not exceed max_file_bytes")
        if self.max_file_bytes > self.max_workspace_bytes:
            raise ValueError("max_file_bytes must not exceed max_workspace_bytes")
        if self.idle_stop_seconds >= self.idle_remove_seconds:
            raise ValueError("idle_stop_seconds must be less than idle_remove_seconds")
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
            placeholder = exc.args[0] if exc.args else "invalid format"
            raise ValueError(
                f"invalid volume driver option template: {placeholder}"
            ) from exc
        if any(not key or not value for key, value in rendered_options.items()):
            raise ValueError("volume driver options must not contain empty values")
        if self.workspace_quota_mode == "volume_driver" and not any(
            "{max_workspace_bytes}" in value
            for value in self.volume_driver_options.values()
        ):
            raise ValueError(
                "volume_driver quota mode requires a max_workspace_bytes placeholder"
            )
        if (
            self.workspace_quota_mode == "volume_driver"
            and self.volume_driver == "local"
        ):
            raise ValueError(
                "volume_driver quota mode requires a quota-capable external driver"
            )
        return self


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


class Cfg(BaseModel):
    """应用全局配置"""

    log: LogCfg
    doris: DBConfig
    meta_postgresql: DBConfig
    langgraph_postgresql: DBConfig
    elasticsearch: ESConfig
    embedding: EmbeddingConfig
    sandbox: SandboxConfig
    lm_config: LMConfigCfg
    mcp: dict[str, MCPCfg]
    cors_origins: list[str]
    port: int


def _load_config() -> Cfg:
    """从 .env 和 app_config.yaml 加载配置"""
    dotenv.load_dotenv(CONFIG_DIR / ".env")
    loaded_cfg = OmegaConf.load(CONFIG_FILE)
    primitive_cfg = OmegaConf.to_container(loaded_cfg, resolve=True)
    return Cfg.model_validate(cast(dict[str, Any], primitive_cfg))


def reload_config() -> None:
    """重新加载配置"""
    global cfg
    cfg = _load_config()


cfg = _load_config()
