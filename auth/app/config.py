from pathlib import Path

import dotenv
from omegaconf import OmegaConf
from pydantic import BaseModel

CONFIG_DIR = Path(__file__).parent / "configs"

dotenv.load_dotenv(CONFIG_DIR / ".env")


# 数据库
class DBCfg(BaseModel):
    host: str
    port: int
    user: str
    password: str
    database: str


class DBCfgs(BaseModel):
    app: DBCfg  # 应用数据库
    auth: DBCfg  # 认证数据库


# 日志
class LogCfg(BaseModel):
    to_console_level: str
    to_console: bool
    to_file_level: str
    to_file: bool
    log_dir: str
    max_file_size: str


class LogCfgs(BaseModel):
    app: LogCfg
    auth: LogCfg


class AuthCfg(BaseModel):
    secret_key: str
    algorithm: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int


class COSCfg(BaseModel):
    bucket: str
    secret_id: str
    secret_key: str
    region: str
    token: str | None
    scheme: str


class Cfg(BaseModel):
    db: DBCfgs
    log: LogCfgs
    auth: AuthCfg
    cos: COSCfg
    encryption_key: str
    cors_origins: list[str]


base_cfg = OmegaConf.load(CONFIG_DIR / "config.yml")  # 加载
OmegaConf.resolve(base_cfg)  # 解析插值
CFG = Cfg.model_validate(base_cfg)  # 转换为配置类
print(CFG)
