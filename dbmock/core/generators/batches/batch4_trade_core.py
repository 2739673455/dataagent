"""批次4：生成订单核心事实及支付、发货、退款等关联事实。"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from loguru import logger
from sqlalchemy import MetaData, select

from ..catalogs import (
    COMMENT_RATE,
    DAY_HOUR_OPTIONS,
    DELIVERY_TYPE_OPTIONS,
    FREIGHT_OPTIONS,
    GIFT_RATE,
    INITIAL_STOCK_BASE,
    NEGATIVE_COMMENTS,
    NEUTRAL_COMMENTS,
    ORDER_ACTIVITY_RATE,
    ORDER_COUPON_RATE,
    ORDER_DETAIL_COUNT_OPTIONS,
    ORDER_DETAIL_TARGET_COUNT,
    ORDER_SOURCE_OPTIONS,
    PAID_CANCEL_RATE,
    PAY_SCENE_OPTIONS,
    POINTS_DISCOUNT_OPTIONS,
    POINTS_DISCOUNT_RATE,
    POSITIVE_COMMENTS,
    REFUND_REASON_OPTIONS,
    REFUSED_RATE,
    RISK_ORDER_RATE,
    SENSITIVE_TAG_OPTIONS,
    SIGNED_REFUND_RATE,
    UNPAID_RATE,
    WAREHOUSE_ID_FALLBACK,
    WAREHOUSE_IDS,
)
from ..settings import RunContext
from ..utils.loaders import bulk_insert

MONEY_ZERO = Decimal("0.00")
MONEY_QUANT = Decimal("0.01")
DEFAULT_FREE_SHIPPING_THRESHOLD = Decimal("159")
FREE_SHIPPING_THRESHOLD_BY_ROOT = {
    "手机通讯": Decimal("0"),
    "数码电子": Decimal("199"),
    "家用电器": Decimal("299"),
    "电脑办公": Decimal("199"),
    "服饰内衣": Decimal("129"),
    "鞋靴箱包": Decimal("149"),
    "美妆个护": Decimal("99"),
    "食品饮料": Decimal("69"),
    "母婴玩具": Decimal("119"),
    "家居家装": Decimal("159"),
    "运动户外": Decimal("149"),
    "汽车用品": Decimal("169"),
}


def _masked_receiver_name(user_row: dict[str, Any]) -> str:
    """基于用户维度生成脱敏收件人姓名。"""
    raw_name = (
        user_row.get("nick_name")
        or user_row.get("user_name")
        or f"用户{user_row.get('user_id', '')}"
    )
    if len(raw_name) <= 1:
        return f"{raw_name}**"
    return f"{raw_name[:1]}**"


def _masked_receiver_phone(user_row: dict[str, Any]) -> str:
    """基于用户维度生成脱敏收件手机号。"""
    phone = user_row.get("phone")
    if phone:
        return str(phone)
    user_id = int(user_row.get("user_id", 0))
    tail = 1000 + user_id % 9000
    return f"138****{tail:04d}"


def _masked_receiver_address(detail_row: dict[str, Any]) -> str:
    """基于用户维度生成脱敏收货地址。"""
    province = detail_row.get("province_code") or "000000"
    city = detail_row.get("city_code") or "000000"
    district = detail_row.get("district_code") or "000000"
    return f"{province}-{city}-{district} xx路xx号"


def _pick_comment_bundle(ctx: RunContext, comment_level: int) -> tuple[str, str | None]:
    """按评分选择评价内容和敏感标签。"""
    if comment_level >= 4:
        return (
            POSITIVE_COMMENTS[ctx.rng.randrange(len(POSITIVE_COMMENTS))],
            None,
        )
    if comment_level == 3:
        return (
            NEUTRAL_COMMENTS[ctx.rng.randrange(len(NEUTRAL_COMMENTS))],
            None,
        )
    return (
        NEGATIVE_COMMENTS[ctx.rng.randrange(len(NEGATIVE_COMMENTS))],
        SENSITIVE_TAG_OPTIONS[ctx.rng.randrange(len(SENSITIVE_TAG_OPTIONS))],
    )


def _sentiment_by_level(comment_level: int) -> str:
    """按评分映射情感标签。"""
    if comment_level >= 4:
        return "正向"
    if comment_level == 3:
        return "中性"
    return "负向"


def _ensure_inventory_state(
    stock_state: dict[int, int],
    lock_state: dict[int, int],
    sku_id: int,
) -> None:
    """初始化某个 SKU 的库存状态。"""
    if sku_id not in stock_state:
        stock_state[sku_id] = INITIAL_STOCK_BASE + (sku_id % 3000)
        lock_state[sku_id] = 0


def _append_inventory_change(
    buffer: list[dict[str, Any]],
    seq: int,
    stock_state: dict[int, int],
    lock_state: dict[int, int],
    detail_row: dict[str, Any],
    change_type: str,
    biz_type: str,
    biz_id: str,
    change_time: datetime,
    change_qty: int,
    change_lock_qty: int,
    warehouse_id: int | None,
    remark: str,
) -> int:
    """追加一条库存变更，并更新当前库存状态。"""
    sku_id = detail_row["sku_id"]
    _ensure_inventory_state(stock_state, lock_state, sku_id)
    before_stock_qty = stock_state[sku_id]
    before_lock_qty = lock_state[sku_id]
    after_stock_qty = before_stock_qty + change_qty
    after_lock_qty = before_lock_qty + change_lock_qty
    stock_state[sku_id] = after_stock_qty
    lock_state[sku_id] = after_lock_qty
    sku_num = detail_row["sku_num"]
    unit_cost = (
        Decimal(str(detail_row["cost_amount"])) / Decimal(str(sku_num))
        if sku_num
        else MONEY_ZERO
    )
    seq += 1
    buffer.append(
        {
            "inventory_change_id": seq,
            "change_no": f"IC{seq}",
            "sku_id": sku_id,
            "spu_id": detail_row.get("spu_id"),
            "shop_id": detail_row.get("shop_id"),
            "warehouse_id": warehouse_id,
            "change_type": change_type,
            "biz_type": biz_type,
            "biz_id": biz_id,
            "before_stock_qty": before_stock_qty,
            "change_qty": change_qty,
            "after_stock_qty": after_stock_qty,
            "before_lock_qty": before_lock_qty,
            "change_lock_qty": change_lock_qty,
            "after_lock_qty": after_lock_qty,
            "unit_cost": unit_cost,
            "total_cost_change": unit_cost * Decimal(str(change_qty)),
            "operator_id": None,
            "operator_type": "系统",
            "remark": remark,
            "change_time": change_time,
            "etl_date": change_time.date(),
        }
    )
    return seq


def append_fulfillment_rows(
    order_id: int,
    detail_rows: list[dict[str, Any]],
    user_row: dict[str, Any],
    payment_types: list[dict[str, Any]],
    logistics_companies: list[dict[str, Any]],
    seq_state: dict[str, int],
    pay_buffer: list[dict[str, Any]],
    delivery_buffer: list[dict[str, Any]],
    refund_buffer: list[dict[str, Any]],
    refund_pay_buffer: list[dict[str, Any]],
) -> None:
    """基于单笔订单明细派生支付、发货、退款和退款打款记录。"""
    first_detail = detail_rows[0]
    order_status = first_detail["order_status"]
    user_id = first_detail["user_id"]
    shop_id = first_detail["shop_id"]
    seller_id = first_detail.get("seller_id")
    order_create_time = first_detail["order_create_time"]
    order_pay_time = first_detail.get("order_pay_time")
    order_delivery_time = first_detail.get("order_delivery_time")
    order_receive_time = first_detail.get("order_receive_time")
    order_cancel_time = first_detail.get("order_cancel_time")
    payment_row = payment_types[(order_id + user_id) % len(payment_types)]
    logistics_row = logistics_companies[(order_id + shop_id) % len(logistics_companies)]

    pay_detail_id = None
    payment_type_code = payment_row["payment_type_code"]
    if order_status != "未支付关闭":
        if order_pay_time is None:
            raise ValueError("已支付订单缺少支付时间")
        seq_state["pay_detail_id"] += 1
        pay_detail_id = seq_state["pay_detail_id"]
        total_pay_amount = sum(Decimal(str(row["paid_amount"])) for row in detail_rows)
        coupon_pay_amount = sum(
            Decimal(str(row["coupon_discount_amount"])) for row in detail_rows
        )
        points_pay_amount = sum(
            Decimal(str(row["points_discount_amount"])) for row in detail_rows
        )
        installment_cnt = None
        installment_fee_amount = MONEY_ZERO
        if payment_type_code == "HUABEI":
            installment_cnt = [3, 6, 12][order_id % 3]
            installment_fee_amount = Decimal("3.00") * installment_cnt
        pay_buffer.append(
            {
                "pay_detail_id": pay_detail_id,
                "pay_order_no": f"PO{order_id}",
                "third_party_pay_no": f"TP{order_id}{user_id}",
                "order_id": order_id,
                "user_id": user_id,
                "shop_id": shop_id,
                "seller_id": seller_id,
                "payment_type_code": payment_type_code,
                "payment_channel_code": payment_row.get("channel_code"),
                "pay_scene": PAY_SCENE_OPTIONS[
                    (order_id + user_id) % len(PAY_SCENE_OPTIONS)
                ],
                "pay_status": "成功",
                "currency_code": "CNY",
                "total_pay_amount": total_pay_amount,
                "cash_pay_amount": max(
                    total_pay_amount - coupon_pay_amount - points_pay_amount,
                    MONEY_ZERO,
                ),
                "coupon_pay_amount": coupon_pay_amount,
                "points_pay_amount": points_pay_amount,
                "balance_pay_amount": MONEY_ZERO,
                "installment_cnt": installment_cnt,
                "installment_fee_amount": installment_fee_amount,
                "pay_success_time": order_pay_time,
                "pay_fail_reason": None,
                "etl_date": order_pay_time.date(),
            }
        )

    if order_status in {"拒收退款", "签收后退款", "已完成"}:
        pay_time = order_pay_time or (order_create_time + timedelta(hours=1))
        delivery_time = order_delivery_time or (
            pay_time + timedelta(hours=(order_id % 36) + 8)
        )
        outbound_time = delivery_time - timedelta(hours=(order_id % 18) + 2)
        sign_time = None
        delivery_status = "拒收" if order_status == "拒收退款" else "已签收"
        if order_status in {"签收后退款", "已完成"}:
            sign_time = order_receive_time or (
                delivery_time + timedelta(days=(order_id % 7) + 1)
            )
        for detail_idx, detail_row in enumerate(detail_rows):
            seq_state["delivery_detail_id"] += 1
            delivery_buffer.append(
                {
                    "delivery_detail_id": seq_state["delivery_detail_id"],
                    "delivery_no": f"DL{detail_row['order_detail_id']}",
                    "order_id": order_id,
                    "order_detail_id": detail_row["order_detail_id"],
                    "user_id": detail_row["user_id"],
                    "shop_id": detail_row["shop_id"],
                    "seller_id": detail_row.get("seller_id"),
                    "warehouse_id": WAREHOUSE_IDS[
                        (order_id + detail_idx) % len(WAREHOUSE_IDS)
                    ],
                    "logistics_company_id": logistics_row["logistics_company_id"],
                    "tracking_no": f"TRK{detail_row['order_detail_id']}{shop_id}",
                    "delivery_status": delivery_status,
                    "delivery_type": DELIVERY_TYPE_OPTIONS[
                        (order_id + detail_idx) % len(DELIVERY_TYPE_OPTIONS)
                    ],
                    "receiver_name": _masked_receiver_name(user_row),
                    "receiver_phone": _masked_receiver_phone(user_row),
                    "receiver_province_code": detail_row.get("province_code"),
                    "receiver_city_code": detail_row.get("city_code"),
                    "receiver_district_code": detail_row.get("district_code"),
                    "receiver_address": _masked_receiver_address(detail_row),
                    "package_cnt": 1,
                    "total_weight": Decimal(str(detail_row["sku_num"]))
                    * Decimal("0.800"),
                    "freight_amount": Decimal(str(detail_row["freight_amount"])),
                    "outbound_time": outbound_time,
                    "delivery_time": delivery_time,
                    "sign_time": sign_time,
                    "etl_date": delivery_time.date(),
                }
            )

    if order_status in {"拒收退款", "签收后退款"}:
        for detail_idx, detail_row in enumerate(detail_rows):
            seq_state["refund_detail_id"] += 1
            refund_detail_id = seq_state["refund_detail_id"]
            if order_status == "拒收退款":
                refund_type = "退货退款"
                need_return_goods = 1
                refund_reason_code, refund_reason_desc = ("REJECT", "用户拒收")
                is_quality_issue = 0
                apply_time = order_cancel_time or (
                    (detail_row.get("order_pay_time") or order_create_time)
                    + timedelta(days=2)
                )
                receive_return_time = apply_time + timedelta(days=2)
            else:
                refund_reason_code, refund_reason_desc = REFUND_REASON_OPTIONS[
                    (order_id + detail_idx) % len(REFUND_REASON_OPTIONS)
                ]
                refund_type = "仅退款" if detail_idx % 2 == 0 else "退货退款"
                need_return_goods = 0 if refund_type == "仅退款" else 1
                is_quality_issue = (
                    1 if refund_reason_code in {"QUALITY", "DAMAGED"} else 0
                )
                base_time = (
                    order_receive_time
                    or detail_row.get("order_pay_time")
                    or order_create_time
                )
                apply_time = base_time + timedelta(days=(order_id % 10) + 7)
                receive_return_time = (
                    apply_time + timedelta(days=3) if need_return_goods == 1 else None
                )
            audit_time = apply_time + timedelta(hours=(detail_idx % 12) + 2)
            refund_success_time = (receive_return_time or audit_time) + timedelta(
                days=1
            )
            refund_amount = Decimal(str(detail_row["paid_amount"]))
            refund_freight_amount = (
                Decimal(str(detail_row["freight_amount"]))
                if order_status == "拒收退款"
                else MONEY_ZERO
            )
            refund_tax_amount = Decimal(str(detail_row["tax_amount"]))
            refund_buffer.append(
                {
                    "refund_detail_id": refund_detail_id,
                    "refund_no": f"RF{detail_row['order_detail_id']}",
                    "order_id": order_id,
                    "order_detail_id": detail_row["order_detail_id"],
                    "user_id": user_id,
                    "shop_id": detail_row["shop_id"],
                    "sku_id": detail_row["sku_id"],
                    "refund_type": refund_type,
                    "refund_reason_code": refund_reason_code,
                    "refund_reason_desc": refund_reason_desc,
                    "refund_status": "退款成功",
                    "refund_apply_amount": refund_amount,
                    "refund_approve_amount": refund_amount,
                    "refund_success_amount": refund_amount,
                    "refund_freight_amount": refund_freight_amount,
                    "refund_tax_amount": refund_tax_amount,
                    "is_quality_issue": is_quality_issue,
                    "need_return_goods": need_return_goods,
                    "return_tracking_no": f"RTR{detail_row['order_detail_id']}"
                    if need_return_goods == 1
                    else None,
                    "apply_time": apply_time,
                    "audit_time": audit_time,
                    "receive_return_time": receive_return_time,
                    "refund_success_time": refund_success_time,
                    "close_time": None,
                    "etl_date": apply_time.date(),
                }
            )

            seq_state["refund_pay_detail_id"] += 1
            refund_pay_buffer.append(
                {
                    "refund_pay_detail_id": seq_state["refund_pay_detail_id"],
                    "refund_no": f"RF{detail_row['order_detail_id']}",
                    "refund_detail_id": refund_detail_id,
                    "pay_detail_id": pay_detail_id,
                    "order_id": order_id,
                    "order_detail_id": detail_row["order_detail_id"],
                    "user_id": user_id,
                    "payment_type_code": payment_type_code,
                    "refund_channel_code": payment_row.get("channel_code"),
                    "refund_status": "成功",
                    "refund_amount": Decimal(str(detail_row["paid_amount"])),
                    "refund_account_type": "原路返回",
                    "refund_apply_time": apply_time,
                    "refund_pay_time": refund_success_time + timedelta(hours=6),
                    "refund_fail_reason": None,
                    "etl_date": (refund_success_time + timedelta(hours=6)).date(),
                }
            )


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

    def random_row(
        self, rng, predicate=None, max_attempts: int = 64
    ) -> dict[str, Any] | None:
        """从当前有效版本中随机选择一条记录。"""
        if not self._active_keys:
            return None
        for _ in range(max_attempts):
            row = self._active_rows[rng.randrange(len(self._active_rows))]
            row_key = (row[self._key_field], row["start_date"])
            if row_key not in self._active_keys:
                continue
            if predicate is not None and not predicate(row):
                continue
            return row
        for row in self._active_rows:
            row_key = (row[self._key_field], row["start_date"])
            if row_key not in self._active_keys:
                continue
            if predicate is not None and not predicate(row):
                continue
            return row
        return None


def _money(value: Decimal | int | float) -> Decimal:
    """统一金额字段精度。"""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _resolve_freight_total(
    ctx: RunContext, detail_rows: list[dict[str, Any]]
) -> Decimal:
    """按类目、店铺和订单金额生成更真实的订单级运费。"""
    order_amount = _money(sum(row["_detail_amount"] for row in detail_rows))
    root_name = (
        detail_rows[0]["_shop_row"].get("industry_type")
        or detail_rows[0]["_spu_row"].get("_root_name")
        or ""
    )
    threshold = FREE_SHIPPING_THRESHOLD_BY_ROOT.get(
        root_name,
        DEFAULT_FREE_SHIPPING_THRESHOLD,
    )
    base_value = FREIGHT_OPTIONS[ctx.rng.randrange(len(FREIGHT_OPTIONS))]
    base_freight = _money(Decimal(str(base_value if base_value > 0 else 6)))

    if any(row["_shop_row"].get("is_global") == 1 for row in detail_rows):
        return _money(base_freight + Decimal("12"))

    if all(row["_shop_row"].get("is_self_operated") == 1 for row in detail_rows):
        threshold = max(threshold - Decimal("30"), MONEY_ZERO)

    if order_amount >= threshold:
        if threshold == MONEY_ZERO or ctx.rng.random() < 0.92:
            return MONEY_ZERO
        return _money(min(base_freight, Decimal("6")))

    near_threshold = _money(threshold * Decimal("0.7"))
    if order_amount >= near_threshold and ctx.rng.random() < 0.20:
        return MONEY_ZERO

    return base_freight


def _load_all_rows(conn, table) -> list[dict[str, Any]]:
    """加载整张表数据。"""
    return [dict(row) for row in conn.execute(select(table)).mappings()]


def _has_rows(conn, table) -> bool:
    """判断目标表是否已有数据。"""
    return conn.execute(select(table.c.id).limit(1)).first() is not None


def _build_weighted_dates(
    start_date: date, end_date: date
) -> tuple[list[date], list[float]]:
    """生成按促销季节加权的日期权重。"""
    dates: list[date] = []
    weights: list[float] = []
    current = start_date
    while current <= end_date:
        weight = 1.0
        if current.month == 6 and 1 <= current.day <= 18:
            weight *= 8
        elif current.month == 11 and 1 <= current.day <= 11:
            weight *= 10
        elif current.month == 12 and 1 <= current.day <= 12:
            weight *= 5
        elif current.month == 1 and 5 <= current.day <= 20:
            weight *= 3
        if current.weekday() >= 5:
            weight *= 1.15
        dates.append(current)
        weights.append(weight)
        current += timedelta(days=1)
    return dates, weights


def _allocate_daily_detail_counts(
    start_date: date,
    end_date: date,
    total_target: int,
) -> list[tuple[date, int]]:
    """按权重把订单明细量分配到每天。"""
    dates, weights = _build_weighted_dates(start_date, end_date)
    total_weight = sum(weights)
    raw_counts = [total_target * weight / total_weight for weight in weights]
    floor_counts = [int(count) for count in raw_counts]
    remainder = total_target - sum(floor_counts)
    fractions = sorted(
        ((raw_counts[idx] - floor_counts[idx], idx) for idx in range(len(dates))),
        reverse=True,
    )
    for _, idx in fractions[:remainder]:
        floor_counts[idx] += 1
    return list(zip(dates, floor_counts, strict=True))


def _pick_order_time(rng, current_date: date) -> datetime:
    """生成订单创建时间。"""
    hour = DAY_HOUR_OPTIONS[rng.randrange(len(DAY_HOUR_OPTIONS))]
    minute = rng.randrange(60)
    second = rng.randrange(60)
    return datetime.combine(current_date, time(hour, minute, second))


def _build_version_index(
    rows: list[dict[str, Any]], key_field: str
) -> dict[Any, list[dict[str, Any]]]:
    """按业务主键聚合拉链版本记录。"""
    index: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        index[row[key_field]].append(row)
    for key in index:
        index[key].sort(key=lambda item: item["start_date"])
    return index


def _find_version(
    version_rows: list[dict[str, Any]], current_date: date
) -> dict[str, Any] | None:
    """从版本列表中查找指定日期生效的记录。"""
    for row in version_rows:
        if row["start_date"] <= current_date <= row["end_date"]:
            return row
    return None


def _build_snapshot_index(
    rows: list[dict[str, Any]],
) -> dict[date, list[dict[str, Any]]]:
    """按 etl_date 聚合每日快照。"""
    index: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        index[row["etl_date"]].append(row)
    return index


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


def _flush_buffer(
    conn,
    table,
    buffer: list[dict[str, Any]],
    batch_size: int,
    inserted_total: int,
) -> tuple[int, int]:
    """按批次写入并返回本次与累计写入量。"""
    inserted = bulk_insert(conn, table, buffer, batch_size=batch_size)
    if inserted <= 0:
        return 0, inserted_total
    buffer.clear()
    inserted_total += inserted
    return inserted, inserted_total


def _sample_same_shop_sku(
    pool: ActiveVersionPool,
    rng,
    shop_id: int,
    used_sku_ids: set[int],
) -> dict[str, Any] | None:
    """尽量从同店铺中选择未重复的 SKU。"""
    return pool.random_row(
        rng,
        predicate=lambda row: row["shop_id"] == shop_id
        and row["sku_id"] not in used_sku_ids,
        max_attempts=96,
    )


def _pick_order_outcome(rng) -> str:
    """按设定比例选择订单结果。"""
    value = rng.random()
    if value < UNPAID_RATE:
        return "未支付关闭"
    if value < UNPAID_RATE + (1 - UNPAID_RATE) * PAID_CANCEL_RATE:
        return "支付后取消"
    if value < UNPAID_RATE + (1 - UNPAID_RATE) * (PAID_CANCEL_RATE + REFUSED_RATE):
        return "拒收退款"
    if value < UNPAID_RATE + (1 - UNPAID_RATE) * (
        PAID_CANCEL_RATE + REFUSED_RATE + SIGNED_REFUND_RATE
    ):
        return "签收后退款"
    return "已完成"


def _promotion_discount_total(
    promotion: dict[str, Any], order_amount: Decimal
) -> Decimal:
    """计算订单级活动优惠总额。"""
    promotion_type = promotion["promotion_type"]
    threshold = _money(promotion.get("threshold_amount") or 0)
    discount_amount = _money(promotion.get("discount_amount") or 0)
    discount_rate = promotion.get("discount_rate")
    max_discount = _money(promotion.get("max_discount_amount") or 0)

    if promotion_type == "满减":
        if order_amount < threshold or discount_amount <= MONEY_ZERO:
            return MONEY_ZERO
        return min(discount_amount, order_amount)
    if promotion_type == "折扣":
        if order_amount < threshold or discount_rate is None:
            return MONEY_ZERO
        discount = order_amount * (Decimal("1") - Decimal(str(discount_rate)))
        if max_discount > MONEY_ZERO:
            discount = min(discount, max_discount)
        return _money(max(discount, MONEY_ZERO))
    if promotion_type in {"秒杀", "拼团"}:
        if discount_amount <= MONEY_ZERO:
            return MONEY_ZERO
        return _money(min(discount_amount, order_amount * Decimal("0.35")))
    return MONEY_ZERO


def _coupon_discount_total(
    coupon: dict[str, Any],
    order_amount: Decimal,
    freight_amount: Decimal,
) -> Decimal:
    """计算订单级优惠券总额。"""
    coupon_type = coupon["coupon_type"]
    threshold = _money(coupon.get("threshold_amount") or 0)
    discount_amount = _money(coupon.get("discount_amount") or 0)
    discount_rate = coupon.get("discount_rate")
    max_discount = _money(coupon.get("max_discount_amount") or 0)

    if coupon_type == "满减券":
        if order_amount < threshold:
            return MONEY_ZERO
        return _money(min(discount_amount, order_amount))
    if coupon_type == "折扣券":
        if order_amount < threshold or discount_rate is None:
            return MONEY_ZERO
        discount = order_amount * (Decimal("1") - Decimal(str(discount_rate)))
        if max_discount > MONEY_ZERO:
            discount = min(discount, max_discount)
        return _money(max(discount, MONEY_ZERO))
    if coupon_type == "运费券":
        return _money(min(discount_amount, freight_amount))
    if order_amount < threshold:
        return MONEY_ZERO
    return _money(min(discount_amount, order_amount * Decimal("0.3")))


def _allocate_amount(total_amount: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """按权重分摊金额，并处理最后一项补差。"""
    if total_amount <= MONEY_ZERO:
        return [MONEY_ZERO for _ in weights]
    weight_sum = sum(weights)
    if weight_sum <= MONEY_ZERO:
        return [MONEY_ZERO for _ in weights]
    allocations: list[Decimal] = []
    allocated = MONEY_ZERO
    for idx, weight in enumerate(weights):
        if idx == len(weights) - 1:
            value = _money(total_amount - allocated)
        else:
            value = _money(total_amount * weight / weight_sum)
            allocated += value
        allocations.append(value)
    return allocations


def _allocate_amount_to_indexes(
    total_amount: Decimal,
    detail_rows: list[dict[str, Any]],
    eligible_indexes: list[int],
) -> list[Decimal]:
    """只在命中的明细行上做金额分摊。"""
    allocations = [MONEY_ZERO for _ in detail_rows]
    if not eligible_indexes or total_amount <= MONEY_ZERO:
        return allocations
    eligible_weights = [detail_rows[idx]["_detail_amount"] for idx in eligible_indexes]
    eligible_allocations = _allocate_amount(total_amount, eligible_weights)
    for index, amount in zip(eligible_indexes, eligible_allocations, strict=True):
        allocations[index] = amount
    return allocations


def _eligible_promotion_indexes(
    promotion: dict[str, Any],
    detail_rows: list[dict[str, Any]],
) -> list[int]:
    """返回活动命中的明细下标。"""
    scene = promotion["promotion_scene"]
    if scene == "平台":
        return list(range(len(detail_rows)))
    if scene == "店铺":
        sponsor_id = promotion.get("sponsor_id")
        return [
            idx
            for idx, row in enumerate(detail_rows)
            if row["_sku_row"]["shop_id"] == sponsor_id
        ]
    if scene == "品牌":
        sponsor_id = promotion.get("sponsor_id")
        return [
            idx
            for idx, row in enumerate(detail_rows)
            if row["_sku_row"].get("brand_id") == sponsor_id
        ]
    return []


def _eligible_coupon_indexes(
    coupon: dict[str, Any],
    detail_rows: list[dict[str, Any]],
) -> list[int]:
    """返回优惠券命中的明细下标。"""
    scope_type = coupon["coupon_scope_type"]
    if scope_type == "全平台":
        return list(range(len(detail_rows)))
    if scope_type == "店铺":
        scope_id = coupon.get("coupon_scope_id")
        return [
            idx
            for idx, row in enumerate(detail_rows)
            if row["_sku_row"]["shop_id"] == scope_id
        ]
    if scope_type == "类目":
        scope_id = coupon.get("coupon_scope_id")
        return [
            idx
            for idx, row in enumerate(detail_rows)
            if row["_sku_row"]["category_id"] == scope_id
        ]
    return []


def _choose_promotion(
    rng,
    promotions: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, Decimal, list[int]]:
    """选择订单可命中的活动，并返回命中明细。"""
    if not promotions or rng.random() >= ORDER_ACTIVITY_RATE:
        return None, MONEY_ZERO, []
    candidates = []
    for promotion in promotions:
        eligible_indexes = _eligible_promotion_indexes(promotion, detail_rows)
        if not eligible_indexes:
            continue
        eligible_amount = _money(
            sum(detail_rows[idx]["_detail_amount"] for idx in eligible_indexes)
        )
        discount_total = _promotion_discount_total(promotion, eligible_amount)
        if discount_total > MONEY_ZERO:
            candidates.append((promotion, discount_total, eligible_indexes))
    if not candidates:
        return None, MONEY_ZERO, []
    return candidates[rng.randrange(len(candidates))]


def _choose_coupon(
    rng,
    coupons: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
    freight_amount: Decimal,
) -> tuple[dict[str, Any] | None, Decimal, list[int]]:
    """选择订单可命中的优惠券，并返回命中明细。"""
    if not coupons or rng.random() >= ORDER_COUPON_RATE:
        return None, MONEY_ZERO, []
    candidates = []
    for coupon in coupons:
        eligible_indexes = _eligible_coupon_indexes(coupon, detail_rows)
        if not eligible_indexes:
            continue
        eligible_amount = _money(
            sum(detail_rows[idx]["_detail_amount"] for idx in eligible_indexes)
        )
        discount_total = _coupon_discount_total(coupon, eligible_amount, freight_amount)
        if discount_total > MONEY_ZERO:
            candidates.append((coupon, discount_total, eligible_indexes))
    if not candidates:
        return None, MONEY_ZERO, []
    return candidates[rng.randrange(len(candidates))]


def run(ctx: RunContext) -> None:
    """生成并写入订单明细及其支付、发货、退款关联事实数据。"""
    logger.info("Run batch4_trade_core")
    metadata = MetaData()
    metadata.reflect(
        bind=ctx.engine,
        only=[
            "dwd_dim_user_info_df",
            "dwd_dim_shop_info_df",
            "dwd_dim_spu_info_df",
            "dwd_dim_sku_info_df",
            "dwd_dim_payment_type_df",
            "dwd_dim_logistics_company_df",
            "dwd_dim_promotion_info_df",
            "dwd_dim_coupon_info_df",
            "dwd_fact_trade_order_detail_di",
            "dwd_fact_trade_order_detail_activity_di",
            "dwd_fact_trade_order_detail_coupon_di",
            "dwd_fact_trade_pay_detail_di",
            "dwd_fact_trade_delivery_detail_di",
            "dwd_fact_trade_refund_detail_di",
            "dwd_fact_trade_refund_pay_detail_di",
            "dwd_fact_service_comment_detail_di",
            "dwd_fact_inventory_change_di",
        ],
    )
    user_table = metadata.tables["dwd_dim_user_info_df"]
    shop_table = metadata.tables["dwd_dim_shop_info_df"]
    spu_table = metadata.tables["dwd_dim_spu_info_df"]
    sku_table = metadata.tables["dwd_dim_sku_info_df"]
    payment_type_table = metadata.tables["dwd_dim_payment_type_df"]
    logistics_table = metadata.tables["dwd_dim_logistics_company_df"]
    promotion_table = metadata.tables["dwd_dim_promotion_info_df"]
    coupon_table = metadata.tables["dwd_dim_coupon_info_df"]
    order_detail_table = metadata.tables["dwd_fact_trade_order_detail_di"]
    order_activity_table = metadata.tables["dwd_fact_trade_order_detail_activity_di"]
    order_coupon_table = metadata.tables["dwd_fact_trade_order_detail_coupon_di"]
    pay_table = metadata.tables["dwd_fact_trade_pay_detail_di"]
    delivery_table = metadata.tables["dwd_fact_trade_delivery_detail_di"]
    refund_table = metadata.tables["dwd_fact_trade_refund_detail_di"]
    refund_pay_table = metadata.tables["dwd_fact_trade_refund_pay_detail_di"]
    comment_table = metadata.tables["dwd_fact_service_comment_detail_di"]
    inventory_table = metadata.tables["dwd_fact_inventory_change_di"]

    with ctx.engine.begin() as conn:
        if _has_rows(conn, order_detail_table):
            logger.info(
                "Order detail tables already contain data, skip batch4 generation"
            )
            return

        user_rows = _load_all_rows(conn, user_table)
        sku_rows = _load_all_rows(conn, sku_table)
        spu_rows = _load_all_rows(conn, spu_table)
        shop_rows = _load_all_rows(conn, shop_table)
        payment_type_rows = _load_all_rows(conn, payment_type_table)
        logistics_rows = _load_all_rows(conn, logistics_table)
        promotion_rows = _load_all_rows(conn, promotion_table)
        coupon_rows = _load_all_rows(conn, coupon_table)
        if not user_rows or not sku_rows or not spu_rows or not shop_rows:
            raise ValueError("批次4缺少用户、店铺、SPU 或 SKU 维度数据")
        if not payment_type_rows or not logistics_rows:
            raise ValueError("批次4缺少支付方式或物流公司维度数据")

        shop_index = _build_version_index(shop_rows, "shop_id")
        spu_index = _build_version_index(spu_rows, "spu_id")
        promotion_by_date = _build_snapshot_index(promotion_rows)
        coupon_by_date = _build_snapshot_index(coupon_rows)
        payment_types = [row for row in payment_type_rows if row.get("is_current") == 1]
        logistics_companies = [
            row for row in logistics_rows if row.get("is_current") == 1
        ]
        user_pool = ActiveVersionPool(user_rows, "user_id")
        sku_pool = ActiveVersionPool(sku_rows, "sku_id")

        detail_buffer: list[dict[str, Any]] = []
        activity_buffer: list[dict[str, Any]] = []
        coupon_buffer: list[dict[str, Any]] = []
        pay_buffer: list[dict[str, Any]] = []
        delivery_buffer: list[dict[str, Any]] = []
        refund_buffer: list[dict[str, Any]] = []
        refund_pay_buffer: list[dict[str, Any]] = []
        comment_buffer: list[dict[str, Any]] = []
        inventory_buffer: list[dict[str, Any]] = []
        detail_inserted = 0
        activity_inserted = 0
        coupon_inserted = 0
        pay_inserted = 0
        delivery_inserted = 0
        refund_inserted = 0
        refund_pay_inserted = 0
        comment_inserted = 0
        inventory_inserted = 0

        order_id_seq = 6_000_000
        order_detail_id_seq = 7_000_000
        order_detail_activity_id_seq = 8_000_000
        order_detail_coupon_id_seq = 9_000_000
        fulfillment_seq_state = {
            "pay_detail_id": 10_000_000,
            "delivery_detail_id": 11_000_000,
            "refund_detail_id": 12_000_000,
            "refund_pay_detail_id": 13_000_000,
        }
        comment_seq = 18_000_000
        inventory_seq = 19_000_000
        stock_state: dict[int, int] = {}
        lock_state: dict[int, int] = {}
        user_seen: set[int] = set()
        day_plans = _allocate_daily_detail_counts(
            date.fromisoformat(ctx.gen.start_date),
            date.fromisoformat(ctx.gen.end_date),
            ORDER_DETAIL_TARGET_COUNT,
        )

        for current_date, daily_target in day_plans:
            if daily_target <= 0:
                continue
            user_pool.advance(current_date)
            sku_pool.advance(current_date)
            if (
                sku_pool.random_row(ctx.rng) is None
                or user_pool.random_row(ctx.rng) is None
            ):
                continue

            daily_created = 0
            while daily_created < daily_target:
                detail_target = ORDER_DETAIL_COUNT_OPTIONS[
                    ctx.rng.randrange(len(ORDER_DETAIL_COUNT_OPTIONS))
                ]
                detail_target = min(detail_target, daily_target - daily_created)
                order_create_time = _pick_order_time(ctx.rng, current_date)
                user_row = user_pool.random_row(ctx.rng)
                primary_sku = sku_pool.random_row(ctx.rng)
                if user_row is None or primary_sku is None:
                    break

                detail_rows: list[dict[str, Any]] = []
                used_sku_ids: set[int] = set()
                sku_candidates = [primary_sku]
                used_sku_ids.add(primary_sku["sku_id"])
                while len(sku_candidates) < detail_target:
                    same_shop_sku = _sample_same_shop_sku(
                        sku_pool,
                        ctx.rng,
                        primary_sku["shop_id"],
                        used_sku_ids,
                    )
                    if same_shop_sku is None:
                        break
                    sku_candidates.append(same_shop_sku)
                    used_sku_ids.add(same_shop_sku["sku_id"])

                order_outcome = _pick_order_outcome(ctx.rng)
                is_first_order = 1 if user_row["user_id"] not in user_seen else 0
                user_seen.add(user_row["user_id"])
                order_id_seq += 1
                order_id = order_id_seq
                order_no = f"OD{order_id}"
                trade_no = f"TN{order_id}"
                order_source = ORDER_SOURCE_OPTIONS[
                    ctx.rng.randrange(len(ORDER_SOURCE_OPTIONS))
                ]
                order_pay_time = None
                order_delivery_time = None
                order_receive_time = None
                order_cancel_time = None
                cancel_stage = None
                if order_outcome == "未支付关闭":
                    order_cancel_time = order_create_time + timedelta(hours=24)
                    cancel_stage = "未支付取消"
                else:
                    order_pay_time = order_create_time + timedelta(
                        minutes=ctx.rng.randrange(3, 180)
                    )
                    if order_outcome in {"拒收退款", "签收后退款", "已完成"}:
                        order_delivery_time = order_pay_time + timedelta(
                            hours=(order_id % 36) + 8
                        )
                    if order_outcome in {"签收后退款", "已完成"}:
                        if order_delivery_time is None:
                            raise ValueError("签收类订单缺少发货时间")
                        order_receive_time = order_delivery_time + timedelta(
                            days=(order_id % 7) + 1
                        )
                    if order_outcome == "支付后取消":
                        order_cancel_time = order_pay_time + timedelta(
                            hours=ctx.rng.randrange(1, 24)
                        )
                        cancel_stage = "支付后取消"
                    elif order_outcome == "拒收退款":
                        if order_delivery_time is None:
                            raise ValueError("拒收订单缺少发货时间")
                        order_cancel_time = order_delivery_time + timedelta(
                            days=(order_id % 5) + 1
                        )
                        cancel_stage = "拒收"

                for idx, sku_row in enumerate(sku_candidates):
                    spu_row = _find_version(spu_index[sku_row["spu_id"]], current_date)
                    if spu_row is None:
                        continue
                    shop_row = _find_version(
                        shop_index.get(sku_row["shop_id"], []),
                        current_date,
                    )
                    if shop_row is None:
                        continue
                    sku_num = 1 + ((order_id + idx) % 3)
                    order_detail_amount = _money(
                        Decimal(str(sku_row["sale_price"])) * sku_num
                    )
                    detail_rows.append(
                        {
                            "_spu_row": spu_row,
                            "_sku_row": sku_row,
                            "_shop_row": shop_row,
                            "_detail_amount": order_detail_amount,
                            "order_detail_id": order_detail_id_seq + 1,
                            "sku_num": sku_num,
                        }
                    )
                    order_detail_id_seq += 1

                if not detail_rows:
                    continue

                order_amount = _money(sum(row["_detail_amount"] for row in detail_rows))
                freight_total = _resolve_freight_total(ctx, detail_rows)
                tax_total = MONEY_ZERO
                if any(row["_shop_row"].get("is_global") == 1 for row in detail_rows):
                    tax_total = _money(order_amount * Decimal("0.05"))

                promotion, activity_total, promotion_indexes = _choose_promotion(
                    ctx.rng,
                    promotion_by_date.get(current_date, []),
                    detail_rows,
                )
                activity_total = min(activity_total, order_amount)
                coupon, coupon_total, coupon_indexes = _choose_coupon(
                    ctx.rng,
                    coupon_by_date.get(current_date, []),
                    detail_rows,
                    freight_total,
                )
                coupon_total = min(
                    coupon_total,
                    max(order_amount - activity_total, MONEY_ZERO),
                )
                points_total = MONEY_ZERO
                if ctx.rng.random() < POINTS_DISCOUNT_RATE:
                    points_total = _money(
                        min(
                            Decimal(
                                POINTS_DISCOUNT_OPTIONS[
                                    ctx.rng.randrange(len(POINTS_DISCOUNT_OPTIONS))
                                ]
                            ),
                            max(
                                order_amount - activity_total - coupon_total, MONEY_ZERO
                            )
                            * Decimal("0.08"),
                        )
                    )

                activity_allocations = _allocate_amount_to_indexes(
                    activity_total,
                    detail_rows,
                    promotion_indexes,
                )
                coupon_allocations = _allocate_amount_to_indexes(
                    coupon_total,
                    detail_rows,
                    coupon_indexes,
                )
                amount_weights = [row["_detail_amount"] for row in detail_rows]
                points_allocations = _allocate_amount(points_total, amount_weights)
                freight_allocations = _allocate_amount(freight_total, amount_weights)
                tax_allocations = _allocate_amount(tax_total, amount_weights)

                if promotion is not None:
                    if promotion["promotion_type"] == "秒杀":
                        order_scene = "秒杀"
                    elif promotion["promotion_type"] == "拼团":
                        order_scene = "拼团"
                    else:
                        order_scene = "普通"
                elif any(row["_spu_row"].get("is_presale") == 1 for row in detail_rows):
                    order_scene = "预售"
                else:
                    order_scene = "普通"

                order_fact_rows: list[dict[str, Any]] = []
                for idx, detail_row in enumerate(detail_rows):
                    spu_row = detail_row["_spu_row"]
                    sku_row = detail_row["_sku_row"]
                    shop_row = detail_row["_shop_row"]
                    activity_amount = activity_allocations[idx]
                    coupon_amount = coupon_allocations[idx]
                    points_amount = points_allocations[idx]
                    freight_amount = freight_allocations[idx]
                    tax_amount = tax_allocations[idx]
                    payable_amount = _money(
                        detail_row["_detail_amount"]
                        - activity_amount
                        - coupon_amount
                        - points_amount
                        + freight_amount
                        + tax_amount
                    )
                    paid_amount = (
                        MONEY_ZERO if order_outcome == "未支付关闭" else payable_amount
                    )
                    order_status = order_outcome
                    is_order_finish = (
                        1 if order_receive_time or order_cancel_time else 0
                    )
                    order_fact_row = {
                        "order_detail_id": detail_row["order_detail_id"],
                        "order_id": order_id,
                        "parent_order_id": None,
                        "trade_no": trade_no,
                        "order_no": order_no,
                        "order_source": order_source,
                        "order_scene": order_scene,
                        "order_status": order_status,
                        "user_id": user_row["user_id"],
                        "shop_id": sku_row["shop_id"],
                        "seller_id": shop_row.get("seller_id"),
                        "sku_id": sku_row["sku_id"],
                        "spu_id": sku_row["spu_id"],
                        "category_id": sku_row["category_id"],
                        "brand_id": sku_row.get("brand_id"),
                        "province_code": user_row.get("province_code"),
                        "city_code": user_row.get("city_code"),
                        "district_code": user_row.get("district_code"),
                        "is_first_order": is_first_order,
                        "is_cross_border": shop_row.get("is_global", 0),
                        "is_pre_sale": spu_row.get("is_presale", 0),
                        "is_gift": 1 if ctx.rng.random() < GIFT_RATE else 0,
                        "is_risk_order": 1 if ctx.rng.random() < RISK_ORDER_RATE else 0,
                        "is_order_finish": is_order_finish,
                        "sku_num": detail_row["sku_num"],
                        "sku_origin_price": _money(sku_row["origin_price"]),
                        "sku_sale_price": _money(sku_row["sale_price"]),
                        "order_detail_amount": detail_row["_detail_amount"],
                        "activity_discount_amount": activity_amount,
                        "coupon_discount_amount": coupon_amount,
                        "points_discount_amount": points_amount,
                        "freight_amount": freight_amount,
                        "tax_amount": tax_amount,
                        "payable_amount": payable_amount,
                        "paid_amount": paid_amount,
                        "cost_amount": _money(
                            Decimal(str(sku_row["cost_price"])) * detail_row["sku_num"]
                        ),
                        "order_create_time": order_create_time,
                        "order_pay_time": order_pay_time,
                        "order_delivery_time": order_delivery_time,
                        "order_receive_time": order_receive_time,
                        "order_cancel_time": order_cancel_time,
                        "cancel_stage": cancel_stage,
                        "etl_date": current_date,
                    }
                    detail_buffer.append(order_fact_row)
                    order_fact_rows.append(order_fact_row)

                    if promotion is not None and activity_amount > MONEY_ZERO:
                        order_detail_activity_id_seq += 1
                        activity_buffer.append(
                            {
                                "order_detail_activity_id": order_detail_activity_id_seq,
                                "order_detail_id": detail_row["order_detail_id"],
                                "order_id": order_id,
                                "promotion_id": promotion["promotion_id"],
                                "promotion_type": promotion["promotion_type"],
                                "promotion_level": promotion.get("promotion_level"),
                                "promotion_discount_amount": activity_amount,
                                "rule_snapshot": promotion.get("rule_desc"),
                                "order_create_time": order_create_time,
                                "etl_date": current_date,
                            }
                        )

                    if coupon is not None and coupon_amount > MONEY_ZERO:
                        order_detail_coupon_id_seq += 1
                        receive_start = coupon["issue_start_time"]
                        receive_end = min(order_create_time, coupon["issue_end_time"])
                        if receive_end < receive_start:
                            coupon_receive_time = receive_start
                        else:
                            delta_seconds = int(
                                (receive_end - receive_start).total_seconds()
                            )
                            coupon_receive_time = receive_start + timedelta(
                                seconds=ctx.rng.randrange(delta_seconds + 1)
                            )
                        coupon_buffer.append(
                            {
                                "order_detail_coupon_id": order_detail_coupon_id_seq,
                                "order_detail_id": detail_row["order_detail_id"],
                                "order_id": order_id,
                                "coupon_id": coupon["coupon_id"],
                                "coupon_user_id": user_row["user_id"],
                                "coupon_type": coupon["coupon_type"],
                                "coupon_scope_type": coupon.get("coupon_scope_type"),
                                "coupon_discount_amount": coupon_amount,
                                "coupon_batch_no": f"CPN{coupon['coupon_id']}{current_date.strftime('%Y%m%d')}",
                                "coupon_receive_time": coupon_receive_time,
                                "coupon_use_time": order_create_time,
                                "order_create_time": order_create_time,
                                "etl_date": current_date,
                            }
                        )

                delivery_start = len(delivery_buffer)
                refund_start = len(refund_buffer)
                append_fulfillment_rows(
                    order_id=order_id,
                    detail_rows=order_fact_rows,
                    user_row=user_row,
                    payment_types=payment_types,
                    logistics_companies=logistics_companies,
                    seq_state=fulfillment_seq_state,
                    pay_buffer=pay_buffer,
                    delivery_buffer=delivery_buffer,
                    refund_buffer=refund_buffer,
                    refund_pay_buffer=refund_pay_buffer,
                )

                warehouse_by_detail = {
                    row["order_detail_id"]: row["warehouse_id"]
                    for row in delivery_buffer[delivery_start:]
                    if row.get("order_detail_id") is not None
                    and row.get("warehouse_id") is not None
                }
                detail_by_id = {row["order_detail_id"]: row for row in order_fact_rows}
                for detail_row in order_fact_rows:
                    warehouse_id = warehouse_by_detail.get(
                        detail_row["order_detail_id"],
                        WAREHOUSE_ID_FALLBACK,
                    )
                    inventory_seq = _append_inventory_change(
                        inventory_buffer,
                        inventory_seq,
                        stock_state,
                        lock_state,
                        detail_row,
                        "锁定",
                        "下单",
                        str(detail_row["order_id"]),
                        detail_row["order_create_time"],
                        0,
                        detail_row["sku_num"],
                        warehouse_id,
                        "下单锁定库存",
                    )
                    if detail_row["order_status"] in {"未支付关闭", "支付后取消"}:
                        cancel_time = detail_row.get("order_cancel_time")
                        if cancel_time is None:
                            raise ValueError("取消订单缺少取消时间")
                        inventory_seq = _append_inventory_change(
                            inventory_buffer,
                            inventory_seq,
                            stock_state,
                            lock_state,
                            detail_row,
                            "解锁",
                            "取消",
                            str(detail_row["order_id"]),
                            cancel_time,
                            0,
                            -detail_row["sku_num"],
                            warehouse_id,
                            "订单取消释放锁定库存",
                        )
                    else:
                        pay_time = detail_row.get("order_pay_time")
                        if pay_time is None:
                            raise ValueError("支付成功订单缺少支付时间")
                        inventory_seq = _append_inventory_change(
                            inventory_buffer,
                            inventory_seq,
                            stock_state,
                            lock_state,
                            detail_row,
                            "出库",
                            "支付",
                            str(detail_row["order_id"]),
                            pay_time,
                            -detail_row["sku_num"],
                            -detail_row["sku_num"],
                            warehouse_id,
                            "支付成功扣减库存并释放锁定库存",
                        )
                    if (
                        detail_row["order_status"] in {"签收后退款", "已完成"}
                        and ctx.rng.random() < COMMENT_RATE
                    ):
                        comment_seq += 1
                        comment_level = (
                            5
                            if comment_seq % 5 in {0, 1}
                            else 4
                            if comment_seq % 5 == 2
                            else 3
                            if comment_seq % 5 == 3
                            else 2
                        )
                        comment_content, sensitive_tag = _pick_comment_bundle(
                            ctx, comment_level
                        )
                        comment_time = detail_row["order_receive_time"] + timedelta(
                            days=(comment_seq % 15) + 1,
                            hours=(comment_seq % 10) + 1,
                        )
                        comment_buffer.append(
                            {
                                "comment_detail_id": comment_seq,
                                "comment_id": 90_000_000 + comment_seq,
                                "order_id": detail_row["order_id"],
                                "order_detail_id": detail_row["order_detail_id"],
                                "user_id": detail_row["user_id"],
                                "shop_id": detail_row["shop_id"],
                                "sku_id": detail_row["sku_id"],
                                "spu_id": detail_row["spu_id"],
                                "category_id": detail_row.get("category_id"),
                                "comment_level": comment_level,
                                "is_anonymous": 1 if comment_seq % 4 == 0 else 0,
                                "is_with_image": 1 if comment_seq % 3 == 0 else 0,
                                "is_with_video": 1 if comment_seq % 10 == 0 else 0,
                                "is_append_comment": 1 if comment_seq % 8 == 0 else 0,
                                "comment_content": comment_content[:2000],
                                "service_score": max(
                                    1,
                                    min(
                                        5,
                                        comment_level
                                        + (1 if comment_seq % 9 == 0 else 0),
                                    ),
                                ),
                                "logistics_score": max(
                                    1,
                                    min(
                                        5,
                                        comment_level
                                        + (1 if comment_seq % 7 == 0 else 0),
                                    ),
                                ),
                                "description_score": comment_level,
                                "sensitive_tag": sensitive_tag,
                                "sentiment": _sentiment_by_level(comment_level),
                                "comment_time": comment_time,
                                "etl_date": comment_time.date(),
                            }
                        )

                for refund_row in refund_buffer[refund_start:]:
                    if refund_row.get("need_return_goods") != 1:
                        continue
                    detail_row = detail_by_id.get(refund_row["order_detail_id"])
                    if detail_row is None:
                        continue
                    refund_time = (
                        refund_row.get("receive_return_time")
                        or refund_row.get("refund_success_time")
                        or refund_row["apply_time"]
                    )
                    warehouse_id = warehouse_by_detail.get(
                        refund_row["order_detail_id"],
                        WAREHOUSE_ID_FALLBACK,
                    )
                    inventory_seq = _append_inventory_change(
                        inventory_buffer,
                        inventory_seq,
                        stock_state,
                        lock_state,
                        detail_row,
                        "入库",
                        "退款",
                        refund_row["refund_no"],
                        refund_time,
                        detail_row["sku_num"],
                        0,
                        warehouse_id,
                        "退款退货回补库存",
                    )

                daily_created += len(detail_rows)

                flush_counts: dict[str, int] = {}
                should_flush = len(detail_buffer) >= ctx.gen.batch_size
                if should_flush:
                    flushed, detail_inserted = _flush_buffer(
                        conn,
                        order_detail_table,
                        detail_buffer,
                        ctx.gen.batch_size,
                        detail_inserted,
                    )
                    if flushed:
                        flush_counts["order_detail"] = flushed
                    flushed, activity_inserted = _flush_buffer(
                        conn,
                        order_activity_table,
                        activity_buffer,
                        ctx.gen.batch_size,
                        activity_inserted,
                    )
                    if flushed:
                        flush_counts["order_activity"] = flushed
                    flushed, coupon_inserted = _flush_buffer(
                        conn,
                        order_coupon_table,
                        coupon_buffer,
                        ctx.gen.batch_size,
                        coupon_inserted,
                    )
                    if flushed:
                        flush_counts["order_coupon"] = flushed
                    flushed, pay_inserted = _flush_buffer(
                        conn,
                        pay_table,
                        pay_buffer,
                        ctx.gen.batch_size,
                        pay_inserted,
                    )
                    if flushed:
                        flush_counts["pay_detail"] = flushed
                    flushed, delivery_inserted = _flush_buffer(
                        conn,
                        delivery_table,
                        delivery_buffer,
                        ctx.gen.batch_size,
                        delivery_inserted,
                    )
                    if flushed:
                        flush_counts["delivery_detail"] = flushed
                    flushed, refund_inserted = _flush_buffer(
                        conn,
                        refund_table,
                        refund_buffer,
                        ctx.gen.batch_size,
                        refund_inserted,
                    )
                    if flushed:
                        flush_counts["refund_detail"] = flushed
                    flushed, refund_pay_inserted = _flush_buffer(
                        conn,
                        refund_pay_table,
                        refund_pay_buffer,
                        ctx.gen.batch_size,
                        refund_pay_inserted,
                    )
                    if flushed:
                        flush_counts["refund_pay_detail"] = flushed
                    flushed, comment_inserted = _flush_buffer(
                        conn,
                        comment_table,
                        comment_buffer,
                        ctx.gen.batch_size,
                        comment_inserted,
                    )
                    if flushed:
                        flush_counts["comment_detail"] = flushed
                    flushed, inventory_inserted = _flush_buffer(
                        conn,
                        inventory_table,
                        inventory_buffer,
                        ctx.gen.batch_size,
                        inventory_inserted,
                    )
                    if flushed:
                        flush_counts["inventory_change"] = flushed
                if flush_counts:
                    logger.info(
                        "batch4 flush etl_date={} order_detail={} activity={} coupon={} pay={} delivery={} refund={} refund_pay={} comment={} inventory={} totals=({},{},{},{},{},{},{},{},{})",
                        current_date.isoformat(),
                        flush_counts.get("order_detail", 0),
                        flush_counts.get("order_activity", 0),
                        flush_counts.get("order_coupon", 0),
                        flush_counts.get("pay_detail", 0),
                        flush_counts.get("delivery_detail", 0),
                        flush_counts.get("refund_detail", 0),
                        flush_counts.get("refund_pay_detail", 0),
                        flush_counts.get("comment_detail", 0),
                        flush_counts.get("inventory_change", 0),
                        detail_inserted,
                        activity_inserted,
                        coupon_inserted,
                        pay_inserted,
                        delivery_inserted,
                        refund_inserted,
                        refund_pay_inserted,
                        comment_inserted,
                        inventory_inserted,
                    )

        final_flush_counts: dict[str, int] = {}
        flushed, detail_inserted = _flush_buffer(
            conn,
            order_detail_table,
            detail_buffer,
            ctx.gen.batch_size,
            detail_inserted,
        )
        if flushed:
            final_flush_counts["order_detail"] = flushed
        flushed, activity_inserted = _flush_buffer(
            conn,
            order_activity_table,
            activity_buffer,
            ctx.gen.batch_size,
            activity_inserted,
        )
        if flushed:
            final_flush_counts["order_activity"] = flushed
        flushed, coupon_inserted = _flush_buffer(
            conn,
            order_coupon_table,
            coupon_buffer,
            ctx.gen.batch_size,
            coupon_inserted,
        )
        if flushed:
            final_flush_counts["order_coupon"] = flushed
        flushed, pay_inserted = _flush_buffer(
            conn,
            pay_table,
            pay_buffer,
            ctx.gen.batch_size,
            pay_inserted,
        )
        if flushed:
            final_flush_counts["pay_detail"] = flushed
        flushed, delivery_inserted = _flush_buffer(
            conn,
            delivery_table,
            delivery_buffer,
            ctx.gen.batch_size,
            delivery_inserted,
        )
        if flushed:
            final_flush_counts["delivery_detail"] = flushed
        flushed, refund_inserted = _flush_buffer(
            conn,
            refund_table,
            refund_buffer,
            ctx.gen.batch_size,
            refund_inserted,
        )
        if flushed:
            final_flush_counts["refund_detail"] = flushed
        flushed, refund_pay_inserted = _flush_buffer(
            conn,
            refund_pay_table,
            refund_pay_buffer,
            ctx.gen.batch_size,
            refund_pay_inserted,
        )
        if flushed:
            final_flush_counts["refund_pay_detail"] = flushed
        flushed, comment_inserted = _flush_buffer(
            conn,
            comment_table,
            comment_buffer,
            ctx.gen.batch_size,
            comment_inserted,
        )
        if flushed:
            final_flush_counts["comment_detail"] = flushed
        flushed, inventory_inserted = _flush_buffer(
            conn,
            inventory_table,
            inventory_buffer,
            ctx.gen.batch_size,
            inventory_inserted,
        )
        if flushed:
            final_flush_counts["inventory_change"] = flushed
        if final_flush_counts:
            logger.info(
                "batch4 final flush order_detail={} activity={} coupon={} pay={} delivery={} refund={} refund_pay={} comment={} inventory={} totals=({},{},{},{},{},{},{},{},{})",
                final_flush_counts.get("order_detail", 0),
                final_flush_counts.get("order_activity", 0),
                final_flush_counts.get("order_coupon", 0),
                final_flush_counts.get("pay_detail", 0),
                final_flush_counts.get("delivery_detail", 0),
                final_flush_counts.get("refund_detail", 0),
                final_flush_counts.get("refund_pay_detail", 0),
                final_flush_counts.get("comment_detail", 0),
                final_flush_counts.get("inventory_change", 0),
                detail_inserted,
                activity_inserted,
                coupon_inserted,
                pay_inserted,
                delivery_inserted,
                refund_inserted,
                refund_pay_inserted,
                comment_inserted,
                inventory_inserted,
            )

    logger.info(
        "Generated batch4 trade facts: order_detail_rows={}, activity_rows={}, coupon_rows={}, pay_rows={}, delivery_rows={}, refund_rows={}, refund_pay_rows={}, comment_rows={}, inventory_rows={}",
        detail_inserted,
        activity_inserted,
        coupon_inserted,
        pay_inserted,
        delivery_inserted,
        refund_inserted,
        refund_pay_inserted,
        comment_inserted,
        inventory_inserted,
    )
