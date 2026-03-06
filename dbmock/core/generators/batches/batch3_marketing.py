"""Batch 3: marketing dimensions."""

from __future__ import annotations

from loguru import logger

from ..settings import RunContext


def run(ctx: RunContext) -> None:
    logger.info("Run batch3_marketing")
    # TODO: generate promotion/coupon

