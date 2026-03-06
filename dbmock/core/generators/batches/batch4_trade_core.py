"""Batch 4: core trade facts."""

from __future__ import annotations

from loguru import logger

from ..settings import RunContext


def run(ctx: RunContext) -> None:
    logger.info("Run batch4_trade_core")
    # TODO: generate order detail + order activity/coupon allocation

