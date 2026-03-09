from pathlib import Path

import dotenv
from omegaconf import OmegaConf
from pydantic import BaseModel

# 路径常量
CURRENT_DIR = Path(__file__).parent  # app
UP1_DIR = CURRENT_DIR.parent  # 项目根目录


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


# 日志
class LogCfg(BaseModel):
    to_console_level: str
    to_console: bool
    to_file_level: str
    to_file: bool
    log_dir: str
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


class LMConfigCfg(BaseModel):
    active: str
    models: dict[str, ModelCfg]


# 认证服务
class AuthServiceCfg(BaseModel):
    base_url: str
    introspection: str


class Cfg(BaseModel):
    db: DBCfg
    log: LogCfg
    mcp: dict[str, MCPCfg]
    lm_config: LMConfigCfg
    auth_service: AuthServiceCfg
    cors_origins: list[str]


CONFIG_DIR = UP1_DIR / "configs"  # 配置文件目录
dotenv.load_dotenv(CONFIG_DIR / ".env")  # 加载 .env
base_cfg = OmegaConf.load(CONFIG_DIR / "config.yml")  # 加载 config.yml

OmegaConf.resolve(base_cfg)  # 解析插值
CFG = Cfg.model_validate(base_cfg)  # 转换为配置类
