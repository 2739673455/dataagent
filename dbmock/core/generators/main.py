"""Main orchestrator for batch-based data generation."""

from __future__ import annotations

from loguru import logger

from .batches import (
    batch1_static_dims,
    batch2_product_dims,
    batch3_marketing,
    batch4_trade_core,
    batch5_fulfillment,
    batch6_behavior,
)
from .settings import DBConfig, GenerateConfig, RunContext

GENERATORS = [
    ("static_dims", batch1_static_dims.run),
    ("product_dims", batch2_product_dims.run),
    ("marketing", batch3_marketing.run),
    ("trade_core", batch4_trade_core.run),
    ("fulfillment", batch5_fulfillment.run),
    ("behavior", batch6_behavior.run),
]


def main() -> None:
    db_cfg = DBConfig()
    gen_cfg = GenerateConfig()
    ctx = RunContext(db=db_cfg, gen=gen_cfg)

    logger.info("Start generators: {}", [name for name, _ in GENERATORS])
    for name, runner in GENERATORS:
        logger.info("Running generator: {}", name)
        runner(ctx)
    logger.info("All generators finished.")


if __name__ == "__main__":
    main()
