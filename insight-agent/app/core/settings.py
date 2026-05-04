from pathlib import Path

import dotenv
from omegaconf import OmegaConf
from pydantic import BaseModel

# 路径常量
CURRENT_DIR = Path(__file__).parent  # core
ROOT_DIR = CURRENT_DIR.parent.parent  # 项目根目录
CONFIG_DIR = ROOT_DIR / "configs"  # 配置文件目录


# 数据库
class MySQLCfg(BaseModel):
    host: str
    port: int
    user: str
    password: str
    database: str


class DBCfg(BaseModel):
    driver: str
    configs: dict[str, MySQLCfg]


# Redis 配置
class RedisCfg(BaseModel):
    host: str
    port: int
    password: str
    db: int


# 日志
class LogCfg(BaseModel):
    level: str
    max_file_size: str


# MCP 工具配置
class MCPCfg(BaseModel):
    transport: str
    url: str


# 模型配置
class ModelCfg(BaseModel):
    model: str
    base_url: str
    api_key: str
    params: dict
    profile: dict


class LMConfigCfg(BaseModel):
    active: str
    models: dict[str, ModelCfg]


# 认证服务
class AuthServiceCfg(BaseModel):
    base_url: str
    introspection: str


class DataAgentCfg(BaseModel):
    base_url: str
    query: str


class Cfg(BaseModel):
    db: DBCfg
    redis: RedisCfg
    log: LogCfg
    mcp: dict[str, MCPCfg]
    lm_config: LMConfigCfg
    auth_service: AuthServiceCfg
    data_agent: DataAgentCfg
    cors_origins: list[str]
    port: int


def _load_config() -> Cfg:
    """从 .env 和 config.yml 加载配置"""
    dotenv.load_dotenv(CONFIG_DIR / ".env")
    raw_cfg = OmegaConf.to_container(
        OmegaConf.load(CONFIG_DIR / "config.yml"), resolve=True
    )
    return Cfg.model_validate(raw_cfg)


def reload_config() -> None:
    """热更新：重新加载 .env 和 config.yml 到当前进程"""
    global cfg
    cfg = _load_config()


cfg = _load_config()
