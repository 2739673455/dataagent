"""批次5：生成互动、流量事实数据。"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import MetaData, select

from ..catalogs import (
    APP_VERSIONS,
    APP_CLIENT_TYPES,
    CART_EVENTS_PER_USER,
    CART_SOURCES,
    CHANNEL_CODES,
    CHANNEL_CLIENT_OPTIONS,
    CLIENT_OS_OPTIONS,
    FAVOR_EVENTS_PER_USER,
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


def _load_current_rows(conn, table) -> list[dict[str, Any]]:
    """加载拉链表中的当前有效版本。"""
    return [
        dict(row)
        for row in conn.execute(select(table).where(table.c.is_current == 1)).mappings()
    ]


def _load_all_rows(conn, table) -> list[dict[str, Any]]:
    """加载整张表数据。"""
    return [dict(row) for row in conn.execute(select(table)).mappings()]


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
    channel_code = CHANNEL_CODES[(user_id * 3 + seq) % len(CHANNEL_CODES)]
    client_candidates = CHANNEL_CLIENT_OPTIONS[channel_code]
    client_type = client_candidates[(user_id + seq) % len(client_candidates)]
    return client_type, channel_code


def _pick_os_type(client_type: str, seq: int) -> str:
    """根据客户端类型选择兼容的操作系统。"""
    os_candidates = CLIENT_OS_OPTIONS[client_type]
    return os_candidates[seq % len(os_candidates)]


def _pick_app_version(client_type: str, seq: int) -> str | None:
    """仅为 APP 客户端填充版本号。"""
    if client_type not in APP_CLIENT_TYPES:
        return None
    return APP_VERSIONS[seq % len(APP_VERSIONS)]


def _random_event_time_for_date(
    rng,
    event_date: date,
    seq: int,
) -> datetime:
    """为指定日期生成随机事件时间。"""
    event_hour = (seq * 7 + rng.randrange(24)) % 24
    return datetime.combine(
        event_date,
        time(event_hour, rng.randrange(60), rng.randrange(60)),
    )


def _allocate_daily_counts(
    total_events: int,
    start_date: date,
    end_date: date,
    rng,
) -> list[tuple[date, int]]:
    """将事件量随机分配到日期区间内的每天。"""
    total_days = (end_date - start_date).days
    counts: dict[date, int] = defaultdict(int)
    for _ in range(total_events):
        event_date = start_date + timedelta(days=rng.randrange(total_days + 1))
        counts[event_date] += 1
    return [
        (start_date + timedelta(days=offset), counts.get(start_date + timedelta(days=offset), 0))
        for offset in range(total_days + 1)
    ]


class ActiveVersionPool:
    """按日期维护可用版本集合，并支持随机抽样。"""

    def __init__(self, rows: list[dict[str, Any]], key_field: str) -> None:
        self._key_field = key_field
        self._start_rows = sorted(
            rows, key=lambda row: (row["start_date"], row[key_field])
        )
        self._end_rows = sorted(rows, key=lambda row: (row["end_date"], row[key_field]))
        self._active_rows: list[dict[str, Any]] = []
        self._active_keys: set[tuple[Any, date]] = set()
        self._start_idx = 0
        self._end_idx = 0

    def advance(self, current_date: date) -> None:
        """推进到指定日期，更新可用版本集合。"""
        while self._start_idx < len(self._start_rows):
            row = self._start_rows[self._start_idx]
            if row["start_date"] > current_date:
                break
            row_key = (row[self._key_field], row["start_date"])
            self._active_rows.append(row)
            self._active_keys.add(row_key)
            self._start_idx += 1

        while self._end_idx < len(self._end_rows):
            row = self._end_rows[self._end_idx]
            if row["end_date"] >= current_date:
                break
            row_key = (row[self._key_field], row["start_date"])
            self._active_keys.discard(row_key)
            self._end_idx += 1

    def random_row(self, rng):
        """从当前有效版本中随机选择一条记录。"""
        if not self._active_keys:
            return None
        for _ in range(24):
            row = self._active_rows[rng.randrange(len(self._active_rows))]
            row_key = (row[self._key_field], row["start_date"])
            if row_key in self._active_keys:
                return row
        for row in self._active_rows:
            row_key = (row[self._key_field], row["start_date"])
            if row_key in self._active_keys:
                return row
        return None


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
        table_has_rows = {
            "cart": _has_rows(conn, cart_table),
            "favor": _has_rows(conn, favor_table),
            "page_view": _has_rows(conn, page_view_table),
            "search": _has_rows(conn, search_table),
        }
        if all(table_has_rows.values()):
            logger.info("Behavior tables already contain data, skip batch5 generation")
            return
        if any(table_has_rows.values()):
            raise ValueError(
                "批次5行为事实表存在部分已生成状态，请先清理后重跑: "
                f"{table_has_rows}"
            )

        logger.info("batch5 loading source rows")
        user_rows = _load_all_rows(conn, user_table)
        sku_rows = _load_all_rows(conn, sku_table)
        category_rows = _load_current_rows(conn, category_table)
        if not user_rows or not sku_rows:
            raise ValueError("批次5缺少用户或 SKU 维度数据")
        logger.info(
            "batch5 loaded source rows: user_rows={}, sku_rows={}, category_rows={}",
            len(user_rows),
            len(sku_rows),
            len(category_rows),
        )

        start_date = date.fromisoformat(ctx.gen.start_date)
        end_date = date.fromisoformat(ctx.gen.end_date)
        current_user_count = sum(1 for row in user_rows if row.get("is_current") == 1)
        current_sku_count = sum(1 for row in sku_rows if row.get("is_current") == 1)
        if current_user_count <= 0 or current_sku_count <= 0:
            raise ValueError("批次5缺少当前有效的用户或 SKU 数据")

        cart_target = int(current_user_count * CART_EVENTS_PER_USER)
        favor_target = int(current_user_count * FAVOR_EVENTS_PER_USER)
        page_target = int(current_user_count * PAGE_VIEW_EVENTS_PER_USER)
        search_target = int(current_user_count * SEARCH_EVENTS_PER_USER)
        logger.info(
            "batch5 targets: users={} skus={} cart_target={} favor_target={} page_target={} search_target={}",
            current_user_count,
            current_sku_count,
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
        rng = ctx.rng
        cart_source_count = len(CART_SOURCES)
        favor_type_mod = 4
        page_definition_count = len(PAGE_DEFINITIONS)
        search_source_count = len(SEARCH_SOURCES)
        category_name_pool = [
            row["category_name"] for row in category_rows if row.get("category_name")
        ]
        keyword_pool = SEARCH_KEYWORDS + category_name_pool[:50]
        keyword_count = len(keyword_pool)
        cart_daily_counts = _allocate_daily_counts(cart_target, start_date, end_date, rng)
        favor_daily_counts = _allocate_daily_counts(
            favor_target, start_date, end_date, rng
        )
        page_daily_counts = _allocate_daily_counts(page_target, start_date, end_date, rng)
        search_daily_counts = _allocate_daily_counts(
            search_target, start_date, end_date, rng
        )
        user_pool = ActiveVersionPool(user_rows, "user_id")
        sku_pool = ActiveVersionPool(sku_rows, "sku_id")

        def flush_single_buffer(
            reason: str,
            metric_key: str,
            table,
            buffer: list[dict[str, Any]],
            inserted_total: int,
        ) -> int:
            flushed, inserted_total = _flush_buffer(
                conn,
                table,
                buffer,
                ctx.gen.batch_size,
                inserted_total,
            )
            if flushed:
                flush_counts = {
                    "cart": 0,
                    "favor": 0,
                    "page": 0,
                    "search": 0,
                }
                flush_counts[metric_key] = flushed
                logger.info(
                    "batch5 flush reason={} cart={} favor={} page={} search={} totals=({},{},{},{})",
                    reason,
                    flush_counts["cart"],
                    flush_counts["favor"],
                    flush_counts["page"],
                    flush_counts["search"],
                    cart_inserted if metric_key != "cart" else inserted_total,
                    favor_inserted if metric_key != "favor" else inserted_total,
                    page_inserted if metric_key != "page" else inserted_total,
                    search_inserted if metric_key != "search" else inserted_total,
                )
            return inserted_total

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
                    "batch5 final flush cart={} favor={} page={} search={} totals=({},{},{},{})",
                    flush_counts.get("cart", 0),
                    flush_counts.get("favor", 0),
                    flush_counts.get("page", 0),
                    flush_counts.get("search", 0),
                    cart_inserted,
                    favor_inserted,
                    page_inserted,
                    search_inserted,
                )

        logger.info("batch5 generating behavior rows by date")
        cart_idx = 0
        favor_idx = 0
        page_idx = 0
        search_idx = 0
        for day_offset, (current_date, cart_count) in enumerate(cart_daily_counts):
            user_pool.advance(current_date)
            sku_pool.advance(current_date)
            favor_count = favor_daily_counts[day_offset][1]
            page_count = page_daily_counts[day_offset][1]
            search_count = search_daily_counts[day_offset][1]

            if (
                cart_count + favor_count + page_count + search_count <= 0
                or user_pool.random_row(rng) is None
                or sku_pool.random_row(rng) is None
            ):
                continue

            for _ in range(cart_count):
                user_row = user_pool.random_row(rng)
                sku_row = sku_pool.random_row(rng)
                if user_row is None or sku_row is None:
                    break
                user_id = user_row["user_id"]
                client_type, channel_code = _pick_client(ctx, user_id, cart_idx)
                event_time = _random_event_time_for_date(rng, current_date, cart_idx)
                cart_seq += 1
                cart_buffer.append(
                    {
                        "cart_add_id": cart_seq,
                        "event_no": f"CA{cart_seq}",
                        "user_id": user_id,
                        "device_id": _device_id(user_id, cart_idx),
                        "session_id": _session_id(user_id, cart_idx),
                        "shop_id": sku_row.get("shop_id"),
                        "sku_id": sku_row["sku_id"],
                        "spu_id": sku_row.get("spu_id"),
                        "category_id": sku_row.get("category_id"),
                        "cart_source": CART_SOURCES[cart_idx % cart_source_count],
                        "client_type": client_type,
                        "channel_code": channel_code,
                        "add_sku_num": 1 + ((sku_row["sku_id"] + cart_idx) % 3),
                        "sku_price": Decimal(str(sku_row["sale_price"])),
                        "event_time": event_time,
                        "etl_date": event_time.date(),
                    }
                )
                cart_idx += 1
                if len(cart_buffer) >= ctx.gen.batch_size:
                    cart_inserted = flush_single_buffer(
                        "cart", "cart", cart_table, cart_buffer, cart_inserted
                    )

            for _ in range(favor_count):
                user_row = user_pool.random_row(rng)
                sku_row = sku_pool.random_row(rng)
                if user_row is None or sku_row is None:
                    break
                user_id = user_row["user_id"]
                client_type, channel_code = _pick_client(ctx, user_id, favor_idx + 1000)
                event_time = _random_event_time_for_date(
                    rng, current_date, favor_idx + 1000
                )
                favor_type = "商品" if favor_idx % favor_type_mod != 0 else "店铺"
                favor_seq += 1
                favor_buffer.append(
                    {
                        "favor_add_id": favor_seq,
                        "event_no": f"FA{favor_seq}",
                        "user_id": user_id,
                        "shop_id": sku_row.get("shop_id"),
                        "sku_id": sku_row["sku_id"] if favor_type == "商品" else None,
                        "spu_id": sku_row.get("spu_id") if favor_type == "商品" else None,
                        "favor_type": favor_type,
                        "client_type": client_type,
                        "channel_code": channel_code,
                        "event_time": event_time,
                        "etl_date": event_time.date(),
                    }
                )
                favor_idx += 1
                if len(favor_buffer) >= ctx.gen.batch_size:
                    favor_inserted = flush_single_buffer(
                        "favor", "favor", favor_table, favor_buffer, favor_inserted
                    )

            for _ in range(page_count):
                user_row = user_pool.random_row(rng)
                sku_row = sku_pool.random_row(rng)
                if user_row is None or sku_row is None:
                    break
                user_id = user_row["user_id"]
                client_type, channel_code = _pick_client(ctx, user_id, page_idx + 2000)
                page_id, page_name, page_type = PAGE_DEFINITIONS[
                    page_idx % page_definition_count
                ]
                event_time = _random_event_time_for_date(
                    rng, current_date, page_idx + 2000
                )
                business_id = None
                business_type = None
                if page_type == "详情":
                    business_id = str(sku_row["sku_id"])
                    business_type = "sku"
                elif page_type == "活动":
                    business_id = f"campaign-{sku_row['shop_id']}"
                    business_type = "campaign"
                elif page_type == "下单":
                    business_id = f"preview-{sku_row['sku_id']}-{page_idx}"
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
                        "device_id": _device_id(user_id, page_idx + 2000),
                        "session_id": _session_id(user_id, page_idx // 3),
                        "page_id": page_id,
                        "page_name": page_name,
                        "last_page_id": PAGE_DEFINITIONS[
                            (page_idx - 1) % len(PAGE_DEFINITIONS)
                        ][0],
                        "page_type": page_type,
                        "business_id": business_id,
                        "business_type": business_type,
                        "channel_code": channel_code,
                        "client_type": client_type,
                        "app_version": _pick_app_version(client_type, page_idx),
                        "os_type": _pick_os_type(client_type, page_idx),
                        "ip": _masked_ip(user_id),
                        "province_code": user_row.get("province_code"),
                        "city_code": user_row.get("city_code"),
                        "stay_duration_sec": 5 + (page_idx % 600),
                        "is_bounce": 1 if page_idx % 9 == 0 else 0,
                        "event_time": event_time,
                        "etl_date": event_time.date(),
                    }
                )
                page_idx += 1
                if len(page_buffer) >= ctx.gen.batch_size:
                    page_inserted = flush_single_buffer(
                        "page", "page", page_view_table, page_buffer, page_inserted
                    )

            for _ in range(search_count):
                user_row = user_pool.random_row(rng)
                if user_row is None:
                    break
                user_id = user_row["user_id"]
                client_type, channel_code = _pick_client(
                    ctx, user_id, search_idx + 3000
                )
                event_time = _random_event_time_for_date(
                    rng, current_date, search_idx + 3000
                )
                is_no_result = 1 if search_idx % 11 == 0 else 0
                click_row = None if is_no_result else sku_pool.random_row(rng)
                search_seq += 1
                search_buffer.append(
                    {
                        "search_detail_id": search_seq,
                        "event_no": f"SE{search_seq}",
                        "user_id": user_id,
                        "device_id": _device_id(user_id, search_idx + 3000),
                        "session_id": _session_id(user_id, search_idx // 2),
                        "search_keyword": _clamp_text(
                            keyword_pool[search_idx % keyword_count], 256
                        ),
                        "search_source": SEARCH_SOURCES[
                            search_idx % search_source_count
                        ],
                        "result_total_cnt": 0 if is_no_result else 20 + (search_idx % 180),
                        "click_rank": None if click_row is None else 1 + (search_idx % 20),
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
                search_idx += 1
                if len(search_buffer) >= ctx.gen.batch_size:
                    search_inserted = flush_single_buffer(
                        "search", "search", search_table, search_buffer, search_inserted
                    )

        flush_behavior_buffers("final")

    logger.info(
        "Generated batch5 behavior facts: cart_rows={}, favor_rows={}, page_rows={}, search_rows={}",
        cart_inserted,
        favor_inserted,
        page_inserted,
        search_inserted,
    )
