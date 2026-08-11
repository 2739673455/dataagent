"""数据生成配置"""

from __future__ import annotations

import hashlib
import json
import os
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import dotenv
from sqlalchemy import URL, Engine, create_engine

if TYPE_CHECKING:
    from .database import DorisStreamLoader

ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"
BUSINESS_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")
dotenv.load_dotenv(ENV_FILE)


def _now() -> datetime:
    return datetime.now(tz=BUSINESS_TIMEZONE).replace(tzinfo=None)


def _today() -> date:
    return _now().date()


def _two_years_ago() -> date:
    today = _today()
    try:
        return today.replace(year=today.year - 2)
    except ValueError:
        return today.replace(year=today.year - 2, day=28)


@dataclass(slots=True)
class DorisConfig:
    host: str = field(default_factory=lambda: os.environ["DB_HOST"])
    port: int = field(default_factory=lambda: int(os.environ["DB_PORT"]))
    http_port: int = field(default_factory=lambda: int(os.environ["DB_HTTP_PORT"]))
    user: str = field(default_factory=lambda: os.environ["DB_USER"])
    password: str = field(default_factory=lambda: os.environ["DB_PASSWORD"])
    database: str = field(default_factory=lambda: os.environ["DB_NAME"])

    @property
    def sqlalchemy_url(self) -> URL:
        return URL.create(
            drivername="mysql+pymysql",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )


@dataclass(slots=True)
class GenerateConfig:
    start_date: date = field(default_factory=_two_years_ago)
    end_date: date = field(default_factory=_today)
    batch_size: int = 250_000
    stream_load_workers: int = 4
    seed: int = 42
    user_count: int = 30_000
    spu_count: int = 5_000
    promotion_count: int = 50
    coupon_count: int = 100
    order_detail_count: int = 100_000
    page_view_count: int = 4_000_000
    search_count: int = 600_000
    warehouse_count: int = 12
    data_dir: Path = field(default_factory=lambda: ROOT_DIR / "data")
    is_smoke: bool = False

    def master_data_path(self, filename: str) -> Path:
        return self.data_dir / filename

    @classmethod
    def smoke(cls) -> GenerateConfig:
        end_date = _today()
        return cls(
            start_date=end_date - timedelta(days=6),
            end_date=end_date,
            batch_size=50_000,
            user_count=20,
            spu_count=8,
            promotion_count=3,
            coupon_count=3,
            order_detail_count=60,
            page_view_count=2_000,
            search_count=300,
            warehouse_count=3,
            is_smoke=True,
        )

    def validate(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("开始日期不能晚于结束日期")
        if self.end_date > _today():
            raise ValueError("结束日期不能晚于任务执行日期")
        positive_values = {
            "batch_size": self.batch_size,
            "stream_load_workers": self.stream_load_workers,
            "user_count": self.user_count,
            "spu_count": self.spu_count,
            "promotion_count": self.promotion_count,
            "coupon_count": self.coupon_count,
            "order_detail_count": self.order_detail_count,
            "page_view_count": self.page_view_count,
            "search_count": self.search_count,
            "warehouse_count": self.warehouse_count,
        }
        invalid = [name for name, value in positive_values.items() if value <= 0]
        if invalid:
            raise ValueError(f"以下配置必须大于 0: {', '.join(invalid)}")
        if self.order_detail_count < 20:
            raise ValueError("order_detail_count 不能小于 20")
        required_files = (
            "manifest.json",
            "brands.json",
            "categories.json",
            "geo_regions.json",
            "logistics_companies.json",
            "payment_types.json",
            "shops.json",
            "spus.jsonl",
            "skus.jsonl",
            "lineage.jsonl",
        )
        missing = [
            filename
            for filename in required_files
            if not (self.data_dir / filename).exists()
        ]
        if missing:
            raise ValueError(
                "data 目录不完整，请执行 uv run python -m collection: "
                + ", ".join(missing)
            )
        manifest = json.loads(
            (self.data_dir / "manifest.json").read_text(encoding="utf-8")
        )
        catalog_spu_count = int(manifest.get("counts", {}).get("spus", 0))
        if catalog_spu_count < self.spu_count:
            raise ValueError(
                f"商品目录 SPU 数量不足 required={self.spu_count} "
                f"actual={catalog_spu_count}"
            )


@dataclass(slots=True)
class RunContext:
    db: DorisConfig
    gen: GenerateConfig
    as_of_time: datetime = field(default_factory=_now)
    execution_id: str = field(init=False)
    run_id: str = field(init=False)
    engine: Engine = field(init=False)
    loader: DorisStreamLoader = field(init=False)
    rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        from .database import DorisStreamLoader

        self.gen.validate()
        execution_stamp = self.as_of_time.strftime("%Y%m%d%H%M%S%f")
        manifest = json.loads(
            (self.gen.data_dir / "manifest.json").read_text(encoding="utf-8")
        )
        catalog_hash = str(
            manifest.get("source", {}).get("origins", [{}])[0].get("sha256", "")
        )
        config_payload = {
            "seed": self.gen.seed,
            "user_count": self.gen.user_count,
            "spu_count": self.gen.spu_count,
            "promotion_count": self.gen.promotion_count,
            "coupon_count": self.gen.coupon_count,
            "order_detail_count": self.gen.order_detail_count,
            "page_view_count": self.gen.page_view_count,
            "search_count": self.gen.search_count,
            "warehouse_count": self.gen.warehouse_count,
            "is_smoke": self.gen.is_smoke,
        }
        config_hash = hashlib.sha256(
            json.dumps(config_payload, sort_keys=True).encode()
        ).hexdigest()
        self.execution_id = f"dbmock-exec-{execution_stamp}"
        self.run_id = (
            f"dbmock-{self.gen.start_date:%Y%m%d}-{self.gen.end_date:%Y%m%d}"
            f"-{catalog_hash[:12]}-{config_hash[:12]}"
        )
        self.engine = create_engine(
            self.db.sqlalchemy_url,
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
        )
        self.loader = DorisStreamLoader(self.db, self.execution_id)
        self.rng = random.Random(self.gen.seed)

    @property
    def initial_batch_id(self) -> str:
        return f"{self.run_id}-INIT"

    def period_batch_id(self, period_key: str) -> str:
        return f"{self.run_id}-{period_key}"

    @property
    def data_end_time(self) -> datetime:
        """本次构建允许的最大业务时间"""
        configured_end = datetime.combine(self.gen.end_date, time.max)
        return min(configured_end, self.as_of_time)

    def close(self) -> None:
        self.loader.close()
        self.engine.dispose()
