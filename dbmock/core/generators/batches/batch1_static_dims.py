"""Batch 1: static dimensions."""

from __future__ import annotations

from loguru import logger

from ..settings import RunContext


def run(ctx: RunContext) -> None:
    logger.info("Run batch1_static_dims")
    # TODO: generate shop/category/brand/payment/logistics/geo-region data

