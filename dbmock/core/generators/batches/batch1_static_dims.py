"""Batch 1: static dimensions loaded from frozen seed files."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import Date, DateTime, MetaData, Numeric, String, select

from ..settings import RunContext
from ..utils.loaders import bulk_insert

TABLE_TO_SEED_FILE = {
    "dwd_dim_shop_info_df": "shops.json",
    "dwd_dim_category_info_df": "categories.json",
    "dwd_dim_brand_info_df": "brands.json",
    "dwd_dim_payment_type_df": "payment_types.json",
    "dwd_dim_logistics_company_df": "logistics_companies.json",
    "dwd_dim_geo_region_df": "geo_regions.json",
}

REQUIRED_FIELDS = {
    "dwd_dim_shop_info_df": [
        "shop_id",
        "shop_name",
        "shop_type",
        "seller_id",
        "seller_name",
        "industry_type",
        "shop_status",
    ],
    "dwd_dim_category_info_df": [
        "category_id",
        "category_name",
        "category_level",
        "root_category_id",
        "root_category_name",
        "is_leaf",
        "status",
    ],
    "dwd_dim_brand_info_df": ["brand_id", "brand_name", "status"],
    "dwd_dim_payment_type_df": [
        "payment_type_code",
        "payment_type_name",
        "is_online",
        "is_installment",
        "status",
    ],
    "dwd_dim_logistics_company_df": [
        "logistics_company_id",
        "logistics_company_code",
        "logistics_company_name",
        "logistics_type",
        "status",
    ],
    "dwd_dim_geo_region_df": [
        "region_code",
        "region_name",
        "region_level",
        "status",
    ],
}

UNIQUE_KEYS = {
    "dwd_dim_shop_info_df": "shop_id",
    "dwd_dim_category_info_df": "category_id",
    "dwd_dim_brand_info_df": "brand_id",
    "dwd_dim_payment_type_df": "payment_type_code",
    "dwd_dim_logistics_company_df": "logistics_company_id",
    "dwd_dim_geo_region_df": "region_code",
}

SHOP_TYPES = {"自营", "旗舰店", "专卖店", "普通店"}
SHOP_STATUS = {"营业", "关店"}
CATEGORY_LEVELS = {"一级", "二级", "三级"}
LOGISTICS_TYPES = {"快递", "同城", "冷链", "国际"}


def _iter_dates(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _existing_dates(conn, table, start_date: date, end_date: date) -> set[date]:
    stmt = (
        select(table.c.etl_date)
        .where(table.c.etl_date >= start_date, table.c.etl_date <= end_date)
        .distinct()
    )
    return set(conn.execute(stmt).scalars().all())


def _load_seed(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise ValueError(f"Seed file {path} must contain a JSON array")
    if not all(isinstance(row, dict) for row in payload):
        raise ValueError(f"Seed file {path} must contain JSON objects")
    return payload


def _validate_required_fields(table_name: str, rows: list[dict[str, Any]]) -> None:
    required = REQUIRED_FIELDS[table_name]
    for idx, row in enumerate(rows, start=1):
        missing = [
            field for field in required if field not in row or row[field] in ("", None)
        ]
        if missing:
            raise ValueError(
                f"{table_name} row {idx} missing required fields: {missing}"
            )


def _validate_unique_key(table_name: str, rows: list[dict[str, Any]]) -> None:
    key = UNIQUE_KEYS[table_name]
    seen: set[Any] = set()
    for row in rows:
        value = row[key]
        if value in seen:
            raise ValueError(f"{table_name} duplicate seed key: {key}={value}")
        seen.add(value)


def _validate_lengths(table_name: str, rows: list[dict[str, Any]], table) -> None:
    for idx, row in enumerate(rows, start=1):
        for col in table.columns:
            if col.name not in row:
                continue
            value = row[col.name]
            if (
                isinstance(col.type, String)
                and isinstance(value, str)
                and col.type.length
            ):
                if len(value) > col.type.length:
                    raise ValueError(
                        f"{table_name} row {idx} field {col.name} exceeds max length {col.type.length}"
                    )


def _normalize_row(table, row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for col in table.columns:
        if col.name in {"id", "etl_date", "created_at", "updated_at"}:
            continue
        if col.name not in row:
            continue
        value = row[col.name]
        if isinstance(value, float):
            value = Decimal(str(value))
        elif isinstance(col.type, Numeric) and isinstance(value, str):
            value = Decimal(value)
        elif isinstance(col.type, DateTime) and isinstance(value, str):
            value = datetime.fromisoformat(value)
        elif isinstance(col.type, Date) and isinstance(value, str):
            value = date.fromisoformat(value)
        normalized[col.name] = value
    return normalized


def _validate_categories(rows: list[dict[str, Any]]) -> set[str]:
    category_ids = {row["category_id"] for row in rows}
    level1_names: set[str] = set()
    for row in rows:
        if row["category_level"] not in CATEGORY_LEVELS:
            raise ValueError(f"Invalid category level: {row['category_level']}")
        if row["category_level"] == "一级":
            level1_names.add(row["category_name"])
        parent_id = row.get("parent_category_id")
        if row["category_level"] != "一级" and parent_id not in category_ids:
            raise ValueError(
                f"Missing parent category for category_id={row['category_id']}"
            )
        if row["category_level"] == "三级" and row.get("is_leaf") != 1:
            raise ValueError(
                f"三级类目必须是叶子节点: category_id={row['category_id']}"
            )
    return level1_names


def _validate_shops(rows: list[dict[str, Any]], level1_names: set[str]) -> None:
    for row in rows:
        if row["shop_type"] not in SHOP_TYPES:
            raise ValueError(f"Invalid shop_type: {row['shop_type']}")
        if row["shop_status"] not in SHOP_STATUS:
            raise ValueError(f"Invalid shop_status: {row['shop_status']}")
        if row["industry_type"] not in level1_names:
            raise ValueError(
                f"Shop industry_type must map to level1 category: {row['shop_name']} -> {row['industry_type']}"
            )


def _validate_payments(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if row["is_online"] not in {0, 1}:
            raise ValueError(
                f"Invalid is_online for payment: {row['payment_type_code']}"
            )
        if row["is_installment"] not in {0, 1}:
            raise ValueError(
                f"Invalid is_installment for payment: {row['payment_type_code']}"
            )
        if row["status"] not in {0, 1}:
            raise ValueError(f"Invalid payment status: {row['payment_type_code']}")


def _validate_logistics(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if row["logistics_type"] not in LOGISTICS_TYPES:
            raise ValueError(
                f"Invalid logistics_type for company {row['logistics_company_code']}: {row['logistics_type']}"
            )
        if row.get("is_trace_supported") not in {0, 1, None}:
            raise ValueError(
                f"Invalid is_trace_supported for company {row['logistics_company_code']}"
            )


def _validate_regions(rows: list[dict[str, Any]]) -> None:
    region_codes = {row["region_code"] for row in rows}
    for row in rows:
        if row["region_level"] not in {1, 2, 3, 4}:
            raise ValueError(f"Invalid region_level: {row['region_code']}")
        parent_code = row.get("parent_region_code")
        if row["region_level"] > 1 and parent_code not in region_codes:
            raise ValueError(
                f"Missing parent region for region_code={row['region_code']}"
            )


def _validate_seed_bundle(seed_rows: dict[str, list[dict[str, Any]]], tables) -> None:
    level1_names = _validate_categories(seed_rows["dwd_dim_category_info_df"])
    _validate_shops(seed_rows["dwd_dim_shop_info_df"], level1_names)
    _validate_payments(seed_rows["dwd_dim_payment_type_df"])
    _validate_logistics(seed_rows["dwd_dim_logistics_company_df"])
    _validate_regions(seed_rows["dwd_dim_geo_region_df"])

    for table_name, rows in seed_rows.items():
        _validate_required_fields(table_name, rows)
        _validate_unique_key(table_name, rows)
        _validate_lengths(table_name, rows, tables[table_name])


def run(ctx: RunContext) -> None:
    logger.info("Run batch1_static_dims")
    metadata = MetaData()
    table_names = list(TABLE_TO_SEED_FILE.keys())
    metadata.reflect(bind=ctx.engine, only=table_names)
    tables = {name: metadata.tables[name] for name in table_names}

    seed_rows: dict[str, list[dict[str, Any]]] = {}
    for table_name, file_name in TABLE_TO_SEED_FILE.items():
        seed_path = ctx.gen.seed_dir / file_name
        if not seed_path.exists():
            raise FileNotFoundError(f"Missing seed file: {seed_path}")
        rows = _load_seed(seed_path)
        seed_rows[table_name] = [
            _normalize_row(tables[table_name], row) for row in rows
        ]
        logger.info(
            "Loaded {} rows from {}", len(seed_rows[table_name]), seed_path.name
        )

    _validate_seed_bundle(seed_rows, tables)

    start_date = date.fromisoformat(ctx.gen.start_date)
    end_date = date.fromisoformat(ctx.gen.end_date)

    with ctx.engine.begin() as conn:
        for table_name in table_names:
            table = tables[table_name]
            existing_dates = _existing_dates(conn, table, start_date, end_date)
            total_inserted = 0
            for etl_date in _iter_dates(start_date, end_date):
                if etl_date in existing_dates:
                    continue
                snapshot_rows = [
                    row | {"etl_date": etl_date} for row in seed_rows[table_name]
                ]
                total_inserted += bulk_insert(conn, table, snapshot_rows)

            logger.info(
                "{} -> seed_rows={}, inserted_rows={}",
                table_name,
                len(seed_rows[table_name]),
                total_inserted,
            )
