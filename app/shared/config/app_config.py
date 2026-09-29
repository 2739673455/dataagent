from pathlib import Path
from typing import Any, Literal, cast

import dotenv
from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

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
    """Elasticsearch 连接与向量维度配置。"""

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    embedding_size: int = Field(gt=0)


class EmbeddingConfig(AppConfigModel):
    """嵌入模型服务配置。"""

    base_url: str = Field(min_length=1)
    api_key: SecretStr | None
    model: str = Field(min_length=1)
    timeout: float = Field(gt=0)


# 元数据索引配置。
class MetadataConfig(AppConfigModel):
    """元数据导入互斥配置。"""

    redis_url: SecretStr = Field(min_length=1)


# 模型与智能体配置。
class ModelProfileCfg(AppConfigModel):
    """应用实际使用的语言模型能力。"""

    image_inputs: bool
    max_input_tokens: int = Field(gt=0)


class ModelCfg(AppConfigModel):
    """语言模型配置。"""

    model_provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: SecretStr = Field(min_length=1)
    params: dict[str, Any]
    profile: ModelProfileCfg


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


class SpecialistConfig(AppConfigModel):
    """专业 Agent 模型选择。"""

    model: str = Field(min_length=1)


class AgentConfig(AppConfigModel):
    """多 Agent 运行时配置。"""

    specialists: dict[
        Literal["explorer", "analyst", "reviewer"],
        SpecialistConfig,
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
    metadata: MetadataConfig

    # 模型与智能体配置。
    lm_config: LMConfigCfg
    agent: AgentConfig


def _load_config() -> Cfg:
    """从 .env 和 app_config.yaml 加载配置。"""
    dotenv.load_dotenv(CONFIG_DIR / ".env")
    loaded_cfg = OmegaConf.load(CONFIG_FILE)
    primitive_cfg = OmegaConf.to_container(loaded_cfg, resolve=True)
    return Cfg.model_validate(cast(dict[str, Any], primitive_cfg))


cfg = _load_config()
