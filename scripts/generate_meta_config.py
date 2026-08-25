"""根据电商数仓 DDL 生成语义元数据配置"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DDL_PATH = ROOT_DIR / "dbmock" / "scripts" / "sql" / "ecommerce.sql"
DEFAULT_OUTPUT_PATH = ROOT_DIR / "conf" / "meta_config.yaml"

TABLE_ORDER = (
    "dim_date",
    "dim_channel_info",
    "dim_page_info",
    "dim_geo_region_zip",
    "dim_user_info_zip",
    "dim_user_tag_info",
    "bridge_user_tag_relation_zip",
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
    "bridge_promotion_scope",
    "dim_coupon_template_version",
    "bridge_coupon_scope",
    "dwd_product_sku_price_change_di",
    "dwd_traffic_session_di",
    "dwd_traffic_page_view_di",
    "dwd_traffic_search_di",
    "dwd_traffic_search_click_di",
    "dwd_interaction_cart_event_di",
    "dwd_interaction_favor_event_di",
    "dwd_marketing_user_coupon_event_di",
    "dwd_trade_order_detail_di",
    "dwd_trade_order_status_event_di",
    "dwd_trade_order_detail_activity_di",
    "dwd_trade_order_detail_coupon_di",
    "dwd_trade_pay_detail_di",
    "dwd_trade_pay_order_detail_di",
    "dwd_trade_pay_status_event_di",
    "dwd_trade_delivery_di",
    "dwd_trade_delivery_item_di",
    "dwd_trade_delivery_status_event_di",
    "dwd_trade_refund_detail_di",
    "dwd_trade_refund_status_event_di",
    "dwd_trade_refund_pay_detail_di",
    "dwd_trade_refund_pay_status_event_di",
    "dwd_service_comment_detail_di",
    "dwd_inventory_change_di",
    "dwd_inventory_daily_snapshot_df",
)

TABLE_GRAINS = {
    "dim_date": "每个自然日期一行",
    "dim_channel_info": "每个渠道一行",
    "dim_page_info": "每个标准页面一行",
    "dim_geo_region_zip": "每个行政区划业务版本一行",
    "dim_user_info_zip": "每个用户业务版本一行",
    "dim_user_tag_info": "每个用户标签一行",
    "bridge_user_tag_relation_zip": "每个用户与标签关系版本一行",
    "dim_seller_info_zip": "每个商家业务版本一行",
    "dim_shop_info_zip": "每个店铺业务版本一行",
    "dim_category_info_zip": "每个类目业务版本一行",
    "dim_brand_info": "每个品牌一行",
    "dim_payment_type": "每种支付方式一行",
    "dim_logistics_company": "每家物流公司一行",
    "dim_warehouse_info_zip": "每个仓库业务版本一行",
    "dim_spu_info_zip": "每个 SPU 业务版本一行",
    "dim_sku_info_zip": "每个 SKU 业务版本一行",
    "dim_promotion_rule_version": "每个促销活动规则版本一行",
    "bridge_promotion_scope": "每个促销规则与适用对象关系一行",
    "dim_coupon_template_version": "每个优惠券模板规则版本一行",
    "bridge_coupon_scope": "每个优惠券规则与适用对象关系一行",
    "dwd_product_sku_price_change_di": "每次 SKU 基础价格生效事件一行",
    "dwd_traffic_session_di": "每个客户端会话一行",
    "dwd_traffic_page_view_di": "每次页面访问一行",
    "dwd_traffic_search_di": "每次搜索请求一行",
    "dwd_traffic_search_click_di": "每次搜索结果点击一行",
    "dwd_interaction_cart_event_di": "每次购物车数量变化一行",
    "dwd_interaction_favor_event_di": "每次收藏状态变化一行",
    "dwd_marketing_user_coupon_event_di": "每次用户优惠券状态变化一行",
    "dwd_trade_order_detail_di": "每个订单商品明细一行",
    "dwd_trade_order_status_event_di": "每次订单状态变化一行",
    "dwd_trade_order_detail_activity_di": "每个订单明细命中的活动分摊一行",
    "dwd_trade_order_detail_coupon_di": "每个订单明细使用的优惠券分摊一行",
    "dwd_trade_pay_detail_di": "每次支付尝试一行",
    "dwd_trade_pay_order_detail_di": "每次支付向订单明细的金额分摊一行",
    "dwd_trade_pay_status_event_di": "每次支付状态变化一行",
    "dwd_trade_delivery_di": "每个物流包裹一行",
    "dwd_trade_delivery_item_di": "每个包裹商品明细一行",
    "dwd_trade_delivery_status_event_di": "每次物流状态变化一行",
    "dwd_trade_refund_detail_di": "每个退款申请商品明细一行",
    "dwd_trade_refund_status_event_di": "每次退款审核状态变化一行",
    "dwd_trade_refund_pay_detail_di": "每次退款打款尝试一行",
    "dwd_trade_refund_pay_status_event_di": "每次退款打款状态变化一行",
    "dwd_service_comment_detail_di": "每条初评或追评内容一行",
    "dwd_inventory_change_di": "每次 SKU 仓库库存数量变化一行",
    "dwd_inventory_daily_snapshot_df": "每个日期、仓库和 SKU 一行",
}

COLUMN_ALIASES = {
    "date_key": ["日期键"],
    "full_date": ["日期", "自然日期"],
    "biz_date": ["业务日期", "数据日期"],
    "user_id": ["用户ID", "客户ID"],
    "user_name": ["用户名", "登录名"],
    "nick_name": ["昵称"],
    "user_level": ["会员等级", "用户等级"],
    "shop_id": ["店铺ID"],
    "shop_name": ["店铺名称"],
    "seller_id": ["商家ID", "卖家ID"],
    "seller_name": ["商家名称", "卖家名称"],
    "category_id": ["类目ID", "品类ID"],
    "category_name": ["类目名称", "品类名称"],
    "root_category_name": ["一级类目", "根类目"],
    "brand_id": ["品牌ID"],
    "brand_name": ["品牌名称", "品牌名"],
    "spu_id": ["SPU ID", "商品款ID"],
    "spu_name": ["SPU名称", "商品款名称"],
    "sku_id": ["SKU ID", "商品规格ID"],
    "sku_name": ["SKU名称", "商品规格名称"],
    "order_id": ["订单ID"],
    "order_detail_id": ["订单明细ID"],
    "order_no": ["订单号"],
    "sku_qty": ["购买件数", "商品数量"],
    "receivable_amount": ["应收金额", "成交金额"],
    "cost_amount": ["成本金额"],
    "session_id": ["会话ID"],
    "page_view_id": ["页面访问ID"],
    "search_detail_id": ["搜索请求ID"],
    "search_keyword": ["搜索词", "搜索关键词"],
    "stay_duration_sec": ["停留时长", "页面停留秒数"],
    "payment_type_name": ["支付方式"],
    "logistics_company_name": ["物流公司", "快递公司"],
    "warehouse_name": ["仓库名称"],
    "comment_level": ["评分", "星级"],
    "comment_content": ["评价内容"],
    "available_qty": ["可用库存", "可售库存"],
    "on_hand_qty": ["在手库存", "现货库存"],
    "reserved_qty": ["预占库存", "锁定库存"],
    "in_transit_qty": ["在途库存"],
    "inventory_cost_amount": ["库存金额", "库存成本金额"],
    "dw_load_time": ["入仓时间"],
}

INDEX_VALUE_COLUMNS = {
    "gender",
    "user_level",
    "is_vip",
    "occupation",
    "income_level",
    "education_level",
    "marital_status",
    "user_status",
    "shop_name",
    "shop_type",
    "shop_status",
    "seller_name",
    "seller_type",
    "seller_status",
    "category_name",
    "category_level",
    "category_path",
    "root_category_name",
    "brand_name",
    "brand_alias",
    "channel_code",
    "channel_name",
    "channel_type",
    "page_name",
    "page_type",
    "region_name",
    "province_name",
    "city_name",
    "district_name",
    "payment_type_code",
    "payment_type_name",
    "logistics_company_name",
    "logistics_type",
    "warehouse_name",
    "warehouse_type",
    "spu_name",
    "spu_status",
    "sku_name",
    "sku_status",
    "promotion_name",
    "promotion_type",
    "promotion_scene",
    "promotion_status",
    "coupon_name",
    "coupon_type",
    "coupon_status",
    "client_type",
    "os_type",
    "search_keyword",
    "normalized_keyword",
    "search_source",
    "cart_event_type",
    "cart_source",
    "favor_target_type",
    "favor_event_type",
    "coupon_event_type",
    "order_source",
    "order_scene",
    "before_order_status",
    "after_order_status",
    "status_event_type",
    "cancel_stage",
    "pay_scene",
    "before_pay_status",
    "after_pay_status",
    "delivery_direction",
    "delivery_type",
    "before_delivery_status",
    "after_delivery_status",
    "refund_type",
    "refund_reason_code",
    "before_refund_status",
    "after_refund_status",
    "refund_account_type",
    "before_refund_pay_status",
    "after_refund_pay_status",
    "comment_type",
    "sentiment",
    "sensitive_tag",
    "change_type",
    "biz_type",
    "operator_type",
    "change_reason_code",
}

REFERENCE_COLUMNS = {
    "user_sk": ("dim_user_info_zip", "user_sk"),
    "user_id": ("dim_user_info_zip", "user_id"),
    "shop_sk": ("dim_shop_info_zip", "shop_sk"),
    "shop_id": ("dim_shop_info_zip", "shop_id"),
    "seller_sk": ("dim_seller_info_zip", "seller_sk"),
    "seller_id": ("dim_seller_info_zip", "seller_id"),
    "category_sk": ("dim_category_info_zip", "category_sk"),
    "category_id": ("dim_category_info_zip", "category_id"),
    "parent_category_id": ("dim_category_info_zip", "category_id"),
    "root_category_id": ("dim_category_info_zip", "category_id"),
    "brand_sk": ("dim_brand_info", "brand_sk"),
    "brand_id": ("dim_brand_info", "brand_id"),
    "spu_sk": ("dim_spu_info_zip", "spu_sk"),
    "spu_id": ("dim_spu_info_zip", "spu_id"),
    "sku_sk": ("dim_sku_info_zip", "sku_sk"),
    "sku_id": ("dim_sku_info_zip", "sku_id"),
    "warehouse_sk": ("dim_warehouse_info_zip", "warehouse_sk"),
    "warehouse_id": ("dim_warehouse_info_zip", "warehouse_id"),
    "region_sk": ("dim_geo_region_zip", "region_sk"),
    "region_code": ("dim_geo_region_zip", "region_code"),
    "province_code": ("dim_geo_region_zip", "province_code"),
    "city_code": ("dim_geo_region_zip", "city_code"),
    "district_code": ("dim_geo_region_zip", "district_code"),
    "parent_region_code": ("dim_geo_region_zip", "region_code"),
    "receiver_region_sk": ("dim_geo_region_zip", "region_sk"),
    "receiver_region_code": ("dim_geo_region_zip", "region_code"),
    "event_region_sk": ("dim_geo_region_zip", "region_sk"),
    "event_region_code": ("dim_geo_region_zip", "region_code"),
    "channel_sk": ("dim_channel_info", "channel_sk"),
    "channel_code": ("dim_channel_info", "channel_code"),
    "register_channel_code": ("dim_channel_info", "channel_code"),
    "page_sk": ("dim_page_info", "page_sk"),
    "page_id": ("dim_page_info", "page_id"),
    "entry_page_sk": ("dim_page_info", "page_sk"),
    "entry_page_id": ("dim_page_info", "page_id"),
    "exit_page_sk": ("dim_page_info", "page_sk"),
    "exit_page_id": ("dim_page_info", "page_id"),
    "last_page_sk": ("dim_page_info", "page_sk"),
    "last_page_id": ("dim_page_info", "page_id"),
    "click_sku_sk": ("dim_sku_info_zip", "sku_sk"),
    "click_sku_id": ("dim_sku_info_zip", "sku_id"),
    "click_spu_sk": ("dim_spu_info_zip", "spu_sk"),
    "click_spu_id": ("dim_spu_info_zip", "spu_id"),
    "click_shop_sk": ("dim_shop_info_zip", "shop_sk"),
    "click_shop_id": ("dim_shop_info_zip", "shop_id"),
    "click_category_sk": ("dim_category_info_zip", "category_sk"),
    "click_category_id": ("dim_category_info_zip", "category_id"),
    "payment_type_sk": ("dim_payment_type", "payment_type_sk"),
    "payment_type_code": ("dim_payment_type", "payment_type_code"),
    "logistics_company_sk": ("dim_logistics_company", "logistics_company_sk"),
    "logistics_company_id": ("dim_logistics_company", "logistics_company_id"),
    "tag_id": ("dim_user_tag_info", "tag_id"),
    "promotion_version_sk": ("dim_promotion_rule_version", "promotion_version_sk"),
    "promotion_id": ("dim_promotion_rule_version", "promotion_id"),
    "coupon_template_version_sk": (
        "dim_coupon_template_version",
        "coupon_template_version_sk",
    ),
    "coupon_template_id": ("dim_coupon_template_version", "coupon_template_id"),
    "session_id": ("dwd_traffic_session_di", "session_id"),
    "source_session_id": ("dwd_traffic_session_di", "session_id"),
    "search_detail_id": ("dwd_traffic_search_di", "search_detail_id"),
    "order_id": ("dwd_trade_order_detail_di", "order_id"),
    "parent_order_id": ("dwd_trade_order_detail_di", "order_id"),
    "related_order_id": ("dwd_trade_order_detail_di", "order_id"),
    "order_detail_id": ("dwd_trade_order_detail_di", "order_detail_id"),
    "pay_detail_id": ("dwd_trade_pay_detail_di", "pay_detail_id"),
    "original_pay_detail_id": ("dwd_trade_pay_detail_di", "pay_detail_id"),
    "delivery_id": ("dwd_trade_delivery_di", "delivery_id"),
    "refund_detail_id": ("dwd_trade_refund_detail_di", "refund_detail_id"),
    "refund_pay_detail_id": (
        "dwd_trade_refund_pay_detail_di",
        "refund_pay_detail_id",
    ),
    "comment_id": ("dwd_service_comment_detail_di", "comment_id"),
    "parent_comment_detail_id": (
        "dwd_service_comment_detail_di",
        "comment_detail_id",
    ),
}


class IndentDumper(yaml.SafeDumper):
    """让 YAML 列表使用常规缩进"""

    def increase_indent(self, flow: bool = False, indentless: bool = False):
        """强制序列列表项使用缩进格式"""
        return super().increase_indent(flow, False)

    def ignore_aliases(self, data: Any) -> bool:
        """禁用 YAML 锚点和别名输出"""
        return True


def _matching_parenthesis(sql: str, opening_index: int) -> int:
    """查找 SQL 表定义起始括号对应的结束位置"""
    depth = 0
    in_string = False
    index = opening_index
    while index < len(sql):
        char = sql[index]
        if char == "'":
            if in_string and index + 1 < len(sql) and sql[index + 1] == "'":
                index += 2
                continue
            in_string = not in_string
        elif not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index
        index += 1
    raise ValueError("DDL 表定义括号未闭合")


def _split_columns(body: str) -> list[str]:
    """按顶层逗号拆分 DDL 字段定义"""
    parts: list[str] = []
    start = 0
    depth = 0
    in_string = False
    index = 0
    while index < len(body):
        char = body[index]
        if char == "'":
            if in_string and index + 1 < len(body) and body[index + 1] == "'":
                index += 2
                continue
            in_string = not in_string
        elif not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append(body[start:index].strip())
                start = index + 1
        index += 1
    parts.append(body[start:].strip())
    return [part for part in parts if part]


def parse_ecommerce_schema(ddl_path: Path = DEFAULT_DDL_PATH) -> dict[str, Any]:
    """解析电商 DDL 中的表、字段和注释"""
    sql = ddl_path.read_text(encoding="utf-8")
    result: dict[str, Any] = {}
    for statement in sql.split(";"):
        match = re.search(
            r"CREATE\s+TABLE\s+`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\(",
            statement,
            re.IGNORECASE,
        )
        if match is None:
            continue
        table_name = match.group(1)
        opening_index = statement.find("(", match.start())
        closing_index = _matching_parenthesis(statement, opening_index)
        body = statement[opening_index + 1 : closing_index]
        tail = statement[closing_index + 1 :]
        table_comment_match = re.search(r"\bCOMMENT\s+'([^']*)'", tail, re.DOTALL)
        table_comment = (
            table_comment_match.group(1).strip()
            if table_comment_match
            else table_name
        )
        columns = []
        for definition in _split_columns(body):
            column_match = re.match(
                r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s+([A-Za-z]+(?:\s*\([^)]*\))?)",
                definition,
                re.DOTALL,
            )
            if column_match is None:
                continue
            column_name = column_match.group(1)
            if column_name.upper() in {
                "UNIQUE",
                "DUPLICATE",
                "PRIMARY",
                "KEY",
                "INDEX",
                "CONSTRAINT",
            }:
                continue
            comment_match = re.search(r"\bCOMMENT\s+'([^']*)'", definition, re.DOTALL)
            columns.append(
                {
                    "name": column_name,
                    "type": re.sub(r"\s+", "", column_match.group(2)).upper(),
                    "comment": (
                        re.sub(r"\s+", " ", comment_match.group(1)).strip()
                        if comment_match
                        else column_name
                    ),
                }
            )
        result[table_name] = {"comment": table_comment, "columns": columns}
    return result


def _index_values(column: dict[str, str]) -> bool:
    """判断字段是否默认开启取值索引"""
    name = column["name"]
    return name in INDEX_VALUE_COLUMNS or name.endswith(
        ("_status", "_type", "_scene", "_source", "_group", "_domain", "_mode")
    )


def _reference(
    table_name: str,
    column_name: str,
    column_keys: set[tuple[str, str]],
) -> tuple[str, str] | None:
    """推断字段在现有元数据中的关联引用"""
    if column_name == "biz_date":
        target = ("dim_date", "full_date")
    elif column_name.endswith("_date_key"):
        target = ("dim_date", "date_key")
    else:
        target = REFERENCE_COLUMNS.get(column_name)
    if target is None or target not in column_keys or target == (table_name, column_name):
        return None
    return target


def _metric(
    name: str,
    description: str,
    columns: list[str],
    alias: list[str],
) -> dict[str, Any]:
    """构造业务指标的配置字典"""
    return {
        "name": name,
        "description": description,
        "relevant_columns": [
            {"t_name": value.split(".", 1)[0], "c_name": value.split(".", 1)[1]}
            for value in columns
        ],
        "alias": alias,
    }


def build_metrics() -> list[dict[str, Any]]:
    """构建常用综合电商业务指标"""
    m = _metric
    return [
        m("页面浏览量", "页面访问事件行数，公式为 COUNT(page_view_id)", ["dwd_traffic_page_view_di.page_view_id", "dwd_traffic_page_view_di.biz_date"], ["PV", "浏览量", "访问量"]),
        m("访客数", "按用户优先、匿名设备兜底去重的访问主体数，公式为 COUNT(DISTINCT COALESCE(user_id, device_id))", ["dwd_traffic_page_view_di.user_id", "dwd_traffic_page_view_di.device_id", "dwd_traffic_page_view_di.biz_date"], ["UV", "独立访客数"]),
        m("会话数", "客户端会话数量，公式为 COUNT(DISTINCT session_id)", ["dwd_traffic_session_di.session_id", "dwd_traffic_session_di.biz_date"], ["访问次数", "Session数"]),
        m("活跃用户数", "发生会话的登录用户数，排除游客，公式为 COUNT(DISTINCT user_id)", ["dwd_traffic_session_di.user_id", "dwd_traffic_session_di.biz_date"], ["活跃会员数"]),
        m("跳出率", "跳出会话数除以全部会话数，跳出会话取 is_bounce=1", ["dwd_traffic_session_di.is_bounce", "dwd_traffic_session_di.session_id"], ["会话跳出率"]),
        m("平均会话时长", "会话持续秒数的平均值，公式为 AVG(session_duration_sec)", ["dwd_traffic_session_di.session_duration_sec"], ["平均访问时长"]),
        m("会话平均浏览深度", "页面浏览量除以会话数，也可取会话表 page_view_count 的平均值", ["dwd_traffic_session_di.page_view_count", "dwd_traffic_session_di.session_id"], ["平均访问深度", "每次访问页数"]),
        m("人均浏览量", "页面浏览量除以访客数", ["dwd_traffic_page_view_di.page_view_id", "dwd_traffic_page_view_di.user_id", "dwd_traffic_page_view_di.device_id"], ["人均PV"]),
        m("平均页面停留时长", "页面停留秒数的平均值，公式为 AVG(stay_duration_sec)", ["dwd_traffic_page_view_di.stay_duration_sec"], ["平均停留时间"]),
        m("搜索次数", "搜索请求数量，公式为 COUNT(search_detail_id)", ["dwd_traffic_search_di.search_detail_id", "dwd_traffic_search_di.biz_date"], ["搜索量", "搜索PV"]),
        m("搜索用户数", "发起搜索的登录用户去重数，公式为 COUNT(DISTINCT user_id)", ["dwd_traffic_search_di.user_id", "dwd_traffic_search_di.biz_date"], ["搜索UV"]),
        m("搜索无结果率", "无结果搜索次数除以搜索次数，取 is_no_result=1", ["dwd_traffic_search_di.is_no_result", "dwd_traffic_search_di.search_detail_id"], ["零结果率"]),
        m("搜索点击次数", "搜索结果点击事件数，公式为 COUNT(search_click_id)", ["dwd_traffic_search_click_di.search_click_id", "dwd_traffic_search_click_di.biz_date"], ["搜索点击量"]),
        m("搜索点击率", "产生至少一次点击的搜索请求数除以成功搜索请求数", ["dwd_traffic_search_di.search_detail_id", "dwd_traffic_search_di.is_search_success", "dwd_traffic_search_click_di.search_detail_id"], ["搜索CTR"]),
        m("平均搜索点击位次", "搜索点击结果位次的平均值，数值越小表示结果排序越靠前", ["dwd_traffic_search_click_di.click_rank"], ["平均点击排名"]),
        m("加购次数", "购物车事件类型为加入的事件数", ["dwd_interaction_cart_event_di.cart_event_id", "dwd_interaction_cart_event_di.cart_event_type", "dwd_interaction_cart_event_di.biz_date"], ["加购量", "加入购物车次数"]),
        m("加购用户数", "发生加入购物车事件的去重用户数", ["dwd_interaction_cart_event_di.user_id", "dwd_interaction_cart_event_di.cart_event_type"], ["加购UV"]),
        m("加购件数", "加入事件中正向商品数量变化量之和", ["dwd_interaction_cart_event_di.sku_qty_delta", "dwd_interaction_cart_event_di.cart_event_type"], ["加购商品数"]),
        m("购物车删除次数", "购物车事件类型为删除的事件数", ["dwd_interaction_cart_event_di.cart_event_id", "dwd_interaction_cart_event_di.cart_event_type"], ["删购次数"]),
        m("购物车清空次数", "购物车事件类型为清空的事件数", ["dwd_interaction_cart_event_di.cart_event_id", "dwd_interaction_cart_event_di.cart_event_type"], ["清空购物车次数"]),
        m("购物车放弃率", "发生加购但未产生订单的会话数除以发生加购的会话数", ["dwd_interaction_cart_event_di.session_id", "dwd_interaction_cart_event_di.cart_event_type", "dwd_trade_order_detail_di.source_session_id"], ["弃购率"]),
        m("收藏次数", "收藏事件类型为收藏的事件数", ["dwd_interaction_favor_event_di.favor_event_id", "dwd_interaction_favor_event_di.favor_event_type", "dwd_interaction_favor_event_di.biz_date"], ["收藏量"]),
        m("收藏用户数", "发生收藏事件的去重用户数", ["dwd_interaction_favor_event_di.user_id", "dwd_interaction_favor_event_di.favor_event_type"], ["收藏UV"]),
        m("取消收藏率", "取消收藏事件数除以收藏事件数", ["dwd_interaction_favor_event_di.favor_event_type", "dwd_interaction_favor_event_di.favor_event_id"], ["取消收藏比例"]),
        m("下单订单数", "下单明细中的去重订单数，公式为 COUNT(DISTINCT order_id)", ["dwd_trade_order_detail_di.order_id", "dwd_trade_order_detail_di.biz_date"], ["订单数", "下单量"]),
        m("下单用户数", "产生订单的去重用户数，公式为 COUNT(DISTINCT user_id)", ["dwd_trade_order_detail_di.user_id", "dwd_trade_order_detail_di.biz_date"], ["购买用户数", "下单UV"]),
        m("订单明细数", "订单商品明细行数，公式为 COUNT(order_detail_id)", ["dwd_trade_order_detail_di.order_detail_id"], ["订单行数"]),
        m("销量", "订单明细购买件数之和，公式为 SUM(sku_qty)", ["dwd_trade_order_detail_di.sku_qty", "dwd_trade_order_detail_di.biz_date"], ["销售件数", "销售数量"]),
        m("下单GMV", "下单口径应收金额之和，包含之后取消的订单，公式为 SUM(receivable_amount)", ["dwd_trade_order_detail_di.receivable_amount", "dwd_trade_order_detail_di.biz_date"], ["GMV", "下单金额", "成交总额"]),
        m("有效GMV", "排除最终状态为 CANCELLED 的订单后，应收金额之和", ["dwd_trade_order_detail_di.order_id", "dwd_trade_order_detail_di.receivable_amount", "dwd_trade_order_status_event_di.after_order_status", "dwd_trade_order_status_event_di.event_time"], ["有效成交额"]),
        m("商品销售额", "优惠前商品销售金额之和，公式为 SUM(sale_amount)", ["dwd_trade_order_detail_di.sale_amount"], ["销售额", "商品金额"]),
        m("实付金额", "支付状态为 SUCCESS 的支付尝试对应订单明细分摊金额之和", ["dwd_trade_pay_order_detail_di.allocated_pay_amount", "dwd_trade_pay_order_detail_di.pay_detail_id", "dwd_trade_pay_status_event_di.pay_detail_id", "dwd_trade_pay_status_event_di.after_pay_status"], ["支付金额", "实付GMV"]),
        m("净支付金额", "实付金额减去退款打款状态为 SUCCESS 的退款金额", ["dwd_trade_pay_order_detail_di.allocated_pay_amount", "dwd_trade_pay_status_event_di.after_pay_status", "dwd_trade_refund_pay_detail_di.refund_amount", "dwd_trade_refund_pay_status_event_di.after_refund_pay_status"], ["净GMV", "净收入"]),
        m("客单价", "有效GMV除以有效订单数，按订单粒度先聚合避免明细重复", ["dwd_trade_order_detail_di.order_id", "dwd_trade_order_detail_di.receivable_amount", "dwd_trade_order_status_event_di.after_order_status"], ["AOV", "平均订单金额"]),
        m("件单价", "有效GMV除以销量", ["dwd_trade_order_detail_di.receivable_amount", "dwd_trade_order_detail_di.sku_qty", "dwd_trade_order_status_event_di.after_order_status"], ["平均每件成交价"]),
        m("连带率", "销量除以订单数，表示每笔订单平均购买件数", ["dwd_trade_order_detail_di.sku_qty", "dwd_trade_order_detail_di.order_id"], ["件单量", "订单连带率"]),
        m("支付订单数", "存在 SUCCESS 支付状态的去重订单数", ["dwd_trade_pay_status_event_di.pay_detail_id", "dwd_trade_pay_status_event_di.after_pay_status", "dwd_trade_pay_order_detail_di.pay_detail_id", "dwd_trade_pay_order_detail_di.order_id"], ["已支付订单数"]),
        m("支付用户数", "存在 SUCCESS 支付尝试的去重用户数", ["dwd_trade_pay_detail_di.user_id", "dwd_trade_pay_detail_di.pay_detail_id", "dwd_trade_pay_status_event_di.after_pay_status"], ["支付UV"]),
        m("支付成功率", "最终状态为 SUCCESS 的支付尝试数除以全部支付尝试数", ["dwd_trade_pay_detail_di.pay_detail_id", "dwd_trade_pay_status_event_di.pay_detail_id", "dwd_trade_pay_status_event_di.after_pay_status"], ["付款成功率"]),
        m("支付转化率", "支付订单数除以下单订单数", ["dwd_trade_order_detail_di.order_id", "dwd_trade_pay_order_detail_di.order_id", "dwd_trade_pay_status_event_di.after_pay_status"], ["下单支付转化率"]),
        m("订单取消数", "最终订单状态为 CANCELLED 的去重订单数", ["dwd_trade_order_status_event_di.order_id", "dwd_trade_order_status_event_di.after_order_status", "dwd_trade_order_status_event_di.event_time"], ["取消订单数"]),
        m("订单取消率", "最终状态为 CANCELLED 的订单数除以下单订单数", ["dwd_trade_order_detail_di.order_id", "dwd_trade_order_status_event_di.after_order_status"], ["取消率"]),
        m("首购订单数", "is_first_order=1 的去重订单数", ["dwd_trade_order_detail_di.order_id", "dwd_trade_order_detail_di.is_first_order"], ["新客订单数", "首单数"]),
        m("首购订单占比", "首购订单数除以下单订单数", ["dwd_trade_order_detail_di.order_id", "dwd_trade_order_detail_di.is_first_order"], ["新客订单占比"]),
        m("毛利额", "有效订单应收金额减去标准成本金额，排除取消订单", ["dwd_trade_order_detail_di.receivable_amount", "dwd_trade_order_detail_di.cost_amount", "dwd_trade_order_status_event_di.after_order_status"], ["销售毛利"]),
        m("毛利率", "毛利额除以有效GMV", ["dwd_trade_order_detail_di.receivable_amount", "dwd_trade_order_detail_di.cost_amount", "dwd_trade_order_status_event_di.after_order_status"], ["销售毛利率"]),
        m("总优惠金额", "活动、优惠券和积分优惠分摊金额之和", ["dwd_trade_order_detail_di.activity_discount_amount", "dwd_trade_order_detail_di.coupon_discount_amount", "dwd_trade_order_detail_di.points_discount_amount"], ["优惠总额"]),
        m("综合优惠率", "总优惠金额除以优惠前销售金额", ["dwd_trade_order_detail_di.activity_discount_amount", "dwd_trade_order_detail_di.coupon_discount_amount", "dwd_trade_order_detail_di.points_discount_amount", "dwd_trade_order_detail_di.sale_amount"], ["折扣率", "优惠深度"]),
        m("活动优惠金额", "订单明细活动优惠分摊金额之和", ["dwd_trade_order_detail_activity_di.promotion_discount_amount"], ["促销优惠金额", "活动减免"]),
        m("优惠券优惠金额", "订单明细优惠券优惠分摊金额之和", ["dwd_trade_order_detail_coupon_di.coupon_discount_amount"], ["券优惠金额", "优惠券抵扣"]),
        m("积分优惠金额", "订单明细积分优惠分摊金额之和", ["dwd_trade_order_detail_di.points_discount_amount"], ["积分抵扣金额"]),
        m("运费收入", "订单明细运费分摊金额之和", ["dwd_trade_order_detail_di.freight_amount"], ["运费金额"]),
        m("风险订单率", "is_risk_order=1 的订单数除以下单订单数", ["dwd_trade_order_detail_di.order_id", "dwd_trade_order_detail_di.is_risk_order"], ["风控订单占比"]),
        m("跨境订单率", "is_cross_border=1 的订单数除以下单订单数", ["dwd_trade_order_detail_di.order_id", "dwd_trade_order_detail_di.is_cross_border"], ["跨境订单占比"]),
        m("活动订单数", "至少命中一条促销活动的去重订单数", ["dwd_trade_order_detail_activity_di.order_id", "dwd_trade_order_detail_activity_di.promotion_id"], ["促销订单数"]),
        m("活动订单占比", "活动订单数除以下单订单数", ["dwd_trade_order_detail_activity_di.order_id", "dwd_trade_order_detail_di.order_id"], ["促销渗透率"]),
        m("优惠券领取量", "用户券事件类型为领取的事件数", ["dwd_marketing_user_coupon_event_di.user_coupon_event_id", "dwd_marketing_user_coupon_event_di.coupon_event_type"], ["领券数", "发券领取量"]),
        m("优惠券领取用户数", "领取优惠券的去重用户数", ["dwd_marketing_user_coupon_event_di.user_id", "dwd_marketing_user_coupon_event_di.coupon_event_type"], ["领券用户数"]),
        m("优惠券使用量", "用户券事件类型为使用的事件数", ["dwd_marketing_user_coupon_event_di.user_coupon_id", "dwd_marketing_user_coupon_event_di.coupon_event_type"], ["用券数"]),
        m("优惠券使用率", "使用的用户券实例数除以领取的用户券实例数", ["dwd_marketing_user_coupon_event_di.user_coupon_id", "dwd_marketing_user_coupon_event_di.coupon_event_type"], ["领券核销率", "券核销率"]),
        m("正向包裹数", "delivery_direction 为正向的物流包裹数", ["dwd_trade_delivery_di.delivery_id", "dwd_trade_delivery_di.delivery_direction", "dwd_trade_delivery_di.biz_date"], ["发货包裹数"]),
        m("发货订单数", "物流状态达到 SHIPPED 的去重订单数", ["dwd_trade_delivery_di.delivery_id", "dwd_trade_delivery_di.order_id", "dwd_trade_delivery_status_event_di.delivery_id", "dwd_trade_delivery_status_event_di.after_delivery_status"], ["已发货订单数"]),
        m("签收订单数", "物流状态达到 SIGNED 的去重订单数", ["dwd_trade_delivery_di.delivery_id", "dwd_trade_delivery_di.order_id", "dwd_trade_delivery_status_event_di.delivery_id", "dwd_trade_delivery_status_event_di.after_delivery_status"], ["已签收订单数"]),
        m("订单履约率", "已签收订单数除以支付订单数", ["dwd_trade_delivery_di.order_id", "dwd_trade_delivery_status_event_di.after_delivery_status", "dwd_trade_pay_order_detail_di.order_id", "dwd_trade_pay_status_event_di.after_pay_status"], ["签收率"]),
        m("平均发货时长", "包裹创建时间减去订单创建时间的平均小时数，仅统计正向包裹", ["dwd_trade_order_detail_di.order_id", "dwd_trade_order_detail_di.order_create_time", "dwd_trade_delivery_di.order_id", "dwd_trade_delivery_di.delivery_create_time", "dwd_trade_delivery_di.delivery_direction"], ["平均出库时长", "下单到发货时长"]),
        m("平均配送时长", "SIGNED 事件时间减去包裹创建时间的平均小时数，仅统计正向包裹", ["dwd_trade_delivery_di.delivery_id", "dwd_trade_delivery_di.delivery_create_time", "dwd_trade_delivery_di.delivery_direction", "dwd_trade_delivery_status_event_di.delivery_id", "dwd_trade_delivery_status_event_di.after_delivery_status", "dwd_trade_delivery_status_event_di.event_time"], ["平均物流时长", "发货到签收时长"]),
        m("拆包率", "正向包裹数大于1的订单数除以有正向包裹的订单数", ["dwd_trade_delivery_di.order_id", "dwd_trade_delivery_di.delivery_id", "dwd_trade_delivery_di.delivery_direction"], ["订单拆包率"]),
        m("平均包裹重量", "正向包裹重量的平均值", ["dwd_trade_delivery_di.package_weight_kg", "dwd_trade_delivery_di.delivery_direction"], ["平均物流重量"]),
        m("逆向包裹数", "delivery_direction 为逆向的物流包裹数", ["dwd_trade_delivery_di.delivery_id", "dwd_trade_delivery_di.delivery_direction"], ["退货包裹数"]),
        m("退款申请数", "退款申请明细数量，公式为 COUNT(refund_detail_id)", ["dwd_trade_refund_detail_di.refund_detail_id", "dwd_trade_refund_detail_di.biz_date"], ["退款单数", "退款笔数"]),
        m("退款申请金额", "申请退款总金额之和", ["dwd_trade_refund_detail_di.refund_apply_amount"], ["申请退款金额"]),
        m("退款成功数", "退款打款最终状态为 SUCCESS 的去重退款明细数", ["dwd_trade_refund_pay_detail_di.refund_detail_id", "dwd_trade_refund_pay_detail_di.refund_pay_detail_id", "dwd_trade_refund_pay_status_event_di.refund_pay_detail_id", "dwd_trade_refund_pay_status_event_di.after_refund_pay_status"], ["成功退款单数"]),
        m("退款成功金额", "退款打款最终状态为 SUCCESS 的退款金额之和", ["dwd_trade_refund_pay_detail_di.refund_amount", "dwd_trade_refund_pay_detail_di.refund_pay_detail_id", "dwd_trade_refund_pay_status_event_di.after_refund_pay_status"], ["退款金额", "实际退款金额"]),
        m("退款率", "退款申请涉及的去重订单明细数除以订单明细数", ["dwd_trade_refund_detail_di.order_detail_id", "dwd_trade_order_detail_di.order_detail_id"], ["售后退款率"]),
        m("退款金额率", "退款成功金额除以实付金额", ["dwd_trade_refund_pay_detail_di.refund_amount", "dwd_trade_refund_pay_status_event_di.after_refund_pay_status", "dwd_trade_pay_order_detail_di.allocated_pay_amount", "dwd_trade_pay_status_event_di.after_pay_status"], ["退款金额占比"]),
        m("退款审核通过率", "最终审核状态为 APPROVED 的退款明细数除以退款申请数", ["dwd_trade_refund_status_event_di.refund_detail_id", "dwd_trade_refund_status_event_di.after_refund_status"], ["退款通过率"]),
        m("质量问题退款率", "is_quality_issue=1 的退款申请数除以退款申请数", ["dwd_trade_refund_detail_di.refund_detail_id", "dwd_trade_refund_detail_di.is_quality_issue"], ["质量退款占比"]),
        m("退货率", "need_return_goods=1 的退款申请数除以订单明细数", ["dwd_trade_refund_detail_di.order_detail_id", "dwd_trade_refund_detail_di.need_return_goods", "dwd_trade_order_detail_di.order_detail_id"], ["商品退货率"]),
        m("平均退款处理时长", "退款打款 SUCCESS 时间减去退款申请时间的平均小时数", ["dwd_trade_refund_detail_di.refund_detail_id", "dwd_trade_refund_detail_di.apply_time", "dwd_trade_refund_pay_detail_di.refund_detail_id", "dwd_trade_refund_pay_status_event_di.after_refund_pay_status", "dwd_trade_refund_pay_status_event_di.event_time"], ["平均退款时长"]),
        m("评价数", "初评内容数量，排除追评避免重复计算评价主题", ["dwd_service_comment_detail_di.comment_detail_id", "dwd_service_comment_detail_di.comment_type", "dwd_service_comment_detail_di.biz_date"], ["评论数", "评价量"]),
        m("评价率", "产生初评的去重订单明细数除以已签收订单明细数", ["dwd_service_comment_detail_di.order_detail_id", "dwd_service_comment_detail_di.comment_type", "dwd_trade_delivery_item_di.order_detail_id", "dwd_trade_delivery_status_event_di.after_delivery_status"], ["评论率"]),
        m("好评率", "初评中综合评分为4或5的评价数除以有评分初评数", ["dwd_service_comment_detail_di.comment_level", "dwd_service_comment_detail_di.comment_type"], ["正向评价率"]),
        m("差评率", "初评中综合评分为1或2的评价数除以有评分初评数", ["dwd_service_comment_detail_di.comment_level", "dwd_service_comment_detail_di.comment_type"], ["负向评价率"]),
        m("平均评分", "有评分初评的综合评分平均值", ["dwd_service_comment_detail_di.comment_level", "dwd_service_comment_detail_di.comment_type"], ["平均星级"]),
        m("有图评价率", "初评中 image_count 大于0的评价数除以初评数", ["dwd_service_comment_detail_di.image_count", "dwd_service_comment_detail_di.comment_type"], ["晒图率"]),
        m("追评率", "存在追评的评价主题数除以初评主题数", ["dwd_service_comment_detail_di.comment_id", "dwd_service_comment_detail_di.comment_type"], ["追加评价率"]),
        m("平均服务评分", "初评服务评分的平均值", ["dwd_service_comment_detail_di.service_score", "dwd_service_comment_detail_di.comment_type"], ["服务评分"]),
        m("平均物流评分", "初评物流评分的平均值", ["dwd_service_comment_detail_di.logistics_score", "dwd_service_comment_detail_di.comment_type"], ["物流评分"]),
        m("期末在手库存", "查询周期最后一个快照日的在手库存数量之和", ["dwd_inventory_daily_snapshot_df.on_hand_qty", "dwd_inventory_daily_snapshot_df.biz_date"], ["在手库存", "现货库存"]),
        m("期末可用库存", "查询周期最后一个快照日的可用库存数量之和", ["dwd_inventory_daily_snapshot_df.available_qty", "dwd_inventory_daily_snapshot_df.biz_date"], ["可售库存", "可用库存"]),
        m("期末预占库存", "查询周期最后一个快照日的预占库存数量之和", ["dwd_inventory_daily_snapshot_df.reserved_qty", "dwd_inventory_daily_snapshot_df.biz_date"], ["锁定库存"]),
        m("期末在途库存", "查询周期最后一个快照日的在途库存数量之和", ["dwd_inventory_daily_snapshot_df.in_transit_qty", "dwd_inventory_daily_snapshot_df.biz_date"], ["在途数量"]),
        m("期末库存金额", "查询周期最后一个快照日的库存成本金额之和", ["dwd_inventory_daily_snapshot_df.inventory_cost_amount", "dwd_inventory_daily_snapshot_df.biz_date"], ["库存价值", "库存成本"]),
        m("零库存SKU数", "查询周期最后一个快照日 available_qty=0 的去重 SKU 数", ["dwd_inventory_daily_snapshot_df.sku_id", "dwd_inventory_daily_snapshot_df.available_qty", "dwd_inventory_daily_snapshot_df.biz_date"], ["缺货SKU数"]),
        m("缺货率", "查询周期最后一个快照日 available_qty=0 的 SKU 仓库记录数除以全部 SKU 仓库记录数", ["dwd_inventory_daily_snapshot_df.sku_id", "dwd_inventory_daily_snapshot_df.warehouse_id", "dwd_inventory_daily_snapshot_df.available_qty", "dwd_inventory_daily_snapshot_df.biz_date"], ["库存缺货率"]),
        m("库存预警SKU数", "查询周期最后一个快照日可用库存不高于 SKU 预警阈值的去重 SKU 数", ["dwd_inventory_daily_snapshot_df.sku_id", "dwd_inventory_daily_snapshot_df.available_qty", "dim_sku_info_zip.sku_id", "dim_sku_info_zip.warning_stock_qty"], ["低库存SKU数"]),
        m("入库数量", "库存事件中 on_hand_qty_delta 大于0的数量之和", ["dwd_inventory_change_di.on_hand_qty_delta", "dwd_inventory_change_di.change_type", "dwd_inventory_change_di.biz_date"], ["入库量"]),
        m("出库数量", "库存事件中 on_hand_qty_delta 小于0的绝对值之和", ["dwd_inventory_change_di.on_hand_qty_delta", "dwd_inventory_change_di.change_type", "dwd_inventory_change_di.biz_date"], ["出库量"]),
        m("库存周转率", "查询周期出库成本除以日均库存成本金额", ["dwd_inventory_change_di.total_cost_delta", "dwd_inventory_change_di.on_hand_qty_delta", "dwd_inventory_daily_snapshot_df.inventory_cost_amount", "dwd_inventory_daily_snapshot_df.biz_date"], ["存货周转率"]),
        m("库存周转天数", "查询周期天数除以库存周转率", ["dwd_inventory_change_di.total_cost_delta", "dwd_inventory_daily_snapshot_df.inventory_cost_amount", "dwd_inventory_daily_snapshot_df.biz_date"], ["存货周转天数"]),
        m("注册用户数", "当前有效用户维度中排除未知成员后的去重用户数", ["dim_user_info_zip.user_id", "dim_user_info_zip.is_current", "dim_user_info_zip.is_deleted"], ["累计用户数", "会员数"]),
        m("新增注册用户数", "注册时间落在查询周期内的去重用户数", ["dim_user_info_zip.user_id", "dim_user_info_zip.register_time"], ["新增用户数", "新注册用户"]),
        m("VIP用户数", "当前用户版本中 is_vip=1 的去重用户数", ["dim_user_info_zip.user_id", "dim_user_info_zip.is_vip", "dim_user_info_zip.is_current"], ["VIP会员数"]),
        m("沉默流失用户数", "当前用户状态为沉默或流失的去重用户数", ["dim_user_info_zip.user_id", "dim_user_info_zip.user_status", "dim_user_info_zip.is_current"], ["不活跃用户数"]),
        m("日活跃用户数", "按业务日统计发生会话的去重登录用户数", ["dwd_traffic_session_di.user_id", "dwd_traffic_session_di.biz_date"], ["DAU"]),
        m("月活跃用户数", "按自然月统计发生会话的去重登录用户数", ["dwd_traffic_session_di.user_id", "dwd_traffic_session_di.biz_date"], ["MAU"]),
        m("用户活跃粘性", "日活跃用户数除以月活跃用户数，按月计算日均 DAU/MAU", ["dwd_traffic_session_di.user_id", "dwd_traffic_session_di.biz_date"], ["DAU/MAU", "活跃度"]),
        m("复购用户数", "查询周期内产生至少2个去重订单的用户数", ["dwd_trade_order_detail_di.user_id", "dwd_trade_order_detail_di.order_id", "dwd_trade_order_detail_di.biz_date"], ["重复购买用户数"]),
        m("复购率", "复购用户数除以下单用户数", ["dwd_trade_order_detail_di.user_id", "dwd_trade_order_detail_di.order_id", "dwd_trade_order_detail_di.biz_date"], ["重复购买率"]),
        m("在售SPU数", "当前有效 SPU 维度中状态为在售的去重 SPU 数", ["dim_spu_info_zip.spu_id", "dim_spu_info_zip.spu_status", "dim_spu_info_zip.is_current"], ["有效商品款数"]),
        m("在售SKU数", "当前有效 SKU 维度中状态为在售的去重 SKU 数", ["dim_sku_info_zip.sku_id", "dim_sku_info_zip.sku_status", "dim_sku_info_zip.is_current"], ["有效商品数"]),
        m("动销SKU数", "查询周期内产生有效销量的去重 SKU 数", ["dwd_trade_order_detail_di.sku_id", "dwd_trade_order_detail_di.sku_qty", "dwd_trade_order_detail_di.biz_date", "dwd_trade_order_status_event_di.after_order_status"], ["有销量SKU数"]),
        m("SKU动销率", "动销 SKU 数除以查询周期内在售 SKU 数", ["dwd_trade_order_detail_di.sku_id", "dwd_trade_order_status_event_di.after_order_status", "dim_sku_info_zip.sku_id", "dim_sku_info_zip.sku_status"], ["商品动销率"]),
        m("新增SPU数", "上架时间落在查询周期内的去重 SPU 数", ["dim_spu_info_zip.spu_id", "dim_spu_info_zip.on_shelf_time"], ["上新SPU数", "新品数"]),
        m("平均成交单价", "有效GMV除以销量", ["dwd_trade_order_detail_di.receivable_amount", "dwd_trade_order_detail_di.sku_qty", "dwd_trade_order_status_event_di.after_order_status"], ["ASP", "平均销售单价"]),
    ]


def build_config(ddl_path: Path = DEFAULT_DDL_PATH) -> dict[str, Any]:
    """构建完整元数据配置并校验所有引用"""
    schema = parse_ecommerce_schema(ddl_path)
    if set(schema) != set(TABLE_ORDER):
        missing = sorted(set(TABLE_ORDER) - set(schema))
        unexpected = sorted(set(schema) - set(TABLE_ORDER))
        raise ValueError(f"DDL 表清单变化，缺失={missing}，意外出现={unexpected}")
    column_keys = {
        (table_name, column["name"])
        for table_name, table in schema.items()
        for column in table["columns"]
    }
    tables = []
    for table_name in TABLE_ORDER:
        source = schema[table_name]
        columns = []
        for column in source["columns"]:
            target = _reference(table_name, column["name"], column_keys)
            item: dict[str, Any] = {
                "name": column["name"],
                "description": column["comment"],
                "alias": COLUMN_ALIASES.get(column["name"], []),
                "index_values": _index_values(column),
            }
            if target is not None:
                item["reference_t_name"] = target[0]
                item["reference_c_name"] = target[1]
            columns.append(item)
        tables.append(
            {
                "name": table_name,
                "role": "fact" if table_name.startswith("dwd_") else "dim",
                "description": f"{source['comment']}，{TABLE_GRAINS[table_name]}",
                "columns": columns,
            }
        )
    metrics = build_metrics()
    missing_metric_columns = sorted(
        {
            (reference["t_name"], reference["c_name"])
            for metric in metrics
            for reference in metric["relevant_columns"]
            if (reference["t_name"], reference["c_name"]) not in column_keys
        }
    )
    if missing_metric_columns:
        raise ValueError(f"指标引用字段不存在: {missing_metric_columns}")
    metric_names = [metric["name"] for metric in metrics]
    if len(metric_names) != len(set(metric_names)):
        raise ValueError("指标名称重复")
    return {"tables": tables, "metrics": metrics}


def main() -> None:
    """解析命令行参数并生成元数据 YAML 配置"""
    parser = argparse.ArgumentParser(description="生成电商数仓语义元数据配置")
    parser.add_argument("--ddl", type=Path, default=DEFAULT_DDL_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    config = build_config(args.ddl.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.dump(
            config,
            Dumper=IndentDumper,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )
    print(
        f"元数据配置生成完成 tables={len(config['tables'])} "
        f"columns={sum(len(table['columns']) for table in config['tables'])} "
        f"metrics={len(config['metrics'])} output={args.output}"
    )


if __name__ == "__main__":
    main()
