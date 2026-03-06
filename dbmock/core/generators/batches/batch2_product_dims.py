"""Batch 2: product dimensions."""

from __future__ import annotations

from loguru import logger

from ..settings import RunContext


def run(ctx: RunContext) -> None:
    logger.info("Run batch2_product_dims")
    # TODO: generate SPU/SKU
