"""Business ID generators."""

from __future__ import annotations


def sequence(start: int, size: int) -> list[int]:
    return list(range(start, start + size))
