"""Database load helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Table
from sqlalchemy.engine import Connection


def bulk_insert(conn: Connection, table: Table, rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    conn.execute(table.insert(), list(rows))
    return len(rows)
