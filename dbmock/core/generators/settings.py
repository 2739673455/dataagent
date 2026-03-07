"""数据生成全局配置。"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import Engine, create_engine

DB_HOST = "127.0.0.1"
DB_PORT = 3306
DB_USER = "root"
DB_PASSWORD = "123321"
DB_NAME = "warehouse"
BATCH_SIZE = 10000
SEED = 42


def _today_iso() -> str:
    return date.today().isoformat()


def _three_years_ago_iso() -> str:
    today = date.today()
    try:
        return today.replace(year=today.year - 3).isoformat()
    except ValueError:
        # 处理闰日回退，例如 2024-02-29 回退到 2021-02-28
        return today.replace(year=today.year - 3, day=28).isoformat()


@dataclass(slots=True)
class DBConfig:
    host: str = DB_HOST
    port: int = DB_PORT
    user: str = DB_USER
    password: str = DB_PASSWORD
    database: str = DB_NAME

    @property
    def db_url(self) -> str:
        return (
            f"mysql+pymysql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


@dataclass(slots=True)
class GenerateConfig:
    batch_size: int = BATCH_SIZE
    seed: int = SEED
    start_date: str = field(default_factory=_three_years_ago_iso)
    end_date: str = field(default_factory=_today_iso)
    seed_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent / "seeds"
    )


@dataclass(slots=True)
class RunContext:
    db: DBConfig
    gen: GenerateConfig
    engine: Engine = field(init=False)
    rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self.engine = create_engine(self.db.db_url, pool_pre_ping=True)
        self.rng = random.Random(self.gen.seed)
