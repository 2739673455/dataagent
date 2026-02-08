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


# 腾讯云 COS
class COSCfg(BaseModel):
    bucket: str
    secret_id: str  # 腾讯云COS SECRET-ID
    secret_key: str  # 腾讯云COS SECRET-KEY
    region: str  # 存储桶所在地域
    token: str | None = None  # 临时密钥Token，如不使用则置为None
    scheme: str  # 访问协议，http或https


class Cfg(BaseModel):
    db: DBCfg
    log: LogCfg
    cos: COSCfg
    authentication_url: str
    encryption_key: str
    cors_origins: list[str]


CONFIG_DIR = Path(__file__).parent / "configs"  # 配置文件目录
dotenv.load_dotenv(CONFIG_DIR / ".env")  # 加载 .env
base_cfg = OmegaConf.load(CONFIG_DIR / "config.yml")  # 加载 config.yml

OmegaConf.resolve(base_cfg)  # 解析插值
CFG = Cfg.model_validate(base_cfg)  # 转换为配置类
