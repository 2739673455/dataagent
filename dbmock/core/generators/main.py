"""按批次生成数据的主调度入口。"""

from __future__ import annotations

from loguru import logger

from .batches import (
    batch1_static_dims,
    batch2_product_dims,
    batch3_marketing,
    batch4_trade_core,
    batch5_behavior,
)
from .settings import DEFAULT_BATCH_SIZE, DBConfig, GenerateConfig, RunContext

GENERATORS = [
    ("static_dims", batch1_static_dims.run),
    ("product_dims", batch2_product_dims.run),
    ("marketing", batch3_marketing.run),
    ("trade_core", batch4_trade_core.run),
    ("behavior", batch5_behavior.run),
]


def main() -> None:
    db_cfg = DBConfig()
    gen_cfg = GenerateConfig(batch_size=DEFAULT_BATCH_SIZE)
    ctx = RunContext(db=db_cfg, gen=gen_cfg)

    logger.info("Starting generators: {}", [name for name, _ in GENERATORS])
    for name, runner in GENERATORS:
        logger.info("Running generator: {}", name)
        runner(ctx)
    logger.info("All generators finished.")


if __name__ == "__main__":
    main()
