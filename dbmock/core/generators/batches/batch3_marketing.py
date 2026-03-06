"""批次3：生成促销活动和优惠券维度数据。"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import MetaData, select

from ..catalogs import (
    CAMPAIGN_SERIES,
    COUPON_NAME_TEMPLATES,
    COUPON_SCOPE_TYPES,
    COUPON_TARGET_COUNT,
    COUPON_THRESHOLD_OPTIONS,
    COUPON_TYPES,
    DISCOUNT_OPTIONS,
    DISCOUNT_RATE_OPTIONS,
    GROUP_SIZE_OPTIONS,
    MAX_DISCOUNT_OPTIONS,
    PROMOTION_RULE_TEMPLATES,
    PROMOTION_SCENES,
    PROMOTION_TARGET_COUNT,
    PROMOTION_TYPES,
    TRANSPORT_COUPON_OPTIONS,
)
from ..settings import RunContext
from ..utils.loaders import bulk_insert


def _has_rows(conn, table) -> bool:
    """判断目标表是否已经存在数据。"""
    stmt = select(table.c.id).limit(1)
    return conn.execute(stmt).first() is not None


def _load_current_rows(conn, table) -> list[dict[str, Any]]:
    """加载当前有效的维度数据。"""
    stmt = select(table).where(table.c.is_current == 1)
    return [dict(row) for row in conn.execute(stmt).mappings()]


def _iter_months(start_date: date, end_date: date):
    """按月遍历日期区间。"""
    current = date(start_date.year, start_date.month, 1)
    end_month = date(end_date.year, end_date.month, 1)
    while current <= end_month:
        yield current
        next_month = current.month + 1
        next_year = current.year
        if next_month == 13:
            next_month = 1
            next_year += 1
        current = date(next_year, next_month, 1)


def _iter_dates(start_date: date, end_date: date):
    """按天遍历日期区间。"""
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _month_campaign_label(month_start: date, idx: int) -> str:
    """为月份选择营销主题名称。"""
    return CAMPAIGN_SERIES[(month_start.month + idx) % len(CAMPAIGN_SERIES)]


def _promotion_time_window(
    month_start: date, slot_idx: int
) -> tuple[datetime, datetime]:
    """生成活动时间窗口。"""
    day_options = [1, 8, 15, 22]
    duration_options = [3, 5, 7, 10]
    day = min(day_options[slot_idx % len(day_options)], 28)
    start_time = datetime(month_start.year, month_start.month, day, 10, 0, 0)
    duration = duration_options[slot_idx % len(duration_options)]
    end_time = start_time + timedelta(days=duration, hours=13, minutes=59)
    return start_time, end_time


def _special_campaign_windows(
    start_date: date, end_date: date
) -> list[tuple[str, datetime, datetime]]:
    """生成大促档期时间窗口。"""
    windows: list[tuple[str, datetime, datetime]] = []
    for year in range(start_date.year, end_date.year + 1):
        for name, month, start_day, end_day in [
            ("618狂欢", 6, 1, 18),
            ("双11狂欢", 11, 1, 11),
            ("双12年终盛典", 12, 1, 12),
            ("年货节", 1, 5, 20),
        ]:
            start_time = datetime(year, month, start_day, 0, 0, 0)
            end_time = datetime(year, month, end_day, 23, 59, 59)
            if end_time.date() < start_date or start_time.date() > end_date:
                continue
            windows.append((name, start_time, end_time))
    return windows


def _build_promotion_rule(
    promotion_type: str,
    threshold_amount: Decimal | None,
    discount_amount: Decimal | None,
    discount_rate: Decimal | None,
    max_discount_amount: Decimal | None,
    idx: int,
) -> str:
    """生成活动规则描述。"""
    template = PROMOTION_RULE_TEMPLATES[promotion_type]
    payload = {
        "threshold": int(threshold_amount or 0),
        "discount": int(discount_amount or 0),
        "discount_rate": f"{(discount_rate or Decimal('1')) * 10:g}",
        "max_discount": int(max_discount_amount or 0),
        "group_size": GROUP_SIZE_OPTIONS[idx % len(GROUP_SIZE_OPTIONS)],
    }
    return template.format(**payload)


def _build_promotion_values(
    promotion_type: str,
    idx: int,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    """按活动类型生成门槛和优惠字段。"""
    if promotion_type == "满减":
        threshold = Decimal(
            COUPON_THRESHOLD_OPTIONS[idx % len(COUPON_THRESHOLD_OPTIONS)]
        )
        discount = Decimal(DISCOUNT_OPTIONS[idx % len(DISCOUNT_OPTIONS)])
        return threshold, discount, None, None
    if promotion_type == "折扣":
        threshold = Decimal(
            COUPON_THRESHOLD_OPTIONS[(idx + 2) % len(COUPON_THRESHOLD_OPTIONS)]
        )
        discount_rate = Decimal(
            DISCOUNT_RATE_OPTIONS[idx % len(DISCOUNT_RATE_OPTIONS)]
        ) / Decimal("100")
        max_discount = Decimal(MAX_DISCOUNT_OPTIONS[idx % len(MAX_DISCOUNT_OPTIONS)])
        return threshold, None, discount_rate, max_discount
    if promotion_type == "秒杀":
        discount = Decimal(DISCOUNT_OPTIONS[(idx + 3) % len(DISCOUNT_OPTIONS)])
        return None, discount, None, None
    discount = Decimal(DISCOUNT_OPTIONS[(idx + 4) % len(DISCOUNT_OPTIONS)])
    return None, discount, None, None


def _pick_sponsor(
    scene: str,
    shops: list[dict[str, Any]],
    brands: list[dict[str, Any]],
    idx: int,
) -> tuple[int, int | None, str]:
    """按活动场景选择发起方。"""
    if scene == "平台":
        return 1, None, "平台"
    if scene == "店铺":
        shop = shops[idx % len(shops)]
        return 2, shop["shop_id"], shop["shop_name"]
    brand = brands[idx % len(brands)]
    return 3, brand["brand_id"], brand["brand_name"]


def _build_promotion_rows(
    start_date: date,
    end_date: date,
    shops: list[dict[str, Any]],
    brands: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成促销活动维度数据。"""
    rows: list[dict[str, Any]] = []
    month_starts = list(_iter_months(start_date, end_date))
    slots_per_month = max(
        (PROMOTION_TARGET_COUNT + max(len(month_starts), 1) - 1)
        // max(len(month_starts), 1),
        1,
    )

    for month_idx, month_start in enumerate(month_starts):
        for slot_idx in range(slots_per_month):
            if len(rows) >= PROMOTION_TARGET_COUNT:
                break
            promotion_type = PROMOTION_TYPES[
                (month_idx + slot_idx) % len(PROMOTION_TYPES)
            ]
            scene = PROMOTION_SCENES[(month_idx + slot_idx) % len(PROMOTION_SCENES)]
            sponsor_type, sponsor_id, sponsor_name = _pick_sponsor(
                scene,
                shops,
                brands,
                month_idx * slots_per_month + slot_idx,
            )
            start_time, end_time = _promotion_time_window(month_start, slot_idx)
            threshold, discount, discount_rate, max_discount = _build_promotion_values(
                promotion_type,
                month_idx * slots_per_month + slot_idx,
            )
            campaign = _month_campaign_label(month_start, slot_idx)
            rows.append(
                {
                    "promotion_id": 4_000_000 + len(rows) + 1,
                    "promotion_name": f"{campaign}{sponsor_name}{promotion_type}活动",
                    "promotion_type": promotion_type,
                    "promotion_scene": scene,
                    "promotion_level": 1 + (slot_idx % 3),
                    "start_time": start_time,
                    "end_time": min(
                        end_time, datetime.combine(end_date, datetime.max.time())
                    ),
                    "rule_desc": _build_promotion_rule(
                        promotion_type,
                        threshold,
                        discount,
                        discount_rate,
                        max_discount,
                        len(rows),
                    ),
                    "threshold_amount": threshold,
                    "discount_amount": discount,
                    "discount_rate": discount_rate,
                    "max_discount_amount": max_discount,
                    "sponsor_type": sponsor_type,
                    "sponsor_id": sponsor_id,
                    "etl_date": start_time.date(),
                }
            )
        if len(rows) >= PROMOTION_TARGET_COUNT:
            break

    for name, start_time, end_time in _special_campaign_windows(start_date, end_date):
        if len(rows) >= PROMOTION_TARGET_COUNT:
            break
        sponsor_type, sponsor_id, sponsor_name = _pick_sponsor(
            "平台",
            shops,
            brands,
            len(rows),
        )
        rows.append(
            {
                "promotion_id": 4_000_000 + len(rows) + 1,
                "promotion_name": f"{name}{sponsor_name}平台活动",
                "promotion_type": "满减" if "618" in name or "双" in name else "折扣",
                "promotion_scene": "平台",
                "promotion_level": 1,
                "start_time": start_time,
                "end_time": end_time,
                "rule_desc": f"{name}期间平台大促",
                "threshold_amount": Decimal("299"),
                "discount_amount": Decimal("50"),
                "discount_rate": None,
                "max_discount_amount": None,
                "sponsor_type": sponsor_type,
                "sponsor_id": sponsor_id,
                "etl_date": start_time.date(),
            }
        )

    return rows[:PROMOTION_TARGET_COUNT]


def _pick_coupon_scope(
    scope_type: str,
    shops: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    idx: int,
) -> tuple[int | None, str]:
    """按适用范围选择优惠券作用对象。"""
    if scope_type == "全平台":
        return None, "平台"
    if scope_type == "店铺":
        shop = shops[idx % len(shops)]
        return shop["shop_id"], shop["shop_name"]
    category = categories[idx % len(categories)]
    return category["category_id"], category["category_name"]


def _coupon_time_window(
    month_start: date, idx: int
) -> tuple[datetime, datetime, datetime, datetime]:
    """生成发券和用券时间窗口。"""
    issue_day = min(2 + (idx % 4) * 7, 25)
    issue_start = datetime(month_start.year, month_start.month, issue_day, 9, 0, 0)
    issue_end = issue_start + timedelta(days=5 + idx % 5, hours=14)
    use_start = issue_start
    use_end = issue_end + timedelta(days=15 + idx % 10)
    return issue_start, issue_end, use_start, use_end


def _build_coupon_values(
    coupon_type: str,
    idx: int,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    """按优惠券类型生成优惠字段。"""
    if coupon_type == "满减券":
        threshold = Decimal(
            COUPON_THRESHOLD_OPTIONS[idx % len(COUPON_THRESHOLD_OPTIONS)]
        )
        discount = Decimal(DISCOUNT_OPTIONS[idx % len(DISCOUNT_OPTIONS)])
        return threshold, discount, None, None
    if coupon_type == "折扣券":
        threshold = Decimal(
            COUPON_THRESHOLD_OPTIONS[(idx + 1) % len(COUPON_THRESHOLD_OPTIONS)]
        )
        discount_rate = Decimal(
            DISCOUNT_RATE_OPTIONS[idx % len(DISCOUNT_RATE_OPTIONS)]
        ) / Decimal("100")
        max_discount = Decimal(MAX_DISCOUNT_OPTIONS[idx % len(MAX_DISCOUNT_OPTIONS)])
        return threshold, None, discount_rate, max_discount
    if coupon_type == "运费券":
        discount = Decimal(
            TRANSPORT_COUPON_OPTIONS[idx % len(TRANSPORT_COUPON_OPTIONS)]
        )
        return Decimal("0"), discount, None, None
    threshold = Decimal(
        COUPON_THRESHOLD_OPTIONS[(idx + 2) % len(COUPON_THRESHOLD_OPTIONS)]
    )
    discount = Decimal(DISCOUNT_OPTIONS[(idx + 3) % len(DISCOUNT_OPTIONS)])
    return threshold, discount, None, None


def _build_coupon_name(
    coupon_type: str,
    campaign: str,
    scope_name: str,
    threshold_amount: Decimal | None,
    discount_amount: Decimal | None,
    discount_rate: Decimal | None,
) -> str:
    """生成优惠券名称。"""
    template = COUPON_NAME_TEMPLATES[coupon_type]
    return template.format(
        campaign=campaign,
        scope_name=scope_name,
        threshold=int(threshold_amount or 0),
        discount=int(discount_amount or 0),
        discount_rate=f"{(discount_rate or Decimal('1')) * 10:g}",
    )


def _build_coupon_rows(
    start_date: date,
    end_date: date,
    shops: list[dict[str, Any]],
    categories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成优惠券维度数据。"""
    rows: list[dict[str, Any]] = []
    month_starts = list(_iter_months(start_date, end_date))
    slots_per_month = max(
        (COUPON_TARGET_COUNT + max(len(month_starts), 1) - 1)
        // max(len(month_starts), 1),
        1,
    )

    for month_idx, month_start in enumerate(month_starts):
        for slot_idx in range(slots_per_month):
            if len(rows) >= COUPON_TARGET_COUNT:
                break
            coupon_type = COUPON_TYPES[(month_idx + slot_idx) % len(COUPON_TYPES)]
            scope_type = COUPON_SCOPE_TYPES[
                (month_idx + slot_idx) % len(COUPON_SCOPE_TYPES)
            ]
            scope_id, scope_name = _pick_coupon_scope(
                scope_type,
                shops,
                categories,
                month_idx * slots_per_month + slot_idx,
            )
            issue_start, issue_end, use_start, use_end = _coupon_time_window(
                month_start,
                slot_idx,
            )
            threshold, discount, discount_rate, max_discount = _build_coupon_values(
                coupon_type,
                month_idx * slots_per_month + slot_idx,
            )
            campaign = _month_campaign_label(month_start, slot_idx)
            total_issue_cnt = 20_000 + (month_idx * 971 + slot_idx * 113) % 180_000
            rows.append(
                {
                    "coupon_id": 5_000_000 + len(rows) + 1,
                    "coupon_name": _build_coupon_name(
                        coupon_type,
                        campaign,
                        scope_name,
                        threshold,
                        discount,
                        discount_rate,
                    ),
                    "coupon_type": coupon_type,
                    "coupon_scope_type": scope_type,
                    "coupon_scope_id": scope_id,
                    "threshold_amount": threshold,
                    "discount_amount": discount,
                    "discount_rate": discount_rate,
                    "max_discount_amount": max_discount,
                    "issue_start_time": issue_start,
                    "issue_end_time": min(
                        issue_end, datetime.combine(end_date, datetime.max.time())
                    ),
                    "use_start_time": use_start,
                    "use_end_time": min(
                        use_end, datetime.combine(end_date, datetime.max.time())
                    ),
                    "total_issue_cnt": total_issue_cnt,
                    "etl_date": issue_start.date(),
                }
            )
        if len(rows) >= COUPON_TARGET_COUNT:
            break

    return rows[:COUPON_TARGET_COUNT]


def _build_promotion_snapshot_rows(
    base_rows: list[dict[str, Any]],
    end_date: date,
) -> list[dict[str, Any]]:
    """将活动基础数据展开为有效期内的每日快照。"""
    snapshot_rows: list[dict[str, Any]] = []
    for row in base_rows:
        start_day = row["start_time"].date()
        end_day = min(row["end_time"].date(), end_date)
        for etl_date in _iter_dates(start_day, end_day):
            snapshot_rows.append(row | {"etl_date": etl_date})
    return snapshot_rows


def _build_coupon_snapshot_rows(
    base_rows: list[dict[str, Any]],
    end_date: date,
) -> list[dict[str, Any]]:
    """将优惠券基础数据展开为发券到用券结束期间的每日快照。"""
    snapshot_rows: list[dict[str, Any]] = []
    for row in base_rows:
        start_day = row["issue_start_time"].date()
        end_day = min(row["use_end_time"].date(), end_date)
        for etl_date in _iter_dates(start_day, end_day):
            snapshot_rows.append(row | {"etl_date": etl_date})
    return snapshot_rows


def run(ctx: RunContext) -> None:
    """生成并写入促销活动和优惠券维度数据。"""
    logger.info("Run batch3_marketing")
    metadata = MetaData()
    metadata.reflect(
        bind=ctx.engine,
        only=[
            "dwd_dim_shop_info_df",
            "dwd_dim_brand_info_df",
            "dwd_dim_category_info_df",
            "dwd_dim_promotion_info_df",
            "dwd_dim_coupon_info_df",
        ],
    )
    shop_table = metadata.tables["dwd_dim_shop_info_df"]
    brand_table = metadata.tables["dwd_dim_brand_info_df"]
    category_table = metadata.tables["dwd_dim_category_info_df"]
    promotion_table = metadata.tables["dwd_dim_promotion_info_df"]
    coupon_table = metadata.tables["dwd_dim_coupon_info_df"]

    with ctx.engine.begin() as conn:
        if _has_rows(conn, promotion_table) or _has_rows(conn, coupon_table):
            logger.info(
                "Promotion/coupon tables already contain data, skip batch3 generation"
            )
            return

        logger.info("batch3 loading source rows")
        shop_rows = _load_current_rows(conn, shop_table)
        brand_rows = _load_current_rows(conn, brand_table)
        category_rows = _load_current_rows(conn, category_table)
        leaf_categories = [
            row for row in category_rows if row["category_level"] == "三级"
        ]
        logger.info(
            "batch3 loaded source rows: shop_rows={} brand_rows={} category_rows={} leaf_categories={}",
            len(shop_rows),
            len(brand_rows),
            len(category_rows),
            len(leaf_categories),
        )
        if not shop_rows or not brand_rows or not leaf_categories:
            raise ValueError("批次3缺少生成营销维度所需的基础维表数据")

        start_date = date.fromisoformat(ctx.gen.start_date)
        end_date = date.fromisoformat(ctx.gen.end_date)
        promotion_base_rows = _build_promotion_rows(
            start_date,
            end_date,
            shop_rows,
            brand_rows,
        )
        coupon_base_rows = _build_coupon_rows(
            start_date,
            end_date,
            shop_rows,
            leaf_categories,
        )
        promotion_rows = _build_promotion_snapshot_rows(promotion_base_rows, end_date)
        coupon_rows = _build_coupon_snapshot_rows(coupon_base_rows, end_date)

        inserted_promotion_rows = bulk_insert(
            conn,
            promotion_table,
            promotion_rows,
            batch_size=ctx.gen.batch_size,
        )
        inserted_coupon_rows = bulk_insert(
            conn,
            coupon_table,
            coupon_rows,
            batch_size=ctx.gen.batch_size,
        )

    logger.info(
        "Generated batch3 marketing dimensions: promotion_base_rows={}, coupon_base_rows={}, promotion_snapshot_rows={}, coupon_snapshot_rows={}",
        len(promotion_base_rows),
        len(coupon_base_rows),
        inserted_promotion_rows,
        inserted_coupon_rows,
    )
