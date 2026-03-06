"""Global settings for data generation."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Engine, create_engine


def _today_iso() -> str:
    return date.today().isoformat()


def _three_years_ago_iso() -> str:
    today = date.today()
    try:
        return today.replace(year=today.year - 3).isoformat()
    except ValueError:
        # Handle leap day: 2024-02-29 -> 2021-02-28
        return today.replace(year=today.year - 3, day=28).isoformat()


@dataclass(slots=True)
class DBConfig:
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = "123321"
    database: str = "warehouse"

    @property
    def db_url(self) -> str:
        return (
            f"mysql+pymysql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


@dataclass(slots=True)
class GenerateConfig:
    seed: int = 42
    start_date: str = field(default_factory=_three_years_ago_iso)
    end_date: str = field(default_factory=_today_iso)
    batch_size: int = 2000


@dataclass(slots=True)
class RunContext:
    db: DBConfig
    gen: GenerateConfig
    engine: Engine = field(init=False)
    rng: random.Random = field(init=False)
    etl_date: date = field(init=False)

    def __post_init__(self) -> None:
        self.engine = create_engine(self.db.db_url, pool_pre_ping=True)
        self.rng = random.Random(self.gen.seed)
        self.etl_date = date.fromisoformat(self.gen.end_date)
