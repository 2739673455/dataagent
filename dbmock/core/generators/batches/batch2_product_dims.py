"""批次2：生成 SPU 和 SKU 维度拉链数据。"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import MetaData, select

from ..catalogs import (
    BLOCKED_CATEGORY_KEYWORDS,
    BRAND_PRODUCT_CATALOG,
    CATEGORY_NAMING_RULES,
    MODEL_TOKENS_BY_ROOT,
    SKU_PER_SPU,
    SKU_TARGET_COUNT,
    SPU_TARGET_COUNT,
    TECH_CATEGORY_ROOTS,
    UNIT_BY_ROOT,
)
from ..settings import RunContext
from ..utils.loaders import bulk_insert

FAR_FUTURE_DATE = date(9999, 12, 31)


def _validate_catalogs(level1_categories: set[str], brand_names: set[str]) -> None:
    """校验品牌词库和类目命名规则是否覆盖当前维表。"""
    missing_category_rules = level1_categories - set(CATEGORY_NAMING_RULES.keys())
    if missing_category_rules:
        raise ValueError(f"缺少一级类目命名规则: {sorted(missing_category_rules)}")

    missing_brand_catalog = brand_names - set(BRAND_PRODUCT_CATALOG.keys())
    if missing_brand_catalog:
        raise ValueError(f"缺少品牌词库配置: {sorted(missing_brand_catalog)}")

    for brand_name, catalog in BRAND_PRODUCT_CATALOG.items():
        invalid_categories = set(catalog["allowed_categories"]) - level1_categories
        if invalid_categories:
            raise ValueError(
                f"品牌 {brand_name} 配置了不存在的一级类目: {sorted(invalid_categories)}"
            )


def _has_rows(conn, table) -> bool:
    """判断目标表是否已经存在数据。"""
    stmt = select(table.c.id).limit(1)
    return conn.execute(stmt).first() is not None


def _load_current_rows(conn, table) -> list[dict[str, Any]]:
    """加载当前有效的维度数据。"""
    stmt = select(table).where(table.c.is_current == 1)
    return [dict(row) for row in conn.execute(stmt).mappings()]


def _build_model_token(root_name: str, idx: int) -> str:
    """按一级类目生成更像商品型号的后缀。"""
    model_tokens = MODEL_TOKENS_BY_ROOT.get(root_name)
    if not model_tokens:
        return ""
    return model_tokens[idx % len(model_tokens)]


def _build_spu_name(
    brand_name: str,
    series_word: str,
    product_word: str,
    root_name: str,
    idx: int,
) -> str:
    """生成 SPU 名称。"""
    model_token = _build_model_token(root_name, idx)
    if root_name in TECH_CATEGORY_ROOTS and model_token:
        return f"{brand_name} {series_word} {model_token} {product_word}"
    return f"{brand_name} {series_word} {product_word}"


def _build_spu_subtitle(rule: dict[str, Any], shop_name: str, idx: int) -> str:
    """生成 SPU 副标题。"""
    subtitle_word = rule["subtitle_words"][idx % len(rule["subtitle_words"])]
    return f"{subtitle_word}，{shop_name}精选好货"


def _is_sellable_category(category_name: str) -> bool:
    """过滤不适合直接作为 SPU 母体的三级类目。"""
    return not any(keyword in category_name for keyword in BLOCKED_CATEGORY_KEYWORDS)


def _pick_category_for_product(
    categories: list[dict[str, Any]],
    product_word: str,
    idx: int,
) -> dict[str, Any]:
    """优先选择与商品词更匹配的三级类目。"""
    matched = [
        row
        for row in categories
        if product_word in row["category_name"] or row["category_name"] in product_word
    ]
    candidates = matched or categories
    return candidates[idx % len(candidates)]


def _pick_shop_for_brand(
    shops: list[dict[str, Any]],
    brand_name: str,
    idx: int,
) -> dict[str, Any]:
    """优先选择品牌官方店，其次选择平台自营店。"""
    brand_shops = [
        row
        for row in shops
        if brand_name in row["shop_name"] or brand_name in row["seller_name"]
    ]
    if brand_shops:
        return brand_shops[idx % len(brand_shops)]

    self_operated_shops = [row for row in shops if row["is_self_operated"] == 1]
    if self_operated_shops:
        return self_operated_shops[idx % len(self_operated_shops)]

    return shops[idx % len(shops)]


def _build_weight_and_volume(root_name: str, idx: int) -> tuple[Decimal, Decimal]:
    """生成商品重量和体积。"""
    weight_base = {
        "手机通讯": Decimal("0.200"),
        "数码电子": Decimal("0.800"),
        "家用电器": Decimal("8.000"),
        "电脑办公": Decimal("1.800"),
        "服饰内衣": Decimal("0.400"),
        "鞋靴箱包": Decimal("0.900"),
        "美妆个护": Decimal("0.150"),
        "食品饮料": Decimal("0.500"),
        "母婴玩具": Decimal("0.700"),
        "家居家装": Decimal("2.500"),
        "运动户外": Decimal("1.000"),
        "汽车用品": Decimal("1.200"),
    }[root_name]
    volume_base = {
        "手机通讯": Decimal("0.002"),
        "数码电子": Decimal("0.006"),
        "家用电器": Decimal("0.120"),
        "电脑办公": Decimal("0.020"),
        "服饰内衣": Decimal("0.010"),
        "鞋靴箱包": Decimal("0.015"),
        "美妆个护": Decimal("0.003"),
        "食品饮料": Decimal("0.008"),
        "母婴玩具": Decimal("0.018"),
        "家居家装": Decimal("0.080"),
        "运动户外": Decimal("0.025"),
        "汽车用品": Decimal("0.030"),
    }[root_name]
    weight = weight_base + Decimal(idx % 7) * Decimal("0.035")
    volume = volume_base + Decimal(idx % 5) * Decimal("0.002")
    return (weight.quantize(Decimal("0.001")), volume.quantize(Decimal("0.001")))


def _build_on_shelf_time(start_date: date, end_date: date, idx: int) -> datetime:
    """生成商品上架时间。"""
    total_days = max((end_date - start_date).days, 1)
    day_offset = idx * total_days // SPU_TARGET_COUNT
    shelf_date = start_date + timedelta(days=day_offset)
    return datetime(
        shelf_date.year,
        shelf_date.month,
        shelf_date.day,
        10 + idx % 8,
        (idx * 7) % 60,
        0,
    )


def _build_first_visible_date(
    on_shelf_time: datetime,
    presale_start_time: datetime | None,
) -> date:
    """计算商品首次可见日期。"""
    if presale_start_time is not None:
        return min(on_shelf_time.date(), presale_start_time.date())
    return on_shelf_time.date()


def _build_spu_rows(
    start_date: date,
    end_date: date,
    brands: list[dict[str, Any]],
    categories_by_root: dict[str, list[dict[str, Any]]],
    shops_by_root: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """生成 SPU 基础信息。"""
    spu_rows: list[dict[str, Any]] = []

    for idx in range(SPU_TARGET_COUNT):
        brand = brands[idx % len(brands)]
        catalog = BRAND_PRODUCT_CATALOG[brand["brand_name"]]
        allowed_roots = [
            root
            for root in catalog["allowed_categories"]
            if root in categories_by_root and root in shops_by_root
        ]
        if not allowed_roots:
            raise ValueError(f"品牌 {brand['brand_name']} 没有可用的店铺与类目组合")

        root_name = allowed_roots[(idx // len(brands)) % len(allowed_roots)]
        categories = categories_by_root[root_name]
        shops = shops_by_root[root_name]
        rule = CATEGORY_NAMING_RULES[root_name]
        product_word = catalog["product_words"][idx % len(catalog["product_words"])]
        category = _pick_category_for_product(
            categories,
            product_word,
            idx * 7 + brand["brand_id"],
        )
        shop = _pick_shop_for_brand(
            shops,
            brand["brand_name"],
            idx * 5 + brand["brand_id"],
        )
        series_word = catalog["series_words"][idx % len(catalog["series_words"])]
        spu_name = _build_spu_name(
            brand["brand_name"],
            series_word,
            product_word,
            root_name,
            idx,
        )
        weight, volume = _build_weight_and_volume(root_name, idx)
        on_shelf_time = _build_on_shelf_time(start_date, end_date, idx)
        is_presale = 1 if idx % 12 == 0 else 0
        presale_start_time = None
        presale_end_time = None
        if is_presale:
            presale_start_time = on_shelf_time - timedelta(days=7)
            presale_end_time = on_shelf_time - timedelta(days=1)

        spu_rows.append(
            {
                "spu_id": 2_000_000 + idx + 1,
                "spu_name": spu_name,
                "spu_sub_title": _build_spu_subtitle(rule, shop["shop_name"], idx),
                "category_id": category["category_id"],
                "brand_id": brand["brand_id"],
                "brand_name": brand["brand_name"],
                "shop_id": shop["shop_id"],
                "is_virtual": 0,
                "is_presale": is_presale,
                "presale_start_time": presale_start_time,
                "presale_end_time": presale_end_time,
                "weight": weight,
                "volume": volume,
                "on_shelf_time": on_shelf_time,
                "_root_name": root_name,
                "_shop_name": shop["shop_name"],
                "_rule": rule,
            }
        )

    return spu_rows


def _build_attribute_combinations(rule: dict[str, Any]) -> list[dict[str, str]]:
    """生成 SKU 属性组合候选。"""
    attr_keys = rule["attribute_keys"]
    attr_values = [rule["attribute_values"][key] for key in attr_keys]
    combinations: list[dict[str, str]] = []
    max_count = max(len(values) for values in attr_values)
    for idx in range(max_count):
        combinations.append(
            {
                key: values[idx % len(values)]
                for key, values in zip(attr_keys, attr_values, strict=True)
            }
        )
    return combinations


def _build_price_values(
    price_range: list[int], idx: int
) -> tuple[Decimal, Decimal, Decimal]:
    """生成价格相关字段。"""
    min_price, max_price = price_range
    span = max_price - min_price
    sale_price = Decimal(min_price + (idx * 137) % max(span, 1))
    sale_price = sale_price.quantize(Decimal("0.01"))
    origin_price = (sale_price * Decimal("1.15")).quantize(Decimal("0.01"))
    cost_price = (sale_price * Decimal("0.68")).quantize(Decimal("0.01"))
    return origin_price, sale_price, cost_price


def _build_bar_code(spu_id: int, sku_idx: int) -> str:
    """生成 13 位商品条码。"""
    return f"{spu_id:09d}{sku_idx:04d}"[-13:]


def _close_current_version(
    versions: list[dict[str, Any]],
    change_date: date,
) -> dict[str, Any] | None:
    """关闭当前版本并返回新版本的基础拷贝。"""
    current = versions[-1]
    if change_date <= current["start_date"]:
        return None
    current["end_date"] = change_date - timedelta(days=1)
    current["is_current"] = 0
    next_version = dict(current)
    next_version["start_date"] = change_date
    next_version["end_date"] = FAR_FUTURE_DATE
    next_version["is_current"] = 1
    return next_version


def _build_spu_versions(
    base_row: dict[str, Any], idx: int, end_date: date
) -> list[dict[str, Any]]:
    """基于 SPU 基础信息生成拉链版本。"""
    first_visible_date = _build_first_visible_date(
        base_row["on_shelf_time"],
        base_row["presale_start_time"],
    )
    if first_visible_date > end_date:
        return []

    versions = [
        {
            "spu_id": base_row["spu_id"],
            "start_date": first_visible_date,
            "end_date": FAR_FUTURE_DATE,
            "spu_name": base_row["spu_name"],
            "spu_sub_title": base_row["spu_sub_title"],
            "category_id": base_row["category_id"],
            "brand_id": base_row["brand_id"],
            "brand_name": base_row["brand_name"],
            "shop_id": base_row["shop_id"],
            "is_virtual": base_row["is_virtual"],
            "is_presale": base_row["is_presale"],
            "presale_start_time": base_row["presale_start_time"],
            "presale_end_time": base_row["presale_end_time"],
            "weight": base_row["weight"],
            "volume": base_row["volume"],
            "on_shelf_time": base_row["on_shelf_time"],
            "is_current": 1,
        }
    ]

    on_shelf_date = base_row["on_shelf_time"].date()
    if base_row["is_presale"] == 1 and first_visible_date < on_shelf_date <= end_date:
        next_version = _close_current_version(versions, on_shelf_date)
        if next_version is not None:
            next_version["is_presale"] = 0
            next_version["presale_start_time"] = None
            next_version["presale_end_time"] = None
            versions.append(next_version)

    subtitle_change_date = on_shelf_date + timedelta(days=180 + idx % 60)
    if idx % 10 == 0 and subtitle_change_date <= end_date:
        next_version = _close_current_version(versions, subtitle_change_date)
        if next_version is not None:
            next_version["spu_sub_title"] = _build_spu_subtitle(
                base_row["_rule"],
                base_row["_shop_name"],
                idx + 3,
            )
            versions.append(next_version)

    return versions


def _build_sku_versions(
    base_row: dict[str, Any], idx: int, end_date: date
) -> list[dict[str, Any]]:
    """基于 SKU 基础信息生成拉链版本。"""
    first_visible_date = base_row["_first_visible_date"]
    if first_visible_date > end_date:
        return []

    versions = [
        {
            "sku_id": base_row["sku_id"],
            "start_date": first_visible_date,
            "end_date": FAR_FUTURE_DATE,
            "sku_name": base_row["sku_name"],
            "spu_id": base_row["spu_id"],
            "shop_id": base_row["shop_id"],
            "category_id": base_row["category_id"],
            "brand_id": base_row["brand_id"],
            "bar_code": base_row["bar_code"],
            "sku_specs_json": base_row["sku_specs_json"],
            "unit": base_row["unit"],
            "origin_price": base_row["origin_price"],
            "sale_price": base_row["sale_price"],
            "cost_price": base_row["cost_price"],
            "warning_stock": base_row["warning_stock"],
            "is_hot_sale": base_row["is_hot_sale"],
            "is_new": 1,
            "is_deleted": base_row["is_deleted"],
            "is_current": 1,
        }
    ]

    new_flag_change_date = first_visible_date + timedelta(days=90)
    if new_flag_change_date <= end_date:
        next_version = _close_current_version(versions, new_flag_change_date)
        if next_version is not None:
            next_version["is_new"] = 0
            versions.append(next_version)

    price_change_date = base_row["_on_shelf_date"] + timedelta(days=180 + idx % 90)
    if idx % 5 == 0 and price_change_date <= end_date:
        next_version = _close_current_version(versions, price_change_date)
        if next_version is not None:
            factor = Decimal("0.95") if idx % 2 == 0 else Decimal("1.03")
            next_version["sale_price"] = (next_version["sale_price"] * factor).quantize(
                Decimal("0.01")
            )
            next_version["origin_price"] = (
                next_version["sale_price"] * Decimal("1.15")
            ).quantize(Decimal("0.01"))
            next_version["cost_price"] = (
                next_version["sale_price"] * Decimal("0.68")
            ).quantize(Decimal("0.01"))
            next_version["is_hot_sale"] = 1
            versions.append(next_version)

    return versions


def run(ctx: RunContext) -> None:
    """生成并写入 SPU/SKU 维度拉链数据。"""
    logger.info("Run batch2_product_dims")
    metadata = MetaData()
    metadata.reflect(
        bind=ctx.engine,
        only=[
            "dwd_dim_category_info_df",
            "dwd_dim_brand_info_df",
            "dwd_dim_shop_info_df",
            "dwd_dim_spu_info_df",
            "dwd_dim_sku_info_df",
        ],
    )
    category_table = metadata.tables["dwd_dim_category_info_df"]
    brand_table = metadata.tables["dwd_dim_brand_info_df"]
    shop_table = metadata.tables["dwd_dim_shop_info_df"]
    spu_table = metadata.tables["dwd_dim_spu_info_df"]
    sku_table = metadata.tables["dwd_dim_sku_info_df"]

    with ctx.engine.begin() as conn:
        if _has_rows(conn, spu_table) or _has_rows(conn, sku_table):
            logger.info("SPU/SKU tables already contain data, skip batch2 generation")
            return

        logger.info("batch2 loading source rows")
        category_rows = _load_current_rows(conn, category_table)
        brand_rows = _load_current_rows(conn, brand_table)
        shop_rows = _load_current_rows(conn, shop_table)
        logger.info(
            "batch2 loaded source rows: category_rows={} brand_rows={} shop_rows={}",
            len(category_rows),
            len(brand_rows),
            len(shop_rows),
        )

        level1_categories = {
            row["category_name"]
            for row in category_rows
            if row["category_level"] == "一级"
        }
        brand_names = {row["brand_name"] for row in brand_rows}
        _validate_catalogs(level1_categories, brand_names)

        categories_by_root: dict[str, list[dict[str, Any]]] = {}
        for row in category_rows:
            if row["category_level"] != "三级":
                continue
            if not _is_sellable_category(row["category_name"]):
                continue
            categories_by_root.setdefault(row["root_category_name"], []).append(row)

        shops_by_root: dict[str, list[dict[str, Any]]] = {}
        for row in shop_rows:
            shops_by_root.setdefault(row["industry_type"], []).append(row)

        available_brand_rows = []
        for row in brand_rows:
            allowed_roots = [
                root
                for root in BRAND_PRODUCT_CATALOG[row["brand_name"]][
                    "allowed_categories"
                ]
                if root in categories_by_root and root in shops_by_root
            ]
            if allowed_roots:
                available_brand_rows.append(row)

        if not available_brand_rows:
            raise ValueError("没有可用于生成 SPU/SKU 的品牌、类目、店铺组合")

        start_date = date.fromisoformat(ctx.gen.start_date)
        end_date = date.fromisoformat(ctx.gen.end_date)
        spu_base_rows = _build_spu_rows(
            start_date,
            end_date,
            available_brand_rows,
            categories_by_root,
            shops_by_root,
        )
        if len(spu_base_rows) != SPU_TARGET_COUNT:
            raise ValueError(f"SPU 生成数量异常: {len(spu_base_rows)}")

        spu_version_rows: list[dict[str, Any]] = []
        spu_first_visible_date: dict[int, date] = {}
        for idx, spu_row in enumerate(spu_base_rows):
            first_visible_date = _build_first_visible_date(
                spu_row["on_shelf_time"],
                spu_row["presale_start_time"],
            )
            spu_first_visible_date[spu_row["spu_id"]] = first_visible_date
            spu_version_rows.extend(_build_spu_versions(spu_row, idx, end_date))

        category_to_root = {
            row["category_id"]: row["root_category_name"]
            for row in category_rows
            if row["category_level"] == "三级"
        }
        sku_base_rows: list[dict[str, Any]] = []
        for spu_idx, spu_row in enumerate(spu_base_rows):
            root_name = category_to_root[spu_row["category_id"]]
            rule = CATEGORY_NAMING_RULES[root_name]
            combinations = _build_attribute_combinations(rule)
            for variant_idx in range(SKU_PER_SPU):
                specs = combinations[variant_idx % len(combinations)]
                spec_suffix = " ".join(specs.values())
                origin_price, sale_price, cost_price = _build_price_values(
                    rule["price_range"],
                    spu_idx * SKU_PER_SPU + variant_idx,
                )
                sku_base_rows.append(
                    {
                        "sku_id": 3_000_000 + spu_idx * SKU_PER_SPU + variant_idx + 1,
                        "sku_name": f"{spu_row['spu_name']} {spec_suffix}",
                        "spu_id": spu_row["spu_id"],
                        "shop_id": spu_row["shop_id"],
                        "category_id": spu_row["category_id"],
                        "brand_id": spu_row["brand_id"],
                        "bar_code": _build_bar_code(spu_row["spu_id"], variant_idx + 1),
                        "sku_specs_json": specs,
                        "unit": UNIT_BY_ROOT.get(root_name, "件"),
                        "origin_price": origin_price,
                        "sale_price": sale_price,
                        "cost_price": cost_price,
                        "warning_stock": 10 + (spu_idx + variant_idx) % 40,
                        "is_hot_sale": 1 if (spu_idx + variant_idx) % 9 == 0 else 0,
                        "is_deleted": 0,
                        "_first_visible_date": spu_first_visible_date[
                            spu_row["spu_id"]
                        ],
                        "_on_shelf_date": spu_row["on_shelf_time"].date(),
                    }
                )

        if len(sku_base_rows) != SKU_TARGET_COUNT:
            raise ValueError(f"SKU 生成数量异常: {len(sku_base_rows)}")

        sku_version_rows: list[dict[str, Any]] = []
        for idx, sku_row in enumerate(sku_base_rows):
            sku_version_rows.extend(_build_sku_versions(sku_row, idx, end_date))

        inserted_spu_rows = bulk_insert(
            conn,
            spu_table,
            spu_version_rows,
            batch_size=ctx.gen.batch_size,
        )
        inserted_sku_rows = bulk_insert(
            conn,
            sku_table,
            sku_version_rows,
            batch_size=ctx.gen.batch_size,
        )

    logger.info(
        "Generated batch2 product dimensions: spu_base_rows={}, sku_base_rows={}, spu_version_rows={}, sku_version_rows={}",
        len(spu_base_rows),
        len(sku_base_rows),
        inserted_spu_rows,
        inserted_sku_rows,
    )
