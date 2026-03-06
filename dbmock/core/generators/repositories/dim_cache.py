"""Dimension cache repository."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DimCache:
    shop_ids: list[int] = field(default_factory=list)
    category_ids: list[int] = field(default_factory=list)
    brand_ids: list[int] = field(default_factory=list)
    sku_ids: list[int] = field(default_factory=list)
    spu_ids: list[int] = field(default_factory=list)
    user_ids: list[int] = field(default_factory=list)

