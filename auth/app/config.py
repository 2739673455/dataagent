from pathlib import Path

import dotenv
from omegaconf import OmegaConf
from pydantic import BaseModel


# 数据库
class DBCfg(BaseModel):
    host: str
    port: int
    user: str
    password: str
    database: str


# 日志
class LogCfg(BaseModel):
    to_console_level: str
    to_console: bool
    to_file_level: str
    to_file: bool
    log_dir: str
    max_file_size: str


# 认证配置
class AuthCfg(BaseModel):
    secret_key: str
    algorithm: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int


class Cfg(BaseModel):
    db: DBCfg
    log: LogCfg
    auth: AuthCfg
    cors_origins: list[str]


CONFIG_DIR = Path(__file__).parent.parent / "configs"  # 配置文件目录
dotenv.load_dotenv(CONFIG_DIR / ".env")  # 加载 .env
base_cfg = OmegaConf.load(CONFIG_DIR / "config.yml")  # 加载 config.yml

OmegaConf.resolve(base_cfg)  # 解析插值
CFG = Cfg.model_validate(base_cfg)  # 转换为配置类
