"""批次5：生成互动、流量事实数据。"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import MetaData, select

from ..catalogs import (
    APP_VERSIONS,
    CART_EVENTS_PER_USER,
    CART_SOURCES,
    CHANNEL_CODES,
    CLIENT_TYPES,
    FAVOR_EVENTS_PER_USER,
    OS_TYPES,
    PAGE_DEFINITIONS,
    PAGE_VIEW_EVENTS_PER_USER,
    SEARCH_EVENTS_PER_USER,
    SEARCH_KEYWORDS,
    SEARCH_SOURCES,
)
from ..settings import RunContext
from ..utils.loaders import bulk_insert

MONEY_ZERO = Decimal("0.00")


def _has_rows(conn, table) -> bool:
    """判断目标表是否已有数据。"""
    return conn.execute(select(table.c.id).limit(1)).first() is not None


def _load_all_rows(conn, table) -> list[dict[str, Any]]:
    """加载整张表的数据。"""
    return [dict(row) for row in conn.execute(select(table)).mappings()]


def _latest_snapshot_rows(
    rows: list[dict[str, Any]], key_field: str
) -> dict[Any, dict[str, Any]]:
    """从快照表中提取每个业务键的最新版本。"""
    latest: dict[Any, dict[str, Any]] = {}
    for row in rows:
        current = latest.get(row[key_field])
        if current is None or row["etl_date"] > current["etl_date"]:
            latest[row[key_field]] = row
    return latest


def _latest_scd_rows(rows: list[dict[str, Any]], key_field: str) -> dict[Any, dict[str, Any]]:
    """从拉链表中提取每个业务键的当前版本。"""
    latest: dict[Any, dict[str, Any]] = {}
    for row in rows:
        current = latest.get(row[key_field])
        if current is None or row["start_date"] > current["start_date"]:
            latest[row[key_field]] = row
    return latest


def _clamp_text(text: str, limit: int) -> str:
    """截断文本，避免超过字段长度。"""
    return text[:limit]


def _device_id(user_id: int, seq: int) -> str:
    """生成稳定设备标识。"""
    return f"DV{user_id}{seq:06d}"


def _session_id(user_id: int, seq: int) -> str:
    """生成稳定会话标识。"""
    return f"SS{user_id}{seq:06d}"


def _masked_ip(user_id: int) -> str:
    """生成脱敏访问IP。"""
    return f"10.{user_id % 255}.{(user_id // 3) % 255}.***"


def _pick_client(ctx: RunContext, user_id: int, seq: int) -> tuple[str, str]:
    """选择客户端和渠道。"""
    client_type = CLIENT_TYPES[(user_id + seq) % len(CLIENT_TYPES)]
    channel_code = CHANNEL_CODES[(user_id * 3 + seq) % len(CHANNEL_CODES)]
    return client_type, channel_code


def _random_event_time(ctx: RunContext, seq: int) -> datetime:
    """在配置时间范围内生成随机事件时间。"""
    start_date = date.fromisoformat(ctx.gen.start_date)
    end_date = date.fromisoformat(ctx.gen.end_date)
    total_days = (end_date - start_date).days
    event_date = start_date + timedelta(days=ctx.rng.randrange(total_days + 1))
    event_hour = (seq * 7 + ctx.rng.randrange(24)) % 24
    return datetime.combine(
        event_date,
        time(event_hour, ctx.rng.randrange(60), ctx.rng.randrange(60)),
    )


def _flush_buffer(
    conn,
    table,
    buffer: list[dict[str, Any]],
    batch_size: int,
    inserted_total: int,
) -> tuple[int, int]:
    """按批次写入并返回本次与累计写入量。"""
    inserted = bulk_insert(conn, table, buffer, batch_size)
    if inserted <= 0:
        return 0, inserted_total
    buffer.clear()
    inserted_total += inserted
    return inserted, inserted_total


def run(ctx: RunContext) -> None:
    """生成批次5的互动、流量事实。"""
    logger.info("Run batch5_behavior")
    metadata = MetaData()
    metadata.reflect(
        bind=ctx.engine,
        only=[
            "dwd_dim_user_info_df",
            "dwd_dim_sku_info_df",
            "dwd_dim_category_info_df",
            "dwd_fact_interaction_cart_add_di",
            "dwd_fact_interaction_favor_add_di",
            "dwd_fact_traffic_page_view_di",
            "dwd_fact_traffic_search_di",
        ],
    )
    user_table = metadata.tables["dwd_dim_user_info_df"]
    sku_table = metadata.tables["dwd_dim_sku_info_df"]
    category_table = metadata.tables["dwd_dim_category_info_df"]
    cart_table = metadata.tables["dwd_fact_interaction_cart_add_di"]
    favor_table = metadata.tables["dwd_fact_interaction_favor_add_di"]
    page_view_table = metadata.tables["dwd_fact_traffic_page_view_di"]
    search_table = metadata.tables["dwd_fact_traffic_search_di"]

    with ctx.engine.begin() as conn:
        if _has_rows(conn, cart_table):
            logger.info("Behavior tables already contain data, skip batch5 generation")
            return

        logger.info("batch5 loading source rows")
        user_rows = _load_all_rows(conn, user_table)
        sku_rows = _load_all_rows(conn, sku_table)
        category_rows = _load_all_rows(conn, category_table)
        if not user_rows or not sku_rows:
            raise ValueError("批次5缺少用户或 SKU 维度数据")
        logger.info(
            "batch5 loaded source rows: user_rows={}, sku_rows={}, category_rows={}",
            len(user_rows),
            len(sku_rows),
            len(category_rows),
        )

        category_map = _latest_snapshot_rows(category_rows, "category_id")
        active_users = list(_latest_scd_rows(user_rows, "user_id").values())
        active_skus = list(_latest_scd_rows(sku_rows, "sku_id").values())
        if not active_users or not active_skus:
            raise ValueError("批次5缺少当前有效的用户或 SKU 数据")

        cart_target = int(len(active_users) * CART_EVENTS_PER_USER)
        favor_target = int(len(active_users) * FAVOR_EVENTS_PER_USER)
        page_target = int(len(active_users) * PAGE_VIEW_EVENTS_PER_USER)
        search_target = int(len(active_users) * SEARCH_EVENTS_PER_USER)
        logger.info(
            "batch5 targets: users={} skus={} cart_target={} favor_target={} page_target={} search_target={}",
            len(active_users),
            len(active_skus),
            cart_target,
            favor_target,
            page_target,
            search_target,
        )

        cart_buffer: list[dict[str, Any]] = []
        favor_buffer: list[dict[str, Any]] = []
        page_buffer: list[dict[str, Any]] = []
        search_buffer: list[dict[str, Any]] = []

        cart_inserted = 0
        favor_inserted = 0
        page_inserted = 0
        search_inserted = 0

        cart_seq = 14_000_000
        favor_seq = 15_000_000
        page_seq = 16_000_000
        search_seq = 17_000_000

        def flush_behavior_buffers(reason: str) -> None:
            nonlocal cart_inserted
            nonlocal favor_inserted
            nonlocal page_inserted
            nonlocal search_inserted

            flush_counts: dict[str, int] = {}

            flushed, cart_inserted = _flush_buffer(
                conn, cart_table, cart_buffer, ctx.gen.batch_size, cart_inserted
            )
            if flushed:
                flush_counts["cart"] = flushed
            flushed, favor_inserted = _flush_buffer(
                conn, favor_table, favor_buffer, ctx.gen.batch_size, favor_inserted
            )
            if flushed:
                flush_counts["favor"] = flushed
            flushed, page_inserted = _flush_buffer(
                conn, page_view_table, page_buffer, ctx.gen.batch_size, page_inserted
            )
            if flushed:
                flush_counts["page"] = flushed
            flushed, search_inserted = _flush_buffer(
                conn, search_table, search_buffer, ctx.gen.batch_size, search_inserted
            )
            if flushed:
                flush_counts["search"] = flushed

            if flush_counts:
                logger.info(
                    "batch5 flush reason={} cart={} favor={} page={} search={} totals=({},{},{},{})",
                    reason,
                    flush_counts.get("cart", 0),
                    flush_counts.get("favor", 0),
                    flush_counts.get("page", 0),
                    flush_counts.get("search", 0),
                    cart_inserted,
                    favor_inserted,
                    page_inserted,
                    search_inserted,
                )

        logger.info("batch5 generating cart rows")
        for idx in range(cart_target):
            user_row = active_users[ctx.rng.randrange(len(active_users))]
            sku_row = active_skus[ctx.rng.randrange(len(active_skus))]
            user_id = user_row["user_id"]
            client_type, channel_code = _pick_client(ctx, user_id, idx)
            event_time = _random_event_time(ctx, idx)
            cart_seq += 1
            cart_buffer.append(
                {
                    "cart_add_id": cart_seq,
                    "event_no": f"CA{cart_seq}",
                    "user_id": user_id,
                    "device_id": _device_id(user_id, idx),
                    "session_id": _session_id(user_id, idx),
                    "shop_id": sku_row.get("shop_id"),
                    "sku_id": sku_row["sku_id"],
                    "spu_id": sku_row.get("spu_id"),
                    "category_id": sku_row.get("category_id"),
                    "cart_source": CART_SOURCES[idx % len(CART_SOURCES)],
                    "client_type": client_type,
                    "channel_code": channel_code,
                    "add_sku_num": 1 + ((sku_row["sku_id"] + idx) % 3),
                    "sku_price": Decimal(str(sku_row["sale_price"])),
                    "event_time": event_time,
                    "etl_date": event_time.date(),
                }
            )
            if len(cart_buffer) >= ctx.gen.batch_size:
                flush_behavior_buffers("cart")

        logger.info("batch5 generating favor rows")
        for idx in range(favor_target):
            user_row = active_users[ctx.rng.randrange(len(active_users))]
            sku_row = active_skus[ctx.rng.randrange(len(active_skus))]
            user_id = user_row["user_id"]
            client_type, channel_code = _pick_client(ctx, user_id, idx + 1000)
            event_time = _random_event_time(ctx, idx + 1000)
            favor_type = "商品" if idx % 4 != 0 else "店铺"
            favor_seq += 1
            favor_buffer.append(
                {
                    "favor_add_id": favor_seq,
                    "event_no": f"FA{favor_seq}",
                    "user_id": user_id,
                    "shop_id": sku_row.get("shop_id"),
                    "sku_id": sku_row["sku_id"] if favor_type == "商品" else None,
                    "spu_id": sku_row.get("spu_id")
                    if favor_type == "商品"
                    else None,
                    "favor_type": favor_type,
                    "client_type": client_type,
                    "channel_code": channel_code,
                    "event_time": event_time,
                    "etl_date": event_time.date(),
                }
            )
            if len(favor_buffer) >= ctx.gen.batch_size:
                flush_behavior_buffers("favor")

        logger.info("batch5 generating page rows")
        for idx in range(page_target):
            user_row = active_users[ctx.rng.randrange(len(active_users))]
            sku_row = active_skus[ctx.rng.randrange(len(active_skus))]
            user_id = user_row["user_id"]
            client_type, channel_code = _pick_client(ctx, user_id, idx + 2000)
            page_id, page_name, page_type = PAGE_DEFINITIONS[
                idx % len(PAGE_DEFINITIONS)
            ]
            event_time = _random_event_time(ctx, idx + 2000)
            business_id = None
            business_type = None
            if page_type == "详情":
                business_id = str(sku_row["sku_id"])
                business_type = "sku"
            elif page_type == "活动":
                business_id = f"campaign-{sku_row['shop_id']}"
                business_type = "campaign"
            elif page_type == "下单":
                business_id = f"preview-{sku_row['sku_id']}-{idx}"
                business_type = "trade_preview"
            elif page_type == "搜索":
                business_id = str(sku_row.get("category_id") or "")
                business_type = "category"
            page_seq += 1
            page_buffer.append(
                {
                    "page_view_id": page_seq,
                    "event_no": f"PV{page_seq}",
                    "user_id": user_id,
                    "device_id": _device_id(user_id, idx + 2000),
                    "session_id": _session_id(user_id, idx // 3),
                    "page_id": page_id,
                    "page_name": page_name,
                    "last_page_id": PAGE_DEFINITIONS[(idx - 1) % len(PAGE_DEFINITIONS)][
                        0
                    ],
                    "page_type": page_type,
                    "business_id": business_id,
                    "business_type": business_type,
                    "channel_code": channel_code,
                    "client_type": client_type,
                    "app_version": APP_VERSIONS[idx % len(APP_VERSIONS)],
                    "os_type": OS_TYPES[idx % len(OS_TYPES)],
                    "ip": _masked_ip(user_id),
                    "province_code": user_row.get("province_code"),
                    "city_code": user_row.get("city_code"),
                    "stay_duration_sec": 5 + (idx % 600),
                    "is_bounce": 1 if idx % 9 == 0 else 0,
                    "event_time": event_time,
                    "etl_date": event_time.date(),
                }
            )
            if len(page_buffer) >= ctx.gen.batch_size:
                flush_behavior_buffers("page")

        category_name_pool = [
            row["category_name"]
            for row in category_map.values()
            if row.get("category_name")
        ]
        keyword_pool = SEARCH_KEYWORDS + category_name_pool[:50]
        logger.info("batch5 generating search rows")
        for idx in range(search_target):
            user_row = active_users[ctx.rng.randrange(len(active_users))]
            user_id = user_row["user_id"]
            client_type, channel_code = _pick_client(ctx, user_id, idx + 3000)
            event_time = _random_event_time(ctx, idx + 3000)
            is_no_result = 1 if idx % 11 == 0 else 0
            click_row = (
                None
                if is_no_result
                else active_skus[ctx.rng.randrange(len(active_skus))]
            )
            search_seq += 1
            search_buffer.append(
                {
                    "search_detail_id": search_seq,
                    "event_no": f"SE{search_seq}",
                    "user_id": user_id,
                    "device_id": _device_id(user_id, idx + 3000),
                    "session_id": _session_id(user_id, idx // 2),
                    "search_keyword": _clamp_text(
                        keyword_pool[idx % len(keyword_pool)], 256
                    ),
                    "search_source": SEARCH_SOURCES[idx % len(SEARCH_SOURCES)],
                    "result_total_cnt": 0 if is_no_result else 20 + (idx % 180),
                    "click_rank": None if click_row is None else 1 + (idx % 20),
                    "click_sku_id": None if click_row is None else click_row["sku_id"],
                    "click_spu_id": None if click_row is None else click_row["spu_id"],
                    "is_no_result": is_no_result,
                    "is_search_success": 1,
                    "channel_code": channel_code,
                    "client_type": client_type,
                    "event_time": event_time,
                    "etl_date": event_time.date(),
                }
            )
            if len(search_buffer) >= ctx.gen.batch_size:
                flush_behavior_buffers("search")

        flush_behavior_buffers("final")

    logger.info(
        "Generated batch5 behavior facts: cart_rows={}, favor_rows={}, page_rows={}, search_rows={}",
        cart_inserted,
        favor_inserted,
        page_inserted,
        search_inserted,
    )
