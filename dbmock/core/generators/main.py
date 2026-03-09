"""按批次生成数据的主调度入口。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from loguru import logger

from .batches import (
    batch1_static_dims,
    batch2_product_dims,
    batch3_marketing,
    batch4_trade_core,
    batch5_behavior,
)
from .settings import DBConfig, GenerateConfig, RunContext

SEQUENTIAL_GENERATORS = [
    ("static_dims", batch1_static_dims.run),
    ("product_dims", batch2_product_dims.run),
    ("marketing", batch3_marketing.run),
]
PARALLEL_GENERATORS = [
    ("trade_core", batch4_trade_core.run),
    ("behavior", batch5_behavior.run),
]


def _build_context(db_cfg: DBConfig, gen_cfg: GenerateConfig) -> RunContext:
    return RunContext(db=replace(db_cfg), gen=replace(gen_cfg))


def _run_generator(name: str, runner, ctx: RunContext) -> None:
    logger.info("Running generator: {}", name)
    runner(ctx)


def main() -> None:
    db_cfg = DBConfig()
    gen_cfg = GenerateConfig()

    logger.info(
        "Starting sequential generators: {}",
        [name for name, _ in SEQUENTIAL_GENERATORS],
    )
    for name, runner in SEQUENTIAL_GENERATORS:
        _run_generator(name, runner, _build_context(db_cfg, gen_cfg))

    logger.info(
        "Starting parallel generators: {}",
        [name for name, _ in PARALLEL_GENERATORS],
    )
    with ThreadPoolExecutor(max_workers=len(PARALLEL_GENERATORS)) as executor:
        futures = [
            executor.submit(
                _run_generator, name, runner, _build_context(db_cfg, gen_cfg)
            )
            for name, runner in PARALLEL_GENERATORS
        ]
        for future in futures:
            future.result()

    logger.info("All generators finished.")


if __name__ == "__main__":
    main()
