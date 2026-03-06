"""Date and datetime helpers."""

from __future__ import annotations

from datetime import date


def parse_date(value: str) -> date:
    return date.fromisoformat(value)

