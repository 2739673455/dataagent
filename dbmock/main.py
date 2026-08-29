"""电商数仓按月状态演化生成入口"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import timedelta

from src import quality, support
from src.batches import behavior, commerce, dimensions, marketing, products, snapshots
from src.reference import load_reference_data
from src.settings import DorisConfig, GenerateConfig, RunContext
from src.timeline import (
    BusinessState,
    build_period_targets,
    day_targets,
    month_periods,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成电商数仓模拟数据")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="使用七天小数据集验证完整链路",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只执行数据质量校验",
    )
    parser.add_argument(
        "--dimensions-only",
        action="store_true",
        help="只加载公共维度、SPU 和 SKU 并执行维度质量校验",
    )
    return parser.parse_args()


def _run(ctx: RunContext, args: argparse.Namespace) -> None:
    tables = support.reflect_tables(ctx.engine)

    if args.validate_only:
        quality.validate_database(ctx, tables)
        return

    if args.dimensions_only:
        with ctx.engine.connect() as conn:
            support.assert_empty(conn, tables)
        dimensions.run(ctx, tables)
        products.run_dimensions(ctx, tables)
        quality.validate_catalog_dimensions(ctx)
        logger.info("维度数据生成并校验完成 run_id=%s", ctx.run_id)
        return

    with ctx.engine.connect() as conn:
        support.assert_empty(conn, tables)
    periods = month_periods(ctx.gen.start_date, ctx.gen.end_date)
    targets = build_period_targets(ctx.gen, periods, ctx.data_end_time)
    dimensions.run(ctx, tables)
    products.run_dimensions(ctx, tables)
    marketing.run(ctx, tables)
    state = BusinessState()
    logger.info("基础维度初始化完成 run_id=%s", ctx.run_id)
    refs = load_reference_data(ctx, tables)
    for period in periods:
        period_targets = targets[period.key]
        views_by_day = day_targets(
            period_targets.page_views,
            period,
            ctx.gen.start_date,
            "traffic",
            ctx.data_end_time,
        )
        searches_by_day = day_targets(
            period_targets.searches,
            period,
            ctx.gen.start_date,
            "search",
            ctx.data_end_time,
        )
        details_by_day = day_targets(
            period_targets.order_details,
            period,
            ctx.gen.start_date,
            "order",
            ctx.data_end_time,
        )
        writer = support.TableWriter(
            ctx.loader,
            ctx.gen.batch_size,
            ctx.gen.stream_load_workers,
            ctx.gen.start_date,
            ctx.as_of_time,
        )
        batch_id = ctx.period_batch_id(period.key)
        ctx.loader.take_metrics()
        period_started = time.perf_counter()
        try:
            current_day = period.start_date
            while current_day <= period.end_date:
                products.generate_price_events(
                    ctx,
                    refs,
                    writer,
                    current_day,
                    batch_id,
                )
                intents = behavior.generate_day(
                    ctx,
                    refs,
                    state,
                    writer,
                    current_day,
                    batch_id,
                    views_by_day[current_day],
                    searches_by_day[current_day],
                    details_by_day[current_day],
                )
                commerce.generate_day(
                    ctx,
                    refs,
                    state,
                    writer,
                    current_day,
                    batch_id,
                    intents,
                )
                current_day = current_day.fromordinal(current_day.toordinal() + 1)
            lifecycle_updates = dimensions.generate_user_lifecycle_updates(
                ctx,
                refs,
                state,
                writer,
                min(
                    support.start_of_day(period.end_date + timedelta(days=1)),
                    ctx.data_end_time,
                ),
                batch_id,
            )
            if lifecycle_updates:
                logger.info(
                    "用户生命周期版本更新 period=%s users=%s",
                    period.key,
                    lifecycle_updates,
                )
            generation_seconds = time.perf_counter() - period_started
            load_started = time.perf_counter()
            row_counts = writer.flush_all()
            load_seconds = time.perf_counter() - load_started
            snapshot_started = time.perf_counter()
            row_counts["dwd_inventory_daily_snapshot_df"] = (
                snapshots.materialize_period(ctx, period, batch_id)
            )
            snapshot_seconds = time.perf_counter() - snapshot_started
            load_metrics = ctx.loader.take_metrics()
            refs = load_reference_data(ctx, tables)
            logger.info(
                "业务月份生成完成 period=%s rows=%s total=%.2fs "
                "generate=%.2fs flush=%.2fs snapshot=%.2fs "
                "loads=%s encode_sum=%.2fs request_sum=%.2fs "
                "parquet=%.2fMiB",
                period.key,
                sum(row_counts.values()),
                time.perf_counter() - period_started,
                generation_seconds,
                load_seconds,
                snapshot_seconds,
                load_metrics.request_count,
                load_metrics.encode_seconds,
                load_metrics.request_seconds,
                load_metrics.parquet_bytes / 1024 / 1024,
            )
        except Exception:
            logger.exception(
                "业务月份生成失败，请执行 make init_db 后重新生成 period=%s",
                period.key,
            )
            raise

    quality.validate_database(ctx, tables)
    logger.info("全部数据生成并校验完成 run_id=%s", ctx.run_id)


def main() -> None:
    args = _parse_args()
    gen = GenerateConfig.smoke() if args.smoke else GenerateConfig()
    ctx = RunContext(DorisConfig(), gen)
    try:
        _run(ctx, args)
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
