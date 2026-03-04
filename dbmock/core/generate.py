from __future__ import annotations

import argparse
import datetime as dt
import decimal
import random
import sys
from importlib import import_module
from pathlib import Path
from typing import Iterable

from loguru import logger
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    text,
)

from .entities import warehouse

# Ensure "core.*" imports work when script is launched as `python core/generate_data.py`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate mock data from model module and write into database."
    )
    parser.add_argument(
        "--model-module",
        default="core.entities.warehouse",
        help="Python module that contains SQLAlchemy Base. e.g. core.entities.warehouse",
    )
    parser.add_argument("--db-url", help="SQLAlchemy database URL")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="123321")
    parser.add_argument("--database", default="warehouse")
    parser.add_argument(
        "--rows-per-table",
        type=int,
        default=100,
        help="How many rows to generate for each table",
    )
    parser.add_argument(
        "--tables",
        default="",
        help="Comma-separated table names to generate; empty means all tables",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Truncate table before inserting",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print plan, do not write database",
    )
    return parser.parse_args()


def get_db_url(args: argparse.Namespace) -> str:
    if args.db_url:
        return args.db_url
    return (
        f"mysql+pymysql://{args.user}:{args.password}"
        f"@{args.host}:{args.port}/{args.database}"
    )


def load_base(model_module: str):
    try:
        module = import_module(model_module)
    except ModuleNotFoundError:
        if model_module.startswith("core."):
            module = import_module(model_module.removeprefix("core."))
        else:
            module = import_module(f"core.{model_module}")
    base = getattr(module, "Base", None)
    if base is None:
        raise ValueError(f"{model_module} does not define Base")
    return base


def iter_model_classes(base, tables: set[str]) -> Iterable[type]:
    classes = []
    for mapper in base.registry.mappers:
        cls = mapper.class_
        table_name = cls.__table__.name
        if tables and table_name not in tables:
            continue
        classes.append(cls)
    return sorted(classes, key=lambda x: x.__table__.name)


def should_skip_column(col) -> bool:
    if col.primary_key and col.autoincrement:
        return True
    if col.server_default is not None:
        return True
    if col.default is not None:
        return True
    return False


def clip_str(v: str, max_len: int | None) -> str:
    if max_len is None:
        return v
    return v[:max_len]


def rand_decimal(rng: random.Random, scale: int | None) -> decimal.Decimal:
    actual_scale = 2 if scale is None else scale
    amount = rng.randint(100, 500000)
    return (decimal.Decimal(amount) / (decimal.Decimal(10) ** actual_scale)).quantize(
        decimal.Decimal(10) ** -actual_scale
    )


def generate_value(col, row_no: int, rng: random.Random):
    col_name = col.name.lower()
    col_type = col.type
    base_date = dt.date(2026, 3, 1)
    base_dt = dt.datetime(2026, 3, 1, 10, 0, 0)

    if isinstance(col_type, Date):
        return base_date - dt.timedelta(days=row_no % 90)

    if isinstance(col_type, DateTime):
        return base_dt - dt.timedelta(days=row_no % 180, seconds=(row_no * 97) % 86400)

    if isinstance(col_type, Numeric):
        scale = getattr(col_type, "scale", 2)
        return rand_decimal(rng, scale)

    if isinstance(col_type, Float):
        return round(rng.uniform(1.0, 9999.0), 4)

    if isinstance(col_type, (Integer, BigInteger)):
        if col_name.startswith("is_"):
            return row_no % 2
        if "status" in col_name:
            return 1
        if "level" in col_name:
            return (row_no % 5) + 1
        if "num" in col_name or "cnt" in col_name:
            return (row_no % 20) + 1
        if col_name.endswith("_id") or col_name == "id":
            return row_no + 1
        return row_no + 100

    if isinstance(col_type, Boolean):
        return row_no % 2 == 1

    if isinstance(col_type, JSON):
        return {"k": f"{col_name}_{row_no + 1}", "n": row_no + 1}

    if isinstance(col_type, Text):
        return f"{col_name}_{row_no + 1}"

    if isinstance(col_type, String):
        max_len = col_type.length
        if "phone" in col_name:
            value = f"1{(3000000000 + row_no):010d}"
        elif "email" in col_name:
            value = f"user{row_no + 1}@example.com"
        elif "channel_code" in col_name:
            value = f"ch_{row_no + 1}"
        elif "event_no" in col_name:
            value = f"EVT_{row_no + 1}"
        elif "code" in col_name:
            value = f"code_{row_no + 1}"
        else:
            value = f"{col_name}_{row_no + 1}"
        return clip_str(value, max_len)

    return None


def build_row(model_cls, row_no: int, rng: random.Random) -> dict:
    row = {}
    for col in model_cls.__table__.columns:
        if should_skip_column(col):
            continue

        value = generate_value(col, row_no, rng)

        if value is None and not col.nullable:
            if isinstance(col.type, String):
                value = clip_str(f"{col.name}_{row_no + 1}", col.type.length)
            elif isinstance(col.type, (Integer, BigInteger)):
                value = row_no + 1
            elif isinstance(col.type, Numeric):
                value = decimal.Decimal("1.00")
            elif isinstance(col.type, Date):
                value = dt.date(2026, 3, 1)
            elif isinstance(col.type, DateTime):
                value = dt.datetime(2026, 3, 1, 0, 0, 0)
            else:
                value = ""

        row[col.name] = value
    return row


def generate_rows(model_cls, count: int, rng: random.Random) -> list[dict]:
    return [build_row(model_cls, i, rng) for i in range(count)]


def main():
    args = parse_args()
    rng = random.Random(args.seed)
    target_tables = {v.strip() for v in args.tables.split(",") if v.strip()}
    db_url = get_db_url(args)

    base = load_base(args.model_module)
    model_classes = list(iter_model_classes(base, target_tables))
    if not model_classes:
        raise ValueError("No model classes found. Check --model-module or --tables")

    logger.info("Target DB: {}", db_url)
    logger.info("Model module: {}", args.model_module)
    logger.info("Rows per table: {}", args.rows_per_table)
    logger.info("Table count: {}", len(model_classes))

    for cls in model_classes:
        logger.info("Plan -> {} : {} rows", cls.__table__.name, args.rows_per_table)

    if args.dry_run:
        logger.info("Dry run finished.")
        return

    engine = create_engine(db_url, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        try:
            for cls in model_classes:
                table = cls.__table__
                if args.truncate:
                    logger.info("Truncate {}", table.name)
                    conn.execute(text(f"TRUNCATE TABLE `{table.name}`"))

                rows = generate_rows(cls, args.rows_per_table, rng)
                logger.info("Insert {} rows into {}", len(rows), table.name)
                conn.execute(table.insert(), rows)
        finally:
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    logger.info("Data generation completed.")


if __name__ == "__main__":
    main()
