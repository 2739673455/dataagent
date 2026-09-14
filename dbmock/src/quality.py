"""跨表数据质量校验"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Mapping

from sqlalchemy import Table, text

from .settings import RunContext
from .support import doris_unique_key_columns
from .timeline import month_periods

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RealismMetric:
    sql: str
    minimum: float | None
    maximum: float | None
    minimum_sample: int
    unit: str = "ratio"
    blocking: bool = False


REALISM_METRICS = {
    "session_bounce_rate": RealismMetric(
        "SELECT AVG(is_bounce), COUNT(*) FROM dwd_traffic_session_di",
        0.05,
        0.35,
        100,
    ),
    "search_no_result_rate": RealismMetric(
        "SELECT AVG(is_no_result), COUNT(*) FROM dwd_traffic_search_di",
        0.03,
        0.15,
        100,
    ),
    "search_click_rate": RealismMetric(
        """
        SELECT AVG(CASE WHEN c.search_detail_id IS NULL THEN 0 ELSE 1 END), COUNT(*)
        FROM dwd_traffic_search_di s
        LEFT JOIN (
            SELECT DISTINCT search_detail_id FROM dwd_traffic_search_click_di
        ) c ON c.search_detail_id = s.search_detail_id
        """,
        0.35,
        0.85,
        100,
    ),
    "payment_attempt_success_rate": RealismMetric(
        """
        SELECT AVG(after_pay_status = 'SUCCESS'), COUNT(*)
        FROM (
            SELECT after_pay_status,
                   ROW_NUMBER() OVER (
                       PARTITION BY pay_detail_id ORDER BY event_seq_no DESC
                   ) row_no
            FROM dwd_trade_pay_status_event_di
        ) x
        WHERE row_no = 1
        """,
        0.90,
        0.995,
        100,
    ),
    "payment_retry_order_rate": RealismMetric(
        """
        SELECT AVG(attempt_count > 1), COUNT(*)
        FROM (
            SELECT order_id, COUNT(DISTINCT pay_detail_id) attempt_count
            FROM dwd_trade_pay_order_detail_di
            GROUP BY order_id
        ) x
        """,
        0.005,
        0.08,
        100,
    ),
    "refund_order_rate": RealismMetric(
        """
        SELECT COUNT(DISTINCT r.order_id) / NULLIF(COUNT(DISTINCT o.order_id), 0),
               COUNT(DISTINCT o.order_id)
        FROM dwd_trade_order_detail_di o
        LEFT JOIN dwd_trade_refund_detail_di r ON r.order_id = o.order_id
        """,
        0.03,
        0.16,
        100,
    ),
    "refund_rejected_or_cancelled_rate": RealismMetric(
        """
        SELECT AVG(after_refund_status IN ('REJECTED', 'CANCELLED')), COUNT(*)
        FROM (
            SELECT after_refund_status,
                   ROW_NUMBER() OVER (
                       PARTITION BY refund_detail_id ORDER BY event_seq_no DESC
                   ) row_no
            FROM dwd_trade_refund_status_event_di
        ) x
        WHERE row_no = 1
        """,
        0.03,
        0.18,
        30,
    ),
    "negative_comment_rate": RealismMetric(
        """
        SELECT AVG(comment_level <= 2), COUNT(*)
        FROM dwd_service_comment_detail_di
        WHERE comment_type = '初评'
        """,
        0.03,
        0.15,
        30,
    ),
    "comment_text_uniqueness_rate": RealismMetric(
        """
        SELECT COUNT(DISTINCT comment_content) / NULLIF(COUNT(comment_content), 0),
               COUNT(comment_content)
        FROM dwd_service_comment_detail_di
        WHERE comment_type = '初评'
        """,
        0.08,
        1.0,
        30,
    ),
    "refund_pay_failure_rate": RealismMetric(
        """
        SELECT AVG(after_refund_pay_status = 'FAILED'), COUNT(*)
        FROM (
            SELECT after_refund_pay_status,
                   ROW_NUMBER() OVER (
                       PARTITION BY refund_pay_detail_id ORDER BY event_seq_no DESC
                   ) row_no
            FROM dwd_trade_refund_pay_status_event_di
        ) x
        WHERE row_no = 1
        """,
        0.01,
        0.10,
        30,
    ),
    "recent_order_terminal_rate": RealismMetric(
        """
        SELECT AVG(latest_status IN ('COMPLETED', 'CANCELLED')), COUNT(*)
        FROM (
            SELECT o.order_id,
                   MAX_BY(s.after_order_status, s.event_seq_no) latest_status
            FROM dwd_trade_order_detail_di o
            JOIN dwd_trade_order_status_event_di s ON s.order_id = o.order_id
            WHERE o.biz_date >= DATE_SUB(CAST(:as_of_time AS DATE), INTERVAL 3 DAY)
            GROUP BY o.order_id
        ) x
        """,
        0.05,
        0.85,
        30,
    ),
    "stockout_cancel_rate": RealismMetric(
        """
        SELECT AVG(
                   CASE WHEN status_reason_code = 'OUT_OF_STOCK' THEN 1 ELSE 0 END
               ), COUNT(*)
        FROM (
            SELECT status_reason_code,
                   ROW_NUMBER() OVER (
                       PARTITION BY order_id ORDER BY event_seq_no DESC
                   ) row_no
            FROM dwd_trade_order_status_event_di
        ) x
        WHERE row_no = 1
        """,
        0.003,
        0.03,
        100,
    ),
    "purchase_order_event_count": RealismMetric(
        """
        SELECT SUM(change_type = 'PURCHASE_ORDER'), COUNT(*)
        FROM dwd_inventory_change_di
        """,
        1.0,
        None,
        100_000,
        "count",
    ),
    "in_window_registration_rate": RealismMetric(
        """
        SELECT AVG(register_time >= :start_time), COUNT(*)
        FROM dim_user_info_zip
        WHERE user_id <> 0 AND is_current = 1
        """,
        0.40,
        0.70,
        100,
    ),
    "average_user_tag_count": RealismMetric(
        """
        SELECT AVG(tag_count), COUNT(*)
        FROM (
            SELECT user_id, COUNT(*) tag_count
            FROM bridge_user_tag_relation_zip
            WHERE is_current = 1 AND user_id <> 0
            GROUP BY user_id
        ) x
        """,
        1.0,
        4.0,
        20,
        "count",
    ),
    "self_operated_order_share": RealismMetric(
        """
        SELECT AVG(s.is_self_operated), COUNT(*)
        FROM dwd_trade_order_detail_di o
        JOIN dim_shop_info_zip s
          ON s.shop_sk = o.shop_sk AND s.shop_id = o.shop_id
        """,
        0.08,
        0.55,
        100,
    ),
    "cart_session_order_conversion_rate": RealismMetric(
        """
        SELECT COUNT(DISTINCT o.source_session_id)
                   / NULLIF(COUNT(DISTINCT c.session_id), 0),
               COUNT(DISTINCT c.session_id)
        FROM dwd_interaction_cart_event_di c
        LEFT JOIN dwd_trade_order_detail_di o
          ON o.source_session_id = c.session_id
        WHERE c.cart_event_type = '加入'
        """,
        0.20,
        0.55,
        30,
    ),
    "cart_mutation_event_rate": RealismMetric(
        """
        SELECT AVG(cart_event_type <> '加入'), COUNT(*)
        FROM dwd_interaction_cart_event_di
        """,
        0.05,
        0.30,
        100,
    ),
    "favorite_cancel_event_rate": RealismMetric(
        """
        SELECT AVG(favor_event_type = '取消收藏'), COUNT(*)
        FROM dwd_interaction_favor_event_di
        """,
        0.05,
        0.25,
        30,
    ),
    "order_total_discount_rate": RealismMetric(
        """
        SELECT SUM(
                   list_amount - sale_amount
                   + activity_discount_amount
                   + coupon_discount_amount
                   + points_discount_amount
               ) / NULLIF(SUM(list_amount), 0),
               COUNT(*)
        FROM dwd_trade_order_detail_di
        """,
        0.03,
        0.12,
        100,
    ),
    "zero_available_inventory_snapshot_rate": RealismMetric(
        """
        SELECT AVG(available_qty = 0), COUNT(*)
        FROM dwd_inventory_daily_snapshot_df
        """,
        0.0005,
        0.03,
        1_000,
    ),
    "in_transit_inventory_snapshot_rate": RealismMetric(
        """
        SELECT AVG(in_transit_qty > 0), COUNT(*)
        FROM dwd_inventory_daily_snapshot_df
        """,
        0.001,
        0.05,
        1_000,
    ),
    "monthly_conversion_variation": RealismMetric(
        """
        SELECT STDDEV_POP(conversion_rate) / NULLIF(AVG(conversion_rate), 0),
               COUNT(*)
        FROM (
            SELECT s.month_key,
                   COALESCE(o.order_sessions, 0) / s.session_count conversion_rate
            FROM (
                SELECT DATE_FORMAT(biz_date, '%Y-%m') month_key,
                       COUNT(*) session_count
                FROM dwd_traffic_session_di
                GROUP BY DATE_FORMAT(biz_date, '%Y-%m')
            ) s
            LEFT JOIN (
                SELECT DATE_FORMAT(biz_date, '%Y-%m') month_key,
                       COUNT(DISTINCT source_session_id) order_sessions
                FROM dwd_trade_order_detail_di
                GROUP BY DATE_FORMAT(biz_date, '%Y-%m')
            ) o ON o.month_key = s.month_key
        ) x
        """,
        0.03,
        0.45,
        12,
    ),
    "page_type_dwell_time_variation": RealismMetric(
        """
        SELECT STDDEV_POP(avg_duration) / NULLIF(AVG(avg_duration), 0), COUNT(*)
        FROM (
            SELECT page_id, AVG(stay_duration_sec) avg_duration
            FROM dwd_traffic_page_view_di
            GROUP BY page_id
        ) x
        """,
        0.08,
        1.0,
        5,
    ),
    "dormant_or_lost_user_share": RealismMetric(
        """
        SELECT AVG(user_status IN ('沉默', '流失')), COUNT(*)
        FROM dim_user_info_zip
        WHERE user_id <> 0 AND is_current = 1
        """,
        0.03,
        0.40,
        100,
    ),
}

GENERATION_FIELD_LINEAGE = {
    "dwd_traffic_*": {
        "classification": "synthetic",
        "model": "session_path_model",
    },
    "dwd_trade_*": {
        "classification": "synthetic",
        "model": "commerce_lifecycle_model",
    },
    "dwd_inventory_*": {
        "classification": "synthetic",
        "model": "inventory_conservation_model",
    },
    "dim_spu_info_zip.weight_kg": {
        "classification": "normalized",
        "source": "catalog.lineage.origin_weight",
        "missing_policy": "null",
    },
    "dwd_product_sku_price_change_di.new_cost_price": {
        "classification": "synthetic",
        "model": "sale_price * deterministic_margin_ratio",
        "parameters": {"margin_ratio_range": [0.58, 0.80]},
    },
    "dim_spu_info_zip.on_shelf_time": {
        "classification": "synthetic",
        "model": "deterministic_listing_lifecycle",
    },
    "dim_warehouse_info_zip.address": {
        "classification": "synthetic",
        "model": "real_administrative_region_with_masked_logistics_park",
    },
}

CHECKS: dict[str, str] = {
    "日期节假日配置": """
        SELECT
            CASE
                WHEN DATEDIFF(MAX(full_date), MIN(full_date)) >= 365
                 AND SUM(is_holiday) = 0
                THEN 1
                ELSE 0
            END
            + SUM(
                CASE
                    WHEN is_holiday = 1
                     AND (holiday_name IS NULL OR is_workday <> 0)
                    THEN 1
                    WHEN is_holiday = 0 AND holiday_name IS NOT NULL
                    THEN 1
                    ELSE 0
                END
            )
        FROM dim_date
    """,
    "订单活动优惠分摊": """
        SELECT COUNT(*)
        FROM dwd_trade_order_detail_di d
        LEFT JOIN (
            SELECT order_detail_id, SUM(promotion_discount_amount) amount
            FROM dwd_trade_order_detail_activity_di
            GROUP BY order_detail_id
        ) a ON a.order_detail_id = d.order_detail_id
        WHERE d.activity_discount_amount <> COALESCE(a.amount, 0)
    """,
    "订单优惠券分摊": """
        SELECT COUNT(*)
        FROM dwd_trade_order_detail_di d
        LEFT JOIN (
            SELECT order_detail_id, SUM(coupon_discount_amount) amount
            FROM dwd_trade_order_detail_coupon_di
            GROUP BY order_detail_id
        ) c ON c.order_detail_id = d.order_detail_id
        WHERE d.coupon_discount_amount <> COALESCE(c.amount, 0)
    """,
    "支付金额分摊": """
        SELECT COUNT(*)
        FROM dwd_trade_pay_detail_di p
        LEFT JOIN (
            SELECT pay_detail_id, SUM(allocated_pay_amount) amount
            FROM dwd_trade_pay_order_detail_di
            GROUP BY pay_detail_id
        ) a ON a.pay_detail_id = p.pay_detail_id
        WHERE p.requested_pay_amount <> COALESCE(a.amount, 0)
    """,
    "包裹重量和运费分摊": """
        SELECT COUNT(*)
        FROM dwd_trade_delivery_di d
        LEFT JOIN (
            SELECT delivery_id,
                   SUM(allocated_weight_kg) weight,
                   SUM(allocated_freight_amount) freight
            FROM dwd_trade_delivery_item_di
            GROUP BY delivery_id
        ) i ON i.delivery_id = d.delivery_id
        WHERE d.package_weight_kg <> COALESCE(i.weight, 0)
           OR d.package_freight_amount <> COALESCE(i.freight, 0)
    """,
    "事实业务日期": """
        SELECT SUM(violations) FROM (
            SELECT COUNT(*) violations FROM dwd_trade_order_detail_di
             WHERE biz_date <> DATE(order_create_time)
                OR order_date_key <> DATE_FORMAT(order_create_time, '%Y%m%d') + 0
            UNION ALL
            SELECT COUNT(*) FROM dwd_trade_pay_detail_di
             WHERE biz_date <> DATE(pay_request_time)
                OR pay_date_key <> DATE_FORMAT(pay_request_time, '%Y%m%d') + 0
            UNION ALL
            SELECT COUNT(*) FROM dwd_inventory_change_di
             WHERE biz_date <> DATE(event_time)
                OR event_date_key <> DATE_FORMAT(event_time, '%Y%m%d') + 0
        ) q
    """,
    "业务状态时间严格递增": """
        SELECT SUM(violations) FROM (
            SELECT COUNT(*) violations FROM (
                SELECT event_time,
                       LAG(event_time) OVER (
                           PARTITION BY order_id ORDER BY event_seq_no
                       ) previous_time
                FROM dwd_trade_order_status_event_di
            ) x
            WHERE previous_time IS NOT NULL AND event_time <= previous_time
            UNION ALL
            SELECT COUNT(*) FROM (
                SELECT event_time,
                       LAG(event_time) OVER (
                           PARTITION BY pay_detail_id ORDER BY event_seq_no
                       ) previous_time
                FROM dwd_trade_pay_status_event_di
            ) x
            WHERE previous_time IS NOT NULL AND event_time <= previous_time
            UNION ALL
            SELECT COUNT(*) FROM (
                SELECT event_time,
                       LAG(event_time) OVER (
                           PARTITION BY delivery_id ORDER BY event_seq_no
                       ) previous_time
                FROM dwd_trade_delivery_status_event_di
            ) x
            WHERE previous_time IS NOT NULL AND event_time <= previous_time
            UNION ALL
            SELECT COUNT(*) FROM (
                SELECT event_time,
                       LAG(event_time) OVER (
                           PARTITION BY refund_detail_id ORDER BY event_seq_no
                       ) previous_time
                FROM dwd_trade_refund_status_event_di
            ) x
            WHERE previous_time IS NOT NULL AND event_time <= previous_time
            UNION ALL
            SELECT COUNT(*) FROM (
                SELECT event_time,
                       LAG(event_time) OVER (
                           PARTITION BY refund_pay_detail_id ORDER BY event_seq_no
                       ) previous_time
                FROM dwd_trade_refund_pay_status_event_di
            ) x
            WHERE previous_time IS NOT NULL AND event_time <= previous_time
        ) q
    """,
    "订单维度时点命中": """
        SELECT COUNT(*)
        FROM dwd_trade_order_detail_di f
        LEFT JOIN dim_user_info_zip u
          ON u.user_sk = f.user_sk
         AND u.user_id = f.user_id
         AND f.order_create_time >= u.effective_start_time
         AND f.order_create_time < u.effective_end_time
        LEFT JOIN dim_shop_info_zip sh
          ON sh.shop_sk = f.shop_sk
         AND sh.shop_id = f.shop_id
         AND f.order_create_time >= sh.effective_start_time
         AND f.order_create_time < sh.effective_end_time
        LEFT JOIN dim_sku_info_zip sk
          ON sk.sku_sk = f.sku_sk
         AND sk.sku_id = f.sku_id
         AND f.order_create_time >= sk.effective_start_time
         AND f.order_create_time < sk.effective_end_time
        LEFT JOIN dim_spu_info_zip sp
          ON sp.spu_sk = f.spu_sk
         AND sp.spu_id = f.spu_id
         AND f.order_create_time >= sp.effective_start_time
         AND f.order_create_time < sp.effective_end_time
        LEFT JOIN dim_category_info_zip c
          ON c.category_sk = f.category_sk
         AND c.category_id = f.category_id
         AND f.order_create_time >= c.effective_start_time
         AND f.order_create_time < c.effective_end_time
        WHERE u.user_sk IS NULL OR sh.shop_sk IS NULL OR sk.sku_sk IS NULL
           OR sp.spu_sk IS NULL OR c.category_sk IS NULL
    """,
    "SKU价格事件衔接": """
        SELECT COUNT(*) FROM (
            SELECT previous_list_price,
                   previous_sale_price,
                   previous_cost_price,
                   LAG(new_list_price) OVER (
                       PARTITION BY sku_id ORDER BY price_effective_time
                   ) last_list_price,
                   LAG(new_sale_price) OVER (
                       PARTITION BY sku_id ORDER BY price_effective_time
                   ) last_sale_price,
                   LAG(new_cost_price) OVER (
                       PARTITION BY sku_id ORDER BY price_effective_time
                   ) last_cost_price,
                   ROW_NUMBER() OVER (
                       PARTITION BY sku_id ORDER BY price_effective_time
                   ) row_no
            FROM dwd_product_sku_price_change_di
        ) x
        WHERE (row_no = 1 AND (
                   previous_list_price IS NOT NULL
                OR previous_sale_price IS NOT NULL
                OR previous_cost_price IS NOT NULL
              ))
           OR (row_no > 1 AND (
                   NOT (previous_list_price <=> last_list_price)
                OR NOT (previous_sale_price <=> last_sale_price)
                OR NOT (previous_cost_price <=> last_cost_price)
              ))
    """,
    "退款不超过实付": """
        SELECT COUNT(*) FROM (
            SELECT p.order_detail_id,
                   SUM(
                       CASE WHEN EXISTS (
                           SELECT 1 FROM dwd_trade_pay_status_event_di ps
                           WHERE ps.pay_detail_id = p.pay_detail_id
                             AND ps.after_pay_status = 'SUCCESS'
                       ) THEN p.allocated_pay_amount ELSE 0 END
                   ) paid_amount,
                   COALESCE(r.refund_amount, 0) refund_amount
            FROM dwd_trade_pay_order_detail_di p
            LEFT JOIN (
                SELECT rp.order_detail_id, SUM(rp.refund_amount) refund_amount
                FROM dwd_trade_refund_pay_detail_di rp
                WHERE EXISTS (
                    SELECT 1 FROM dwd_trade_refund_pay_status_event_di s
                    WHERE s.refund_pay_detail_id = rp.refund_pay_detail_id
                      AND s.after_refund_pay_status = 'SUCCESS'
                )
                GROUP BY rp.order_detail_id
            ) r ON r.order_detail_id = p.order_detail_id
            GROUP BY p.order_detail_id, r.refund_amount
        ) x
        WHERE refund_amount > paid_amount
    """,
    "库存事件首尾衔接": """
        SELECT COUNT(*) FROM (
            SELECT before_on_hand_qty,
                   before_reserved_qty,
                   before_in_transit_qty,
                   LAG(after_on_hand_qty) OVER (
                       PARTITION BY warehouse_id, sku_id
                       ORDER BY event_time, inventory_change_id
                   ) previous_on_hand,
                   LAG(after_reserved_qty) OVER (
                       PARTITION BY warehouse_id, sku_id
                       ORDER BY event_time, inventory_change_id
                   ) previous_reserved,
                   LAG(after_in_transit_qty) OVER (
                       PARTITION BY warehouse_id, sku_id
                       ORDER BY event_time, inventory_change_id
                   ) previous_in_transit
            FROM dwd_inventory_change_di
        ) x
        WHERE previous_on_hand IS NOT NULL
          AND (before_on_hand_qty <> previous_on_hand
               OR before_reserved_qty <> previous_reserved
               OR before_in_transit_qty <> previous_in_transit)
    """,
    "库存期末快照": """
        SELECT COUNT(*)
        FROM dwd_inventory_daily_snapshot_df s
        JOIN (
            SELECT warehouse_id, sku_id, biz_date,
                   after_on_hand_qty last_on_hand,
                   after_reserved_qty last_reserved,
                   after_in_transit_qty last_in_transit
            FROM (
                SELECT warehouse_id, sku_id, biz_date,
                       after_on_hand_qty, after_reserved_qty, after_in_transit_qty,
                       ROW_NUMBER() OVER (
                           PARTITION BY warehouse_id, sku_id, biz_date
                           ORDER BY event_time DESC, inventory_change_id DESC
                       ) row_no
                FROM dwd_inventory_change_di
            ) ranked
            WHERE row_no = 1
        ) e ON e.warehouse_id = s.warehouse_id
           AND e.sku_id = s.sku_id
           AND e.biz_date = s.biz_date
        WHERE s.on_hand_qty <> e.last_on_hand
           OR s.reserved_qty <> e.last_reserved
           OR s.in_transit_qty <> e.last_in_transit
    """,
    "库存快照覆盖范围": """
        -- 快照 UNIQUE KEY 为 (snapshot_date_key, warehouse_sk, sku_sk)。
        -- 缺失数 = 应有数 - 合法数；多余数单独累计，避免相互抵消。
        WITH listings AS (
            SELECT warehouse_sk, sku_sk, MIN(biz_date) listing_date
            FROM dwd_inventory_change_di
            WHERE change_type = 'INITIAL_STOCK'
            GROUP BY warehouse_sk, sku_sk
        ), listing_counts AS (
            SELECT listing_date, COUNT(*) pair_count
            FROM listings
            GROUP BY listing_date
        ), expected_count AS (
            SELECT COALESCE(SUM(b.pair_count), 0) total
            FROM listing_counts b
            JOIN dim_date d ON d.full_date >= b.listing_date
        ), actual_counts AS (
            SELECT COUNT(*) total,
                   COALESCE(SUM(CASE
                       WHEN b.sku_sk IS NOT NULL
                        AND d.full_date >= b.listing_date THEN 1
                       ELSE 0
                   END), 0) valid_count
            FROM dwd_inventory_daily_snapshot_df s
            LEFT JOIN listings b
              ON b.warehouse_sk = s.warehouse_sk
             AND b.sku_sk = s.sku_sk
            LEFT JOIN dim_date d ON d.date_key = s.snapshot_date_key
        )
        SELECT e.total - a.valid_count + (a.total - a.valid_count)
        FROM expected_count e CROSS JOIN actual_counts a
    """,
    "会话事件汇总": """
        SELECT COUNT(*)
        FROM dwd_traffic_session_di s
        LEFT JOIN (
            SELECT session_id, COUNT(*) count_value
            FROM dwd_traffic_page_view_di GROUP BY session_id
        ) p ON p.session_id = s.session_id
        LEFT JOIN (
            SELECT session_id, COUNT(*) count_value
            FROM dwd_traffic_search_di GROUP BY session_id
        ) q ON q.session_id = s.session_id
        WHERE s.page_view_count <> COALESCE(p.count_value, 0)
           OR s.search_count <> COALESCE(q.count_value, 0)
    """,
    "订单行为链路": """
        SELECT COUNT(*)
        FROM dwd_trade_order_detail_di d
        LEFT JOIN dwd_traffic_session_di s
          ON s.session_id = d.source_session_id
         AND s.user_id = d.user_id
        LEFT JOIN dwd_interaction_cart_event_di c
          ON c.session_id = d.source_session_id
         AND c.user_id = d.user_id
         AND c.sku_id = d.sku_id
         AND c.cart_event_type = '加入'
         AND c.event_time <= d.order_create_time
        WHERE s.session_id IS NULL OR c.cart_event_id IS NULL
    """,
    "订单成本完整性": """
        SELECT COUNT(*)
        FROM dwd_trade_order_detail_di
        WHERE cost_amount IS NULL OR cost_amount <= 0
    """,
    "库存数量合法性": """
        SELECT SUM(violations) FROM (
            SELECT COUNT(*) violations
            FROM dwd_inventory_change_di
            WHERE after_on_hand_qty < 0
               OR after_reserved_qty < 0
               OR after_reserved_qty > after_on_hand_qty
               OR after_in_transit_qty < 0
            UNION ALL
            SELECT COUNT(*)
            FROM dwd_inventory_daily_snapshot_df
            WHERE on_hand_qty < 0
               OR reserved_qty < 0
               OR in_transit_qty < 0
               OR available_qty <> on_hand_qty - reserved_qty
        ) q
    """,
    "履约出库仓一致": """
        SELECT COUNT(*)
        FROM dwd_trade_delivery_item_di i
        JOIN dwd_trade_delivery_di d ON d.delivery_id = i.delivery_id
        JOIN (
            SELECT DISTINCT delivery_id
            FROM dwd_trade_delivery_status_event_di
            WHERE after_delivery_status IN ('SHIPPED', 'SIGNED')
        ) shipped ON shipped.delivery_id = i.delivery_id
        LEFT JOIN dwd_inventory_change_di c
          ON c.biz_type = 'DELIVERY'
         AND c.biz_id = CAST(i.order_id AS STRING)
         AND c.sku_id = i.sku_id
        WHERE d.delivery_direction = '正向'
          AND (c.inventory_change_id IS NULL OR c.warehouse_id <> d.warehouse_id)
    """,
    "会话事件时间边界": """
        SELECT SUM(violations) FROM (
            SELECT COUNT(*) violations
            FROM dwd_traffic_session_di
            WHERE session_end_time < session_start_time
            UNION ALL
            SELECT COUNT(*)
            FROM dwd_traffic_page_view_di e
            JOIN dwd_traffic_session_di s ON s.session_id = e.session_id
            WHERE e.event_time < s.session_start_time
               OR e.event_time > s.session_end_time
            UNION ALL
            SELECT COUNT(*)
            FROM dwd_traffic_search_di e
            JOIN dwd_traffic_session_di s ON s.session_id = e.session_id
            WHERE e.event_time < s.session_start_time
               OR e.event_time > s.session_end_time
            UNION ALL
            SELECT COUNT(*)
            FROM dwd_traffic_search_click_di e
            JOIN dwd_traffic_session_di s ON s.session_id = e.session_id
            WHERE e.event_time < s.session_start_time
               OR e.event_time > s.session_end_time
            UNION ALL
            SELECT COUNT(*)
            FROM dwd_interaction_cart_event_di e
            JOIN dwd_traffic_session_di s ON s.session_id = e.session_id
            WHERE e.event_time < s.session_start_time
               OR e.event_time > s.session_end_time
            UNION ALL
            SELECT COUNT(*)
            FROM dwd_interaction_favor_event_di e
            JOIN dwd_traffic_session_di s ON s.session_id = e.session_id
            WHERE e.event_time < s.session_start_time
               OR e.event_time > s.session_end_time
            UNION ALL
            SELECT COUNT(*)
            FROM dwd_trade_order_detail_di e
            JOIN dwd_traffic_session_di s
              ON s.session_id = e.source_session_id
            WHERE e.order_create_time < s.session_start_time
               OR e.order_create_time > s.session_end_time
        ) q
    """,
    "订单金额算术": """
        SELECT COUNT(*)
        FROM dwd_trade_order_detail_di
        WHERE receivable_amount < 0
           OR receivable_amount <>
              sale_amount
              - activity_discount_amount
              - coupon_discount_amount
              - points_discount_amount
              + freight_amount
              + tax_amount
    """,
    "缺货取消库存证据": """
        WITH latest_status AS (
            SELECT order_id, after_order_status, status_reason_code
            FROM (
                SELECT order_id,
                       after_order_status,
                       status_reason_code,
                       ROW_NUMBER() OVER (
                           PARTITION BY order_id ORDER BY event_seq_no DESC
                       ) row_no
                FROM dwd_trade_order_status_event_di
            ) x
            WHERE row_no = 1
        ), inventory_evidence AS (
            SELECT biz_id,
                   SUM(change_type = 'ALLOCATION_FAILED') failed_count,
                   SUM(change_type = 'SALE_RESERVE') reserve_count
            FROM dwd_inventory_change_di
            WHERE biz_type = 'ORDER'
            GROUP BY biz_id
        )
        SELECT COUNT(*)
        FROM latest_status s
        LEFT JOIN inventory_evidence i ON i.biz_id = CAST(s.order_id AS STRING)
        WHERE s.after_order_status = 'CANCELLED'
          AND s.status_reason_code = 'OUT_OF_STOCK'
          AND (COALESCE(i.failed_count, 0) = 0 OR COALESCE(i.reserve_count, 0) > 0)
    """,
    "已发货订单配送数量": """
        WITH latest_status AS (
            SELECT order_id, after_order_status
            FROM (
                SELECT order_id,
                       after_order_status,
                       ROW_NUMBER() OVER (
                           PARTITION BY order_id ORDER BY event_seq_no DESC
                       ) row_no
                FROM dwd_trade_order_status_event_di
            ) x
            WHERE row_no = 1
        ), delivered AS (
            SELECT i.order_detail_id, SUM(i.delivery_sku_qty) delivered_qty
            FROM dwd_trade_delivery_item_di i
            JOIN dwd_trade_delivery_di d ON d.delivery_id = i.delivery_id
            WHERE d.delivery_direction = '正向'
            GROUP BY i.order_detail_id
        )
        SELECT COUNT(*)
        FROM dwd_trade_order_detail_di o
        JOIN latest_status s ON s.order_id = o.order_id
        LEFT JOIN delivered d ON d.order_detail_id = o.order_detail_id
        WHERE s.after_order_status IN ('SHIPPED', 'COMPLETED')
          AND o.sku_qty <> COALESCE(d.delivered_qty, 0)
    """,
    "优惠券使用时间": """
        WITH used AS (
            SELECT user_coupon_id, MAX(event_time) used_time
            FROM dwd_marketing_user_coupon_event_di
            WHERE after_coupon_status = 'USED'
            GROUP BY user_coupon_id
        )
        SELECT COUNT(*)
        FROM dwd_trade_order_detail_coupon_di c
        JOIN used u ON u.user_coupon_id = c.user_coupon_id
        WHERE c.coupon_use_time <> u.used_time
    """,
    "用户生命周期最近活跃": """
        WITH cutoff AS (
            SELECT MAX(full_date) cutoff_date FROM dim_date
        ), activity AS (
            SELECT user_id, MAX(session_end_time) last_active_time
            FROM dwd_traffic_session_di
            WHERE user_id IS NOT NULL
            GROUP BY user_id
        )
        SELECT COUNT(*)
        FROM dim_user_info_zip u
        CROSS JOIN cutoff c
        LEFT JOIN activity a ON a.user_id = u.user_id
        WHERE u.user_id <> 0
          AND u.is_current = 1
          AND (
              (u.user_status IN ('正常', '召回') AND DATEDIFF(
                   c.cutoff_date,
                   DATE(COALESCE(a.last_active_time, u.register_time))
              ) >= 90)
              OR (u.user_status = '沉默' AND DATEDIFF(
                   c.cutoff_date,
                   DATE(COALESCE(a.last_active_time, u.register_time))
              ) < 60)
              OR (u.user_status = '流失' AND DATEDIFF(
                   c.cutoff_date,
                   DATE(COALESCE(a.last_active_time, u.register_time))
              ) < 180)
          )
    """,
}

SCD_TABLES: dict[str, tuple[str, ...]] = {
    "dim_geo_region_zip": ("region_code",),
    "dim_user_info_zip": ("user_id",),
    "dim_seller_info_zip": ("seller_id",),
    "dim_shop_info_zip": ("shop_id",),
    "dim_category_info_zip": ("category_id",),
    "dim_warehouse_info_zip": ("warehouse_id",),
    "dim_spu_info_zip": ("spu_id",),
    "dim_sku_info_zip": ("sku_id",),
}

RELATION_SCD_TABLES: dict[str, tuple[str, ...]] = {
    "bridge_user_tag_relation_zip": ("user_id", "user_tag_sk"),
}

UNKNOWN_MEMBER_TABLES = (
    "dim_channel_info",
    "dim_page_info",
    "dim_geo_region_zip",
    "dim_user_info_zip",
    "dim_user_tag_info",
    "dim_seller_info_zip",
    "dim_shop_info_zip",
    "dim_category_info_zip",
    "dim_brand_info",
    "dim_payment_type",
    "dim_logistics_company",
    "dim_warehouse_info_zip",
    "dim_spu_info_zip",
    "dim_sku_info_zip",
    "dim_promotion_rule_version",
    "dim_coupon_template_version",
)

OPTIONAL_EMPTY_TABLES: set[str] = set()

SMOKE_OPTIONAL_EMPTY_TABLES = {
    "dwd_service_comment_detail_di",
    "dwd_trade_refund_detail_di",
    "dwd_trade_refund_pay_detail_di",
    "dwd_trade_refund_pay_status_event_di",
    "dwd_trade_refund_status_event_di",
}

FACT_TIME_COLUMNS = {
    "dwd_interaction_cart_event_di": "event_time",
    "dwd_interaction_favor_event_di": "event_time",
    "dwd_inventory_change_di": "event_time",
    "dwd_inventory_daily_snapshot_df": "snapshot_time",
    "dwd_marketing_user_coupon_event_di": "event_time",
    "dwd_product_sku_price_change_di": "price_effective_time",
    "dwd_service_comment_detail_di": "comment_time",
    "dwd_trade_delivery_di": "delivery_create_time",
    "dwd_trade_delivery_item_di": "delivery_create_time",
    "dwd_trade_delivery_status_event_di": "event_time",
    "dwd_trade_order_detail_activity_di": "order_create_time",
    "dwd_trade_order_detail_coupon_di": "coupon_use_time",
    "dwd_trade_order_detail_di": "order_create_time",
    "dwd_trade_order_status_event_di": "event_time",
    "dwd_trade_pay_detail_di": "pay_request_time",
    "dwd_trade_pay_order_detail_di": "pay_request_time",
    "dwd_trade_pay_status_event_di": "event_time",
    "dwd_trade_refund_detail_di": "apply_time",
    "dwd_trade_refund_pay_detail_di": "refund_pay_request_time",
    "dwd_trade_refund_pay_status_event_di": "event_time",
    "dwd_trade_refund_status_event_di": "event_time",
    "dwd_traffic_page_view_di": "event_time",
    "dwd_traffic_search_click_di": "event_time",
    "dwd_traffic_search_di": "event_time",
    "dwd_traffic_session_di": "session_end_time",
}


def _collect_realism_metrics(
    ctx: RunContext,
    conn,
) -> tuple[dict, list[str], list[str]]:
    results: dict[str, dict] = {}
    failures: list[str] = []
    warnings: list[str] = []
    parameters = {
        "start_time": datetime.combine(ctx.gen.start_date, datetime.min.time()),
        "as_of_time": ctx.as_of_time,
    }
    for name, metric in REALISM_METRICS.items():
        value, sample_size = conn.execute(text(metric.sql), parameters).one()
        sample = int(sample_size or 0)
        numeric_value = float(value) if value is not None else None
        status = "pass"
        if sample < metric.minimum_sample or numeric_value is None:
            status = "observe"
        elif metric.minimum is not None and numeric_value < metric.minimum:
            status = "fail"
        elif metric.maximum is not None and numeric_value > metric.maximum:
            status = "fail"
        results[name] = {
            "value": numeric_value,
            "unit": metric.unit,
            "sample_size": sample,
            "blocking_sample_size": metric.minimum_sample,
            "minimum": metric.minimum,
            "maximum": metric.maximum,
            "status": status,
            "blocking": metric.blocking,
        }
        if status == "fail":
            message = (
                f"真实性指标 {name} 超出阈值: value={numeric_value} "
                f"range=[{metric.minimum}, {metric.maximum}] sample={sample}"
            )
            (failures if metric.blocking else warnings).append(message)
    return results, failures, warnings


def _write_quality_report(
    ctx: RunContext,
    counts: dict[str, int],
    realism_metrics: dict,
    failures: list[str],
    warnings: list[str],
) -> Path:
    manifest_path = ctx.gen.data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": (
            "failed"
            if failures
            else "passed_with_warnings"
            if warnings
            else "passed"
        ),
        "run": {
            "run_id": ctx.run_id,
            "as_of_time": ctx.as_of_time.isoformat(),
            "data_start_date": ctx.gen.start_date.isoformat(),
            "data_end_date": ctx.gen.end_date.isoformat(),
            "seed": ctx.gen.seed,
            "is_smoke": ctx.gen.is_smoke,
        },
        "catalog": {
            "counts": manifest.get("counts", {}),
            "selection_quality": {
                key: manifest.get("selection", {}).get(key)
                for key in (
                    "rejected_products",
                    "rejected_records",
                    "rejection_reason_distribution",
                    "removed_ui_artifact_specs",
                    "derived_single_sku_specs",
                    "self_operated_spu_share",
                    "largest_brand_spu_share",
                    "largest_store_spu_share",
                )
            },
            "field_lineage": manifest.get("lineage", {}).get("field_lineage", {}),
            "validation_metrics": {
                "core_field_completeness_rate": 1.0,
                "ui_artifact_hit_count": 0,
                "category_price_violation_count": 0,
                "field_lineage_coverage_rate": 1.0,
            },
        },
        "generation_field_lineage": GENERATION_FIELD_LINEAGE,
        "realism_metrics": realism_metrics,
        "table_counts": counts,
        "failures": failures,
        "warnings": warnings,
    }
    path = ctx.gen.data_dir / "quality_report.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def _generation_completeness_failures(
    ctx: RunContext,
    conn,
    counts: Mapping[str, int],
) -> list[str]:
    failures: list[str] = []
    expected_counts = {
        "dim_date": (ctx.gen.end_date - ctx.gen.start_date).days + 1,
        "dwd_traffic_page_view_di": ctx.gen.page_view_count,
        "dwd_traffic_search_di": ctx.gen.search_count,
        "dwd_trade_order_detail_di": ctx.gen.order_detail_count,
    }
    for table_name, expected in expected_counts.items():
        actual = counts.get(table_name, 0)
        if actual != expected:
            failures.append(
                f"{table_name} 生成数量异常: expected={expected} actual={actual}"
            )

    date_start, date_end = conn.execute(
        text("SELECT MIN(full_date), MAX(full_date) FROM dim_date")
    ).one()
    if date_start != ctx.gen.start_date or date_end != ctx.gen.end_date:
        failures.append(
            "日期维度范围异常: "
            f"expected=[{ctx.gen.start_date}, {ctx.gen.end_date}] "
            f"actual=[{date_start}, {date_end}]"
        )

    expected_months = {
        period.key
        for period in month_periods(ctx.gen.start_date, ctx.gen.end_date)
    }
    for table_name in (
        "dwd_traffic_page_view_di",
        "dwd_traffic_search_di",
        "dwd_trade_order_detail_di",
    ):
        if expected_counts[table_name] < len(expected_months):
            continue
        actual_months = {
            str(row[0])
            for row in conn.execute(
                text(
                    f"SELECT DISTINCT DATE_FORMAT(biz_date, '%Y-%m') "
                    f"FROM `{table_name}`"
                )
            )
        }
        if actual_months != expected_months:
            failures.append(
                f"{table_name} 月份覆盖异常: "
                f"missing={sorted(expected_months - actual_months)} "
                f"unexpected={sorted(actual_months - expected_months)}"
            )
    return failures


def validate_database(ctx: RunContext, tables: Mapping[str, Table]) -> None:
    failures: list[str] = []
    counts: dict[str, int] = {}
    earliest_load_time = None
    optional_empty_tables = OPTIONAL_EMPTY_TABLES | (
        SMOKE_OPTIONAL_EMPTY_TABLES if ctx.gen.is_smoke else set()
    )
    realism_metrics: dict = {}
    warnings: list[str] = []
    with ctx.engine.connect() as conn:
        for table_name in sorted(tables):
            table = tables[table_name]
            count = int(
                conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar_one()
            )
            counts[table_name] = count
            if count == 0 and table_name not in optional_empty_tables:
                failures.append(f"空表: {table_name}")
            if count == 0 or "dw_load_time" not in table.c:
                continue
            load_range = conn.execute(
                text(
                    f"SELECT MIN(dw_load_time), MAX(dw_load_time), "
                    f"SUM(dw_load_time > :as_of_time) FROM `{table_name}`"
                ),
                {"as_of_time": ctx.as_of_time},
            ).one()
            table_earliest = load_range[0]
            if table_earliest is not None and (
                earliest_load_time is None or table_earliest < earliest_load_time
            ):
                earliest_load_time = table_earliest
            if int(load_range[2] or 0):
                failures.append(f"{table_name} 存在晚于任务执行时刻的入仓时间")
            if "biz_date" in table.c:
                before_business_date = int(
                    conn.execute(
                        text(
                            f"SELECT COUNT(*) FROM `{table_name}` "
                            "WHERE DATE(dw_load_time) < biz_date"
                        )
                    ).scalar_one()
                )
                if before_business_date:
                    failures.append(
                        f"{table_name} 入仓时间早于业务日期: "
                        f"{before_business_date} 条"
                    )
                batch_month_mismatch = int(
                    conn.execute(
                        text(
                            f"SELECT COUNT(*) FROM `{table_name}` "
                            "WHERE RIGHT(load_batch_id, 7) "
                            "<> DATE_FORMAT(biz_date, '%Y-%m')"
                        )
                    ).scalar_one()
                )
                if batch_month_mismatch:
                    failures.append(
                        f"{table_name} 业务月份与装载批次不一致: "
                        f"{batch_month_mismatch} 条"
                    )
            fact_time_column = FACT_TIME_COLUMNS.get(table_name)
            if fact_time_column is not None:
                invalid_fact_time = conn.execute(
                    text(
                        f"SELECT SUM(`{fact_time_column}` > :as_of_time), "
                        f"SUM(dw_load_time < `{fact_time_column}`), "
                        f"SUM(DATE(`{fact_time_column}`) <> biz_date) "
                        f"FROM `{table_name}`"
                    ),
                    {"as_of_time": ctx.as_of_time},
                ).one()
                if int(invalid_fact_time[0] or 0):
                    failures.append(f"{table_name} 存在晚于任务执行时刻的业务时间")
                if int(invalid_fact_time[1] or 0):
                    failures.append(f"{table_name} 存在早于业务时间的入仓时间")
                if int(invalid_fact_time[2] or 0):
                    failures.append(
                        f"{table_name} 业务日期与业务时间不一致: "
                        f"{int(invalid_fact_time[2])} 条"
                    )
            if "source_update_time" in table.c:
                warehouse_time_column = (
                    "dw_update_time"
                    if "dw_update_time" in table.c
                    else "dw_load_time"
                )
                invalid_source_time = int(
                    conn.execute(
                        text(
                            f"SELECT COUNT(*) FROM `{table_name}` "
                            "WHERE source_update_time IS NULL "
                            f"OR source_update_time > `{warehouse_time_column}`"
                        )
                    ).scalar_one()
                )
                if invalid_source_time:
                    failures.append(
                        f"{table_name} 来源更新时间异常: {invalid_source_time} 条"
                    )
        for name, sql in CHECKS.items():
            violations = int(conn.execute(text(sql)).scalar_one() or 0)
            if violations:
                failures.append(f"{name}: {violations} 条")
        failures.extend(_generation_completeness_failures(ctx, conn, counts))
        for table_name, business_keys in SCD_TABLES.items():
            partition = ", ".join(f"`{key}`" for key in business_keys)
            overlap_sql = f"""
                SELECT COUNT(*) FROM (
                    SELECT effective_start_time,
                           LAG(effective_end_time) OVER (
                               PARTITION BY {partition}
                               ORDER BY effective_start_time
                           ) previous_end
                    FROM `{table_name}`
                ) x
                WHERE previous_end > effective_start_time
            """
            overlaps = int(conn.execute(text(overlap_sql)).scalar_one() or 0)
            if overlaps:
                failures.append(f"{table_name} 拉链区间重叠: {overlaps} 条")
            current_sql = f"""
                SELECT COUNT(*) FROM (
                    SELECT {partition}
                    FROM `{table_name}`
                    GROUP BY {partition}
                    HAVING SUM(is_current) <> 1
                ) x
            """
            invalid_current = int(conn.execute(text(current_sql)).scalar_one() or 0)
            if invalid_current:
                failures.append(f"{table_name} 当前版本数量异常: {invalid_current} 条")
        for table_name, business_keys in RELATION_SCD_TABLES.items():
            partition = ", ".join(f"`{key}`" for key in business_keys)
            invalid_relations = int(
                conn.execute(
                    text(
                        f"""
                        SELECT COUNT(*) FROM (
                            SELECT effective_start_time,
                                   LAG(effective_end_time) OVER (
                                       PARTITION BY {partition}
                                       ORDER BY effective_start_time
                                   ) previous_end
                            FROM `{table_name}`
                        ) x
                        WHERE previous_end > effective_start_time
                        """
                    )
                ).scalar_one()
                or 0
            )
            multiple_current = int(
                conn.execute(
                    text(
                        f"""
                        SELECT COUNT(*) FROM (
                            SELECT {partition}
                            FROM `{table_name}`
                            GROUP BY {partition}
                            HAVING SUM(is_current) > 1
                        ) x
                        """
                    )
                ).scalar_one()
                or 0
            )
            if invalid_relations:
                failures.append(f"{table_name} 关系区间重叠: {invalid_relations} 条")
            if multiple_current:
                failures.append(f"{table_name} 当前关系重复: {multiple_current} 条")
        for table_name in UNKNOWN_MEMBER_TABLES:
            unique_key_columns = doris_unique_key_columns(conn, table_name)
            if not unique_key_columns:
                failures.append(f"{table_name} 未定义Doris UNIQUE KEY")
                continue
            primary_key = unique_key_columns[0]
            unknown_count = int(
                conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM `{table_name}` "
                        f"WHERE `{primary_key}` = -1"
                    )
                ).scalar_one()
            )
            if unknown_count != 1:
                failures.append(f"{table_name} 未知成员数量: {unknown_count} 条")
        realism_metrics, realism_failures, warnings = _collect_realism_metrics(
            ctx,
            conn,
        )
        failures.extend(realism_failures)
    if (
        earliest_load_time is not None
        and ctx.gen.start_date < ctx.gen.end_date
        and earliest_load_time.date() >= ctx.as_of_time.date()
    ):
        failures.append("历史数据入仓时间全部集中在任务执行日期")
    report_path = _write_quality_report(
        ctx,
        counts,
        realism_metrics,
        failures,
        warnings,
    )
    if failures:
        raise ValueError("数据质量校验失败\n" + "\n".join(failures))
    logger.info(
        "数据质量校验通过 tables=%s rows=%s warnings=%s report=%s",
        len(counts),
        sum(counts.values()),
        len(warnings),
        report_path,
    )
