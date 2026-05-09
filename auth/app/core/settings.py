from __future__ import annotations

from pathlib import Path
from typing import Literal

import dotenv
from omegaconf import OmegaConf
from pydantic import BaseModel, Field, field_validator

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent.parent
CONFIG_DIR = ROOT_DIR / "configs"


class AppCfg(BaseModel):
    host: str
    port: int
    web_base_url: str


class DBCfg(BaseModel):
    driver: Literal["sqlite"]
    sqlite: SQLiteCfg | None = None

    @property
    def selected(self) -> SQLiteCfg:
        cfg = getattr(self, self.driver, None)
        if cfg is None:
            raise ValueError(f"数据库驱动 {self.driver} 缺少对应配置")
        return cfg


class SQLiteCfg(BaseModel):
    file_path: Path = Path("data/auth.db")

    @field_validator("file_path", mode="before")
    @classmethod
    def resolve_file_path(cls, value: str | Path) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return ROOT_DIR / path


class AuthCfg(BaseModel):
    secret_key: str
    algorithm: str
    session_expire_days: int
    access_token_expire_days: int
    auth_code_expire_seconds: int
    email_code_expire_seconds: int


class CookieCfg(BaseModel):
    name: str
    secure: bool
    httponly: bool
    samesite: Literal["lax", "strict", "none"]


class CorsCfg(BaseModel):
    origins: list[str] = Field(default_factory=list)


class LogCfg(BaseModel):
    level: str
    max_file_size: str


class AdminCfg(BaseModel):
    role: str
    email: str
    username: str
    password: str


class EmailCfg(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    from_email: str
    from_name: str


class Cfg(BaseModel):
    app: AppCfg
    db: DBCfg
    auth: AuthCfg
    cookie: CookieCfg
    cors: CorsCfg
    log: LogCfg
    admin: AdminCfg
    email: EmailCfg


def _load_config() -> Cfg:
    """从 .env 和 config.yml 加载配置"""
    dotenv.load_dotenv(CONFIG_DIR / ".env")
    raw_cfg = OmegaConf.to_container(OmegaConf.load(CONFIG_DIR / "config.yml"), resolve=True)
    return Cfg.model_validate(raw_cfg)


def reload_config() -> None:
    """热更新：重新加载 .env 和 config.yml 到当前进程"""
    global cfg
    cfg = _load_config()


cfg = _load_config()
