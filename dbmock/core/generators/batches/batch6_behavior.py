"""Batch 6: behavior, traffic and inventory facts."""

from __future__ import annotations

from loguru import logger

from ..settings import RunContext


def run(ctx: RunContext) -> None:
    logger.info("Run batch6_behavior")
    # TODO: generate cart/favor/page-view/search/comment/inventory-change

