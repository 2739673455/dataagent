"""数据库写入工具。"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Table
from sqlalchemy.engine import Connection


def bulk_insert(
    conn: Connection,
    table: Table,
    rows: Sequence[dict[str, Any]],
    batch_size: int,
) -> int:
    """按批次写入数据，避免一次性提交过大批量。"""
    if not rows:
        return 0
    if batch_size <= 0:
        batch_size = len(rows)
    for start in range(0, len(rows), batch_size):
        conn.execute(table.insert(), list(rows[start : start + batch_size]))
    return len(rows)
