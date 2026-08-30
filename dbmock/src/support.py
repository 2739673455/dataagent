"""造数公共能力"""

from __future__ import annotations

import hashlib
import json
import warnings
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SAWarning

from .database import DorisStreamLoader

UNKNOWN_SK = -1
UNKNOWN_ID = 0
END_OF_TIME = datetime(9999, 12, 31, 23, 59, 59, 999999)
MONEY_ZERO = Decimal("0.00")
MONEY_QUANT = Decimal("0.01")
PRICE_QUANT = Decimal("0.0001")
AUDIT_TIME_FIELDS = (
    "snapshot_time",
    "event_time",
    "comment_time",
    "refund_pay_request_time",
    "apply_time",
    "delivery_create_time",
    "pay_request_time",
    "coupon_use_time",
    "order_create_time",
    "change_time",
    "price_effective_time",
    "session_end_time",
    "effective_start_time",
    "rule_effective_start_time",
    "register_time",
    "open_time",
    "source_update_time",
)
DW_UPDATE_TABLES = {
    "dim_brand_info",
    "dim_category_info_zip",
    "dim_channel_info",
    "dim_geo_region_zip",
    "dim_logistics_company",
    "dim_page_info",
    "dim_payment_type",
    "dim_seller_info_zip",
    "dim_shop_info_zip",
    "dim_sku_info_zip",
    "dim_spu_info_zip",
    "dim_user_info_zip",
    "dim_user_tag_info",
    "dim_warehouse_info_zip",
}


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def price(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(PRICE_QUANT, rounding=ROUND_HALF_UP)


def start_of_day(value: date) -> datetime:
    return datetime.combine(value, time.min)


def end_of_day(value: date) -> datetime:
    return datetime.combine(value, time.max)


def date_key(value: date | datetime) -> int:
    day = value.date() if isinstance(value, datetime) else value
    return day.year * 10000 + day.month * 100 + day.day


def iter_dates(start_date: date, end_date: date) -> Iterator[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def stable_hash(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_json_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(
        isinstance(row, dict) for row in payload
    ):
        raise ValueError(f"种子文件必须是对象数组: {path}")
    return payload


def iter_jsonl_rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"JSONL行必须是对象: {path}:{line_number}")
            yield row


def dimension_audit(
    attributes: Mapping[str, Any],
    batch_id: str,
) -> dict[str, Any]:
    return {
        "attribute_hash": stable_hash(attributes),
        "source_update_time": None,
        "load_batch_id": batch_id,
    }


def fact_audit(record_id: str, batch_id: str) -> dict[str, Any]:
    return {
        "source_record_id": record_id,
        "load_batch_id": batch_id,
    }


def reflect_tables(engine: Engine) -> dict[str, Table]:
    metadata = MetaData()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Unknown schema content:.*",
            category=SAWarning,
        )
        metadata.reflect(bind=engine)
    return dict(metadata.tables)


def doris_unique_key_columns(
    conn: Connection,
    table_name: str,
) -> list[str]:
    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND column_key = 'UNI'
            ORDER BY ordinal_position
            """
        ),
        {"table_name": table_name},
    )
    return [str(value) for value in rows.scalars()]


def assert_empty(
    conn: Connection,
    tables: Mapping[str, Table],
) -> None:
    populated = []
    for name, table in tables.items():
        if conn.execute(select(1).select_from(table).limit(1)).first() is not None:
            populated.append(name)
    if populated:
        names = ", ".join(sorted(populated))
        raise ValueError(f"目标库不是空库，请先执行 make init-db: {names}")


def load_rows(
    conn: Connection,
    table: Table,
    *,
    where: Any | None = None,
    order_by: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    stmt = select(table)
    if where is not None:
        stmt = stmt.where(where)
    order_columns = list(order_by)
    if order_columns:
        stmt = stmt.order_by(*order_columns)
    return [dict(row) for row in conn.execute(stmt).mappings()]


def build_version_index(
    rows: Iterable[Mapping[str, Any]], business_key: str
) -> dict[Any, list[dict[str, Any]]]:
    index: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        index[row[business_key]].append(dict(row))
    for versions in index.values():
        versions.sort(key=lambda row: row["effective_start_time"])
    return dict(index)


def version_at(
    index: Mapping[Any, list[dict[str, Any]]], business_id: Any, event_time: datetime
) -> dict[str, Any]:
    for row in reversed(index[business_id]):
        if row["effective_start_time"] <= event_time < row["effective_end_time"]:
            return row
    raise ValueError(f"维度版本未命中: business_id={business_id}, time={event_time}")


class TableWriter:
    """按表缓冲并批量写入"""

    def __init__(
        self,
        loader: DorisStreamLoader,
        batch_size: int,
        load_workers: int,
        start_date: date,
        as_of_time: datetime,
    ) -> None:
        self.loader = loader
        self.batch_size = batch_size
        self.load_workers = load_workers
        self.start_time = start_of_day(start_date)
        self.as_of_time = as_of_time
        self.buffers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.counts: dict[str, int] = defaultdict(int)

    def _business_time(self, row: Mapping[str, Any]) -> datetime:
        candidates = []
        for field in AUDIT_TIME_FIELDS:
            value = row.get(field)
            if isinstance(value, datetime):
                candidates.append(value)
            elif isinstance(value, date):
                candidates.append(end_of_day(value))
        if candidates:
            return min(max(max(candidates), self.start_time), self.as_of_time)
        biz_date = row.get("biz_date")
        if isinstance(biz_date, datetime):
            return min(max(biz_date, self.start_time), self.as_of_time)
        if isinstance(biz_date, date):
            value_time = end_of_day(biz_date)
            return min(max(value_time, self.start_time), self.as_of_time)
        return self.start_time

    def _audit_row(self, table_name: str, row: dict[str, Any]) -> dict[str, Any]:
        if "load_batch_id" not in row:
            return row
        business_time = self._business_time(row)
        audit_key = str(
            row.get("source_record_id")
            or row.get("attribute_hash")
            or stable_hash(row)
        )
        digest = int(hashlib.sha256(audit_key.encode()).hexdigest()[:8], 16)
        if "snapshot_time" in row:
            delay_minutes = 60 + digest % 240
        elif "biz_date" in row:
            delay_minutes = 2 + digest % 180
        else:
            delay_minutes = 15 + digest % 720
        load_time = min(
            business_time + timedelta(minutes=delay_minutes),
            self.as_of_time,
        )
        if "dw_load_time" not in row:
            row["dw_load_time"] = load_time
        if table_name in DW_UPDATE_TABLES:
            row["dw_update_time"] = load_time
        if "source_update_time" in row and row["source_update_time"] is None:
            row["source_update_time"] = business_time
        return row

    def add(self, table_name: str, row: dict[str, Any]) -> None:
        buffer = self.buffers[table_name]
        buffer.append(self._audit_row(table_name, dict(row)))
        if len(buffer) >= self.batch_size:
            self.flush(table_name)

    def add_many(self, table_name: str, rows: Iterable[dict[str, Any]]) -> None:
        for row in rows:
            self.add(table_name, row)

    def flush(self, table_name: str) -> None:
        rows = self.buffers[table_name]
        if not rows:
            return
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[tuple(sorted(row))].append(row)
        for group in groups.values():
            self.loader.load(table_name, group)
        self.counts[table_name] += len(rows)
        rows.clear()

    def flush_all(self) -> dict[str, int]:
        table_names = [name for name, rows in self.buffers.items() if rows]
        if not table_names:
            return dict(self.counts)
        with ThreadPoolExecutor(
            max_workers=min(self.load_workers, len(table_names)),
            thread_name_prefix="doris-stream-load",
        ) as executor:
            futures = {
                table_name: executor.submit(self._load_buffer, table_name)
                for table_name in table_names
            }
            for table_name, future in futures.items():
                future.result()
                self.counts[table_name] += len(self.buffers[table_name])
                self.buffers[table_name].clear()
        return dict(self.counts)

    def _load_buffer(self, table_name: str) -> None:
        rows = self.buffers[table_name]
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[tuple(sorted(row))].append(row)
        for group in groups.values():
            self.loader.load(table_name, group)
