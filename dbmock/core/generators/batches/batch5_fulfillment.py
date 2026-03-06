"""Batch 5: fulfillment facts."""

from __future__ import annotations

from loguru import logger

from ..settings import RunContext


def run(ctx: RunContext) -> None:
    logger.info("Run batch5_fulfillment")
    # TODO: generate pay/delivery/refund/refund-pay

