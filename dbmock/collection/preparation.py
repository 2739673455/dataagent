"""综合电商真实商品采集结果的标准化、生成与校验"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .source import (
    CATEGORY_PRICE_RANGES,
    UI_ARTIFACT_PATTERN,
    CatalogProduct,
    CatalogSku,
    prepare_source,
    sha256,
)

ARTIFACT_NAMES = (
    "categories.json",
    "brands.json",
    "shops.json",
    "spus.jsonl",
    "skus.jsonl",
    "lineage.jsonl",
)

PERSISTED_SOURCE_METADATA_FIELDS = (
    "captured_at",
    "target_spu_count",
    "selected_spus",
    "selected_skus",
    "rejected_products",
    "rejected_records",
    "rejection_reason_distribution",
    "removed_ui_artifact_specs",
    "derived_single_sku_specs",
    "cached_spus",
    "sha256",
    "selection_group_distribution",
    "request_delay_seconds",
    "price_region_code",
    "robots_checked_at",
    "robots_sha256",
    "source_fields_required",
)
FIELD_LINEAGE = {
    "categories.json.category_id": {
        "classification": "derived",
        "rule": "CATEGORY命名空间与标准类目路径的SHA-256稳定映射",
    },
    "categories.json.category_name": {
        "classification": "normalized",
        "source": "lineage.origin_category_path",
        "rule": "来源类目路径映射至平台三级类目",
    },
    "brands.json.brand_id": {
        "classification": "derived",
        "rule": "BRAND命名空间与标准品牌键的SHA-256稳定映射",
    },
    "brands.json.brand_name": {
        "classification": "normalized",
        "source": "lineage.origin_brand_name",
        "rule": "NFKC标准化后按同义名称出现频次选取展示名",
    },
    "shops.json.shop_id": {
        "classification": "derived",
        "rule": "SHOP命名空间与来源店铺键的SHA-256稳定映射",
    },
    "shops.json.shop_name": {
        "classification": "normalized",
        "source": "lineage.origin_store_name",
        "rule": "去除来源平台名称并统一平台自营店展示名",
    },
    "spus.jsonl.spu_id": {
        "classification": "derived",
        "rule": "SPU命名空间与来源商品关系键的SHA-256稳定映射",
    },
    "spus.jsonl.spu_name": {
        "classification": "normalized",
        "source": "lineage.origin_product_name",
        "rule": "从来源标题中剔除当前SKU规格并清理空白",
    },
    "spus.jsonl.weight_kg": {
        "classification": "normalized",
        "source": "lineage.origin_weight",
        "rule": "仅对来源明确数值执行千克单位标准化，缺失保持null",
    },
    "spus.jsonl.volume_m3": {
        "classification": "observed",
        "source": "lineage.origin_volume",
        "rule": "来源未公开时保持null",
    },
    "skus.jsonl.sku_id": {
        "classification": "derived",
        "rule": "SKU命名空间与来源SKU关系键的SHA-256稳定映射",
    },
    "skus.jsonl.sku_name": {
        "classification": "normalized",
        "source": "lineage.skus.origin_sku_title",
        "rule": "标准SPU名称与已清洗真实规格拼接",
    },
    "skus.jsonl.sku_specs_json": {
        "classification": "normalized",
        "source": "lineage.skus.origin_specs",
        "rule": "清除页面控件；真实单规格商品显式写为规格=单规格",
    },
    "lineage.jsonl.origin_*": {
        "classification": "observed",
        "source": "公开商品页面",
        "rule": "保留来源值、页面URL和抓取时间",
    },
}

FIELD_LINEAGE.update(
    {
        f"categories.json.{field}": {
            "classification": "derived",
            "source": "lineage.origin_category_path",
            "rule": "由平台三级类目树结构计算",
        }
        for field in (
            "category_level",
            "parent_category_id",
            "parent_category_name",
            "root_category_id",
            "root_category_name",
            "is_leaf",
            "sort_order",
            "category_path",
            "status",
        )
    }
)
FIELD_LINEAGE.update(
    {
        f"brands.json.{field}": {
            "classification": "normalized",
            "source": "lineage.origin_brand_name",
            "rule": "来源未公开或无法可靠映射时保持null",
        }
        for field in (
            "brand_name_en",
            "brand_alias",
            "brand_logo_url",
            "brand_story",
            "country_code",
            "country_name",
            "first_letter",
            "status",
        )
    }
)
FIELD_LINEAGE.update(
    {
        f"shops.json.{field}": {
            "classification": "normalized",
            "source": "lineage.origin_store_name",
            "rule": "公开店铺属性标准化，来源未公开时保持null",
        }
        for field in (
            "shop_type",
            "seller_id",
            "seller_name",
            "industry_type",
            "service_score",
            "logistics_score",
            "description_score",
            "open_time",
            "province_code",
            "city_code",
            "district_code",
            "is_self_operated",
            "is_cross_border",
            "is_deleted",
            "shop_status",
        )
    }
)
FIELD_LINEAGE.update(
    {
        "brands.json.status": {
            "classification": "derived",
            "rule": "进入有效目录的品牌状态设为启用",
        },
    }
)
FIELD_LINEAGE.update(
    {
        f"spus.jsonl.{field}": {
            "classification": "derived",
            "source": "lineage.jsonl",
            "rule": "由采集商品关系及平台标准维度映射生成",
        }
        for field in (
            "spu_sub_title",
            "category_id",
            "brand_id",
            "shop_id",
            "is_virtual",
            "is_presale",
            "spu_status",
        )
    }
)
FIELD_LINEAGE.update(
    {
        f"skus.jsonl.{field}": {
            "classification": "derived",
            "source": "lineage.jsonl",
            "rule": "由来源SKU关系及平台标准维度映射生成",
        }
        for field in (
            "spu_id",
            "shop_id",
            "category_id",
            "brand_id",
            "bar_code",
            "unit",
            "sku_status",
        )
    }
)
FIELD_LINEAGE.update(
    {
        "spus.jsonl.spu_sub_title": {
            "classification": "observed",
            "source": "lineage.origin_product_subtitle",
            "rule": "来源未公开副标题时保持null",
        },
        "spus.jsonl.is_presale": {
            "classification": "observed",
            "source": "lineage.origin_attributes",
            "rule": "来源未明确公开时保持null",
        },
        "skus.jsonl.bar_code": {
            "classification": "observed",
            "source": "lineage.skus",
            "rule": "来源未公开时保持null",
        },
        "skus.jsonl.unit": {
            "classification": "observed",
            "source": "lineage.skus",
            "rule": "来源未公开时保持null",
        },
        "brands.json.brand_name_en": {
            "classification": "observed",
            "source": "lineage.origin_brand_name",
            "rule": "来源未单独公开英文品牌名时保持null",
        },
        "brands.json.brand_alias": {
            "classification": "observed",
            "source": "lineage.origin_brand_name",
            "rule": "来源未单独公开品牌别名时保持null",
        },
        "brands.json.brand_logo_url": {
            "classification": "observed",
            "source": "lineage.origin_attributes",
            "rule": "来源未公开品牌Logo时保持null",
        },
        "brands.json.brand_story": {
            "classification": "observed",
            "source": "lineage.origin_attributes",
            "rule": "来源未公开品牌故事时保持null",
        },
        "brands.json.country_code": {
            "classification": "observed",
            "source": "lineage.origin_attributes",
            "rule": "来源未可靠公开品牌国家时保持null",
        },
        "brands.json.country_name": {
            "classification": "observed",
            "source": "lineage.origin_attributes",
            "rule": "来源未可靠公开品牌国家时保持null",
        },
        "brands.json.first_letter": {
            "classification": "derived",
            "source": "brands.json.brand_name",
            "rule": "ASCII品牌名取首字母，其余写为#",
        },
        "shops.json.shop_type": {
            "classification": "derived",
            "source": "lineage.origin_is_self_operated",
            "rule": "按来源自营标记映射为自营店或第三方店铺",
        },
        "shops.json.seller_id": {
            "classification": "derived",
            "source": "lineage.external_store_id",
            "rule": "SELLER命名空间与来源店铺键的SHA-256稳定映射",
        },
        "shops.json.seller_name": {
            "classification": "normalized",
            "source": "lineage.origin_store_name",
            "rule": "第三方沿用标准店铺名，自营统一为平台自营",
        },
        "shops.json.industry_type": {
            "classification": "derived",
            "source": "lineage.origin_category_path",
            "rule": "取店铺商品数最多的平台一级类目",
        },
        **{
            f"shops.json.{field}": {
                "classification": "observed",
                "source": "lineage.origin_store_name",
                "rule": "商品页未公开可验证的店铺属性时保持null",
            }
            for field in (
                "service_score",
                "logistics_score",
                "description_score",
                "open_time",
                "province_code",
                "city_code",
                "district_code",
            )
        },
        "shops.json.is_self_operated": {
            "classification": "normalized",
            "source": "lineage.origin_is_self_operated",
            "rule": "来源布尔值标准化为0或1",
        },
        "shops.json.is_cross_border": {
            "classification": "normalized",
            "source": "lineage.origin_is_cross_border",
            "rule": "同一来源店铺存在跨境商品时标准化为1",
        },
        "shops.json.is_deleted": {
            "classification": "derived",
            "rule": "进入有效目录的店铺设为未删除",
        },
        "shops.json.shop_status": {
            "classification": "derived",
            "rule": "进入有效目录的店铺设为营业",
        },
    }
)


def _clean_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _identity_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def _stable_id(namespace: str, business_key: str) -> int:
    material = f"{namespace}:{business_key}".encode()
    return int(hashlib.sha256(material).hexdigest()[:15], 16) + 1


def _spu_id(product: CatalogProduct) -> int:
    return _stable_id("SPU", product.source_key)


def _sku_id(sku: CatalogSku) -> int:
    return _stable_id("SKU", sku.source_key)


def _business_store_name(value: str) -> str:
    normalized = re.sub(r"苏宁易购|苏宁", "", value)
    return _clean_text(normalized, 128) or "第三方店铺"


def _business_lineage_keys(row: Mapping[str, Any]) -> set[str]:
    legacy_keys = {
        "source_parent_asin",
        "source_listing_key",
        "source_price_usd",
        "source_system",
    }
    return {
        key
        for key in row
        if key.startswith(("origin_", "external_")) or key in legacy_keys
    }


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with temporary.open("w", encoding="utf-8") as target:
        for row in rows:
            target.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            target.write("\n")
            count += 1
    os.replace(temporary, path)
    return count


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"JSONL行不是对象: {path}:{line_number}")
            yield row


def _build_categories(
    products: list[CatalogProduct],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    normalized_paths = {
        product.source_key: (
            product.root_category,
            product.second_category,
            product.leaf_category,
        )
        for product in products
    }
    paths = set(normalized_paths.values())
    roots = sorted({path[0] for path in paths})
    output: list[dict[str, Any]] = []
    leaf_ids: dict[tuple[str, str, str], int] = {}

    for root_order, root in enumerate(roots, start=1):
        root_id = _stable_id("CATEGORY", root)
        output.append(
            {
                "category_id": root_id,
                "category_name": root,
                "category_level": "一级",
                "parent_category_id": None,
                "parent_category_name": None,
                "root_category_id": root_id,
                "root_category_name": root,
                "is_leaf": 0,
                "sort_order": root_order,
                "category_path": root,
                "status": 1,
            }
        )
        second_categories = sorted({path[1] for path in paths if path[0] == root})
        for second_order, second in enumerate(second_categories, start=1):
            second_id = _stable_id("CATEGORY", f"{root}/{second}")
            output.append(
                {
                    "category_id": second_id,
                    "category_name": second,
                    "category_level": "二级",
                    "parent_category_id": root_id,
                    "parent_category_name": root,
                    "root_category_id": root_id,
                    "root_category_name": root,
                    "is_leaf": 0,
                    "sort_order": second_order,
                    "category_path": f"{root}/{second}",
                    "status": 1,
                }
            )
            leaves = sorted(
                {path[2] for path in paths if path[0] == root and path[1] == second}
            )
            for leaf_order, leaf in enumerate(leaves, start=1):
                leaf_id = _stable_id("CATEGORY", f"{root}/{second}/{leaf}")
                output.append(
                    {
                        "category_id": leaf_id,
                        "category_name": leaf,
                        "category_level": "三级",
                        "parent_category_id": second_id,
                        "parent_category_name": second,
                        "root_category_id": root_id,
                        "root_category_name": root,
                        "is_leaf": 1,
                        "sort_order": leaf_order,
                        "category_path": f"{root}/{second}/{leaf}",
                        "status": 1,
                    }
                )
                leaf_ids[(root, second, leaf)] = leaf_id
    category_ids = {
        source_key: leaf_ids[path] for source_key, path in normalized_paths.items()
    }
    return output, category_ids


def _build_brands_and_shops(
    products: list[CatalogProduct],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, int],
    dict[str, int],
]:
    brand_names: dict[str, Counter[str]] = defaultdict(Counter)
    store_names: dict[str, Counter[str]] = defaultdict(Counter)
    store_roots: dict[str, Counter[str]] = defaultdict(Counter)
    store_self_operated: dict[str, bool] = {}
    store_cross_border: dict[str, bool] = {}
    brand_keys_by_product: dict[str, str] = {}
    store_keys_by_product: dict[str, str] = {}
    for product in products:
        brand_key = _identity_key(product.brand)
        if not brand_key:
            raise ValueError(f"品牌名称无法标准化: {product.brand}")
        brand_keys_by_product[product.source_key] = brand_key
        brand_names[brand_key][product.brand] += 1

        store_key = (
            "PLATFORM_SELF"
            if product.is_self_operated
            else f"{product.origin_platform}:{product.source_store_id}"
        )
        store_keys_by_product[product.source_key] = store_key
        business_store_name = (
            "平台自营店"
            if product.is_self_operated
            else _business_store_name(product.store)
        )
        store_names[store_key][business_store_name] += 1
        store_roots[store_key][product.root_category] += 1
        store_self_operated[store_key] = product.is_self_operated
        store_cross_border[store_key] = (
            False
            if product.is_self_operated
            else (store_cross_border.get(store_key, False) or product.is_cross_border)
        )

    brands = []
    brand_ids_by_key = {}
    for brand_key in sorted(brand_names):
        variants = brand_names[brand_key]
        brand_name = min(
            variants,
            key=lambda name: (-variants[name], len(name), name.casefold()),
        )
        brand_id = _stable_id("BRAND", brand_key)
        brand_ids_by_key[brand_key] = brand_id
        first_letter = brand_name[0].upper() if brand_name[0].isascii() else "#"
        brands.append(
            {
                "brand_id": brand_id,
                "brand_name": brand_name,
                "brand_name_en": None,
                "brand_alias": None,
                "brand_logo_url": None,
                "brand_story": None,
                "country_code": None,
                "country_name": None,
                "first_letter": first_letter,
                "status": 1,
            }
        )
    brand_ids = {
        product.source_key: brand_ids_by_key[brand_keys_by_product[product.source_key]]
        for product in products
    }

    shops = []
    shop_ids_by_key = {}
    for store_key in sorted(store_names):
        variants = store_names[store_key]
        shop_name = min(
            variants,
            key=lambda name: (-variants[name], len(name), name.casefold()),
        )
        shop_id = _stable_id("SHOP", store_key)
        seller_id = _stable_id("SELLER", store_key)
        shop_ids_by_key[store_key] = shop_id
        is_self_operated = store_self_operated[store_key]
        shops.append(
            {
                "shop_id": shop_id,
                "shop_name": shop_name,
                "shop_type": "自营店" if is_self_operated else "第三方店铺",
                "seller_id": seller_id,
                "seller_name": "平台自营" if is_self_operated else shop_name,
                "industry_type": store_roots[store_key].most_common(1)[0][0],
                "service_score": None,
                "logistics_score": None,
                "description_score": None,
                "open_time": None,
                "province_code": None,
                "city_code": None,
                "district_code": None,
                "is_self_operated": int(is_self_operated),
                "is_cross_border": int(store_cross_border[store_key]),
                "is_deleted": 0,
                "shop_status": "营业",
            }
        )
    shop_ids = {
        product.source_key: shop_ids_by_key[store_keys_by_product[product.source_key]]
        for product in products
    }
    return brands, shops, brand_ids, shop_ids


def _display_name(product: CatalogProduct) -> str:
    return _clean_text(product.spu_name, 256)


def _spu_rows(
    products: list[CatalogProduct],
    category_ids: Mapping[str, int],
    brand_ids: Mapping[str, int],
    shop_ids: Mapping[str, int],
) -> Iterator[dict[str, Any]]:
    for product in products:
        yield {
            "spu_id": _spu_id(product),
            "spu_name": _display_name(product),
            "spu_sub_title": product.subtitle,
            "category_id": category_ids[product.source_key],
            "brand_id": brand_ids[product.source_key],
            "shop_id": shop_ids[product.source_key],
            "is_virtual": 0,
            "is_presale": None,
            "weight_kg": product.weight_kg,
            "volume_m3": product.volume_m3,
            "spu_status": "在售",
        }


def _sku_rows(
    products: list[CatalogProduct],
    category_ids: Mapping[str, int],
    brand_ids: Mapping[str, int],
    shop_ids: Mapping[str, int],
) -> Iterator[dict[str, Any]]:
    for product in products:
        spu_id = _spu_id(product)
        for sku in product.skus:
            yield {
                "sku_id": _sku_id(sku),
                "sku_name": sku.title,
                "spu_id": spu_id,
                "shop_id": shop_ids[product.source_key],
                "category_id": category_ids[product.source_key],
                "brand_id": brand_ids[product.source_key],
                "bar_code": None,
                "sku_specs_json": sku.specs,
                "unit": None,
                "sku_status": "在售",
            }


def _lineage_rows(
    products: list[CatalogProduct],
    category_ids: Mapping[str, int],
    brand_ids: Mapping[str, int],
    shop_ids: Mapping[str, int],
) -> Iterator[dict[str, Any]]:
    for product in products:
        spu_id = _spu_id(product)
        yield {
            "spu_id": spu_id,
            "category_id": category_ids[product.source_key],
            "brand_id": brand_ids[product.source_key],
            "shop_id": shop_ids[product.source_key],
            "origin_product_key": product.source_key.partition(":")[2],
            "external_product_id": product.external_product_id,
            "origin_product_name": product.title,
            "origin_product_subtitle": product.subtitle,
            "origin_brand_name": product.brand,
            "external_brand_id": product.source_brand_id,
            "origin_store_name": product.store,
            "external_store_id": product.source_store_id,
            "origin_is_self_operated": product.is_self_operated,
            "origin_is_cross_border": product.is_cross_border,
            "origin_category_code": product.source_category,
            "origin_category_ids": list(product.source_category_ids),
            "origin_category_path": list(product.source_category_path),
            "origin_attributes": product.attributes,
            "origin_main_image_url": product.main_image_url,
            "origin_review_count": product.review_count,
            "origin_weight": product.source_weight,
            "origin_volume": product.source_volume,
            "origin_url": product.source_url,
            "origin_captured_at": product.captured_at,
            "skus": [
                {
                    "sku_id": _sku_id(sku),
                    "external_sku_id": sku.external_sku_id,
                    "origin_sku_key": sku.source_key.partition(":")[2],
                    "origin_sku_title": sku.title,
                    "origin_specs": sku.origin_specs,
                    "normalized_specs": sku.specs,
                    "specs_provenance": sku.specs_provenance,
                    "origin_sale_price_cny": sku.sale_price_cny,
                    "origin_list_price_cny": sku.list_price_cny,
                    "origin_price_region_code": sku.price_region_code,
                    "origin_main_image_url": sku.image_url or product.main_image_url,
                    "origin_url": sku.source_url,
                    "origin_captured_at": product.captured_at,
                    "variant_no": variant_index + 1,
                }
                for variant_index, sku in enumerate(product.skus)
            ],
        }


def prepare_catalog(
    data_dir: Path,
    *,
    target_spu_count: int = 5_000,
    force_download: bool = False,
    crawl_delay_seconds: float = 0.5,
) -> dict[str, Any]:
    if target_spu_count <= 0:
        raise ValueError("SPU 目标数量必须大于 0")

    _source_paths, source_metadata, products = prepare_source(
        data_dir,
        target_spu_count,
        force=force_download,
        delay_seconds=crawl_delay_seconds,
    )
    categories, category_ids = _build_categories(products)
    brands, shops, brand_ids, shop_ids = _build_brands_and_shops(products)
    root_distribution = Counter(product.root_category for product in products)
    brand_distribution = Counter(product.brand for product in products)
    store_distribution = Counter(product.store for product in products)
    self_operated_spus = sum(product.is_self_operated for product in products)
    sku_counts_per_spu = [len(product.skus) for product in products]
    source_entries = []
    for metadata in source_metadata["sources"]:
        source_entries.append(
            {
                field_name: metadata[field_name]
                for field_name in PERSISTED_SOURCE_METADATA_FIELDS
                if field_name in metadata
            }
        )

    data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".catalog-", dir=data_dir) as temporary:
        staging = Path(temporary)
        _atomic_json(staging / "categories.json", categories)
        _atomic_json(staging / "brands.json", brands)
        _atomic_json(staging / "shops.json", shops)
        spu_count = _atomic_jsonl(
            staging / "spus.jsonl",
            _spu_rows(products, category_ids, brand_ids, shop_ids),
        )
        sku_count = _atomic_jsonl(
            staging / "skus.jsonl",
            _sku_rows(
                products,
                category_ids,
                brand_ids,
                shop_ids,
            ),
        )
        _atomic_jsonl(
            staging / "lineage.jsonl",
            _lineage_rows(products, category_ids, brand_ids, shop_ids),
        )
        manifest = {
            "generated_at": datetime.now(UTC).isoformat(),
            "catalog_name": "综合电商商品目录",
            "source": {
                "captured_at": source_metadata["captured_at"],
                "origins": source_entries,
            },
            "selection": {
                "strategy": "按目标综合电商画像采样公开商品供给，保留每个SPU清洗后的真实SKU",
                "request_delay_seconds": source_metadata["request_delay_seconds"],
                "eligible_rows": len(products),
                "source_brand_names": len(
                    {(product.origin_platform, product.brand) for product in products}
                ),
                "source_store_names": len(
                    {(product.origin_platform, product.store) for product in products}
                ),
                "root_category_distribution": dict(root_distribution),
                "selection_group_distribution": source_metadata[
                    "selection_group_distribution"
                ],
                "self_operated_spu_share": self_operated_spus / len(products),
                "largest_brand_spu_share": (
                    brand_distribution.most_common(1)[0][1] / len(products)
                ),
                "largest_store_spu_share": (
                    store_distribution.most_common(1)[0][1] / len(products)
                ),
                "rejected_products": sum(
                    int(source.get("rejected_products", 0))
                    for source in source_metadata["sources"]
                ),
                "rejected_records": sum(
                    int(source.get("rejected_records", 0))
                    for source in source_metadata["sources"]
                ),
                "rejection_reason_distribution": dict(
                    sum(
                        (
                            Counter(source.get("rejection_reason_distribution", {}))
                            for source in source_metadata["sources"]
                        ),
                        Counter(),
                    )
                ),
                "removed_ui_artifact_specs": sum(
                    int(source.get("removed_ui_artifact_specs", 0))
                    for source in source_metadata["sources"]
                ),
                "derived_single_sku_specs": sum(
                    int(source.get("derived_single_sku_specs", 0))
                    for source in source_metadata["sources"]
                ),
            },
            "counts": {
                "spus": spu_count,
                "skus": sku_count,
                "average_skus_per_spu": sku_count / spu_count,
                "min_skus_per_spu": min(sku_counts_per_spu),
                "max_skus_per_spu": max(sku_counts_per_spu),
                "categories": len(categories),
                "root_categories": len(root_distribution),
                "brands": len(brands),
                "shops": len(shops),
            },
            "lineage": {
                "field_lineage": FIELD_LINEAGE,
                "origin_fields": [
                    "external_product_id",
                    "external_sku_id",
                    "external_brand_id",
                    "external_store_id",
                    "origin_is_self_operated",
                    "origin_is_cross_border",
                    "origin_url",
                    "origin_captured_at",
                    "原始标题、类目、参数、价格和图片",
                ],
                "derived_fields": [
                    "由来源业务键稳定派生的平台SPU和SKU业务ID",
                    "由标准化业务键稳定派生的平台品牌、店铺和商家业务ID",
                    "品牌名称标准化",
                    "平台三级类目映射",
                    "外部自营商品归入平台自营店",
                    "第三方商家名称使用公开店铺名称",
                    "由真实商品标题和真实规格拼接的SKU展示名",
                    "来源未提供SKU专属图片时引用同一真实商品主图",
                    "实体商品类目用于推导is_virtual=0",
                    "公开页面和有效售价用于推导当前在售状态",
                ],
                "missing_fields": [
                    "未公开的条码",
                    "来源页未公开的外部品牌和店铺ID",
                    "未公开的SKU计量单位和商品上架时间",
                    "未公开的商品预售状态",
                    "未公开的成本价",
                    "未公开的实时库存数量",
                    "商品页未提供时的重量和体积",
                    "店铺未公开的商家国家、开店时间和行政区",
                ],
            },
            "artifacts": {
                name: {"sha256": sha256(staging / name)} for name in ARTIFACT_NAMES
            },
        }
        _atomic_json(staging / "manifest.json", manifest)
        validate_catalog(staging)
        for name in (*ARTIFACT_NAMES, "manifest.json"):
            os.replace(staging / name, data_dir / name)
    shutil.rmtree(data_dir / "source")
    return manifest


def _load_json_array(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(
        isinstance(row, dict) for row in payload
    ):
        raise ValueError(f"文件不是对象数组: {path}")
    return payload


def validate_catalog(data_dir: Path) -> dict[str, int]:
    manifest_path = data_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"真实商品目录尚未准备: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest["counts"]
    field_lineage = manifest.get("lineage", {}).get("field_lineage")
    if field_lineage != FIELD_LINEAGE:
        raise ValueError("商品目录字段级血缘声明不完整")
    for field_name, declaration in field_lineage.items():
        classification = declaration.get("classification")
        if classification not in {"observed", "normalized", "derived"}:
            raise ValueError(
                f"商品目录字段血缘分类无效: {field_name}={classification}"
            )
    categories = _load_json_array(data_dir / "categories.json")
    brands = _load_json_array(data_dir / "brands.json")
    shops = _load_json_array(data_dir / "shops.json")
    business_artifacts = {
        "categories.json": categories[0],
        "brands.json": brands[0],
        "shops.json": shops[0],
        "spus.jsonl": next(_iter_jsonl(data_dir / "spus.jsonl")),
        "skus.jsonl": next(_iter_jsonl(data_dir / "skus.jsonl")),
    }
    missing_lineage_fields = [
        f"{artifact}.{field}"
        for artifact, sample in business_artifacts.items()
        for field in sample
        if f"{artifact}.{field}" not in field_lineage
    ]
    if missing_lineage_fields:
        raise ValueError(
            "业务目录字段缺少机器血缘: " + ", ".join(missing_lineage_fields)
        )

    category_ids = {int(row["category_id"]) for row in categories}
    brand_ids = {int(row["brand_id"]) for row in brands}
    shop_ids = {int(row["shop_id"]) for row in shops}
    if len(category_ids) != len(categories):
        raise ValueError("类目业务ID不唯一")
    if len(brand_ids) != len(brands):
        raise ValueError("品牌业务ID不唯一")
    if len(shop_ids) != len(shops):
        raise ValueError("店铺业务ID不唯一")
    allowed_shop_types = {"自营店", "第三方店铺", "旗舰店", "专卖店", "专营店"}
    for shop in shops:
        lineage_keys = _business_lineage_keys(shop)
        if lineage_keys:
            raise ValueError(f"店铺业务文件包含血缘字段: {sorted(lineage_keys)}")
        if shop.get("shop_type") not in allowed_shop_types:
            raise ValueError(f"店铺类型不是平台业务属性: {shop.get('shop_type')}")
        if any(key in shop for key in ("seller_name_source", "source_system")):
            raise ValueError(f"店铺包含遗留字段: {shop.get('shop_id')}")
        if re.search(r"苏宁", str(shop.get("shop_name") or "")):
            raise ValueError(f"店铺业务名称包含外部平台: {shop.get('shop_id')}")

    category_by_id = {int(row["category_id"]): row for row in categories}
    for category in categories:
        lineage_keys = _business_lineage_keys(category)
        if lineage_keys:
            raise ValueError(f"类目业务文件包含血缘字段: {sorted(lineage_keys)}")
        parent_id = category.get("parent_category_id")
        if parent_id is not None and int(parent_id) not in category_ids:
            raise ValueError(f"类目父节点不存在: {category['category_id']}")
    root_names = {
        str(row["category_name"])
        for row in categories
        if row.get("category_level") == "一级"
    }
    for brand in brands:
        lineage_keys = _business_lineage_keys(brand)
        if lineage_keys:
            raise ValueError(f"品牌业务文件包含血缘字段: {sorted(lineage_keys)}")

    lineage_spu_ids: set[int] = set()
    lineage_spu_refs: dict[int, tuple[int, int, int]] = {}
    origin_keys: set[str] = set()
    lineage_sku_refs: dict[int, int] = {}
    origin_sku_keys: set[str] = set()
    self_operated_spus = 0
    for row in _iter_jsonl(data_dir / "lineage.jsonl"):
        spu_id = int(row["spu_id"])
        if spu_id in lineage_spu_ids:
            raise ValueError(f"血缘SPU业务ID重复: {spu_id}")
        origin_key = _clean_text(row.get("origin_product_key"), 256)
        required_text = {
            "origin_product_key": origin_key,
            "external_product_id": row.get("external_product_id"),
            "origin_product_name": row.get("origin_product_name"),
            "origin_brand_name": row.get("origin_brand_name"),
            "origin_store_name": row.get("origin_store_name"),
            "origin_category_code": row.get("origin_category_code"),
            "origin_main_image_url": row.get("origin_main_image_url"),
            "origin_url": row.get("origin_url"),
            "origin_captured_at": row.get("origin_captured_at"),
        }
        missing_fields = [
            key for key, value in required_text.items() if not _clean_text(value, 1000)
        ]
        if missing_fields:
            raise ValueError(f"商品血缘核心字段缺失: {spu_id} {missing_fields}")
        if not isinstance(row.get("origin_is_self_operated"), bool) or not isinstance(
            row.get("origin_is_cross_border"), bool
        ):
            raise ValueError(f"商品来源店铺标记无效: {spu_id}")
        if origin_key in origin_keys:
            raise ValueError(f"原始商品关系键重复: {origin_key}")
        source_category_path = row.get("origin_category_path")
        if (
            not isinstance(source_category_path, list)
            or len(source_category_path) < 2
            or not all(_clean_text(value, 256) for value in source_category_path)
        ):
            raise ValueError(f"商品来源类目路径不完整: {spu_id}")
        if (
            not isinstance(row.get("origin_attributes"), dict)
            or not row["origin_attributes"]
        ):
            raise ValueError(f"商品来源参数缺失: {spu_id}")
        if not str(row["origin_url"]).startswith(("http://", "https://")):
            raise ValueError(f"商品来源链接无效: {spu_id}")
        source_skus = row.get("skus")
        if not isinstance(source_skus, list) or not source_skus:
            raise ValueError(f"商品血缘没有真实SKU: {spu_id}")
        for source_sku in source_skus:
            if not isinstance(source_sku, dict):
                raise ValueError(f"SKU血缘不是对象: {spu_id}")
            sku_id = int(source_sku["sku_id"])
            if sku_id in lineage_sku_refs:
                raise ValueError(f"血缘SKU业务ID重复: {sku_id}")
            origin_sku_key = _clean_text(source_sku.get("origin_sku_key"), 256)
            sku_required_text = {
                "external_sku_id": source_sku.get("external_sku_id"),
                "origin_sku_key": origin_sku_key,
                "origin_main_image_url": source_sku.get("origin_main_image_url"),
                "origin_url": source_sku.get("origin_url"),
                "origin_captured_at": source_sku.get("origin_captured_at"),
                "origin_sku_title": source_sku.get("origin_sku_title"),
            }
            sku_missing_fields = [
                key
                for key, value in sku_required_text.items()
                if not _clean_text(value, 1000)
            ]
            if sku_missing_fields:
                raise ValueError(f"SKU血缘核心字段缺失: {sku_id} {sku_missing_fields}")
            if origin_sku_key in origin_sku_keys:
                raise ValueError(f"原始SKU关系键重复: {origin_sku_key}")
            origin_specs = source_sku.get("origin_specs")
            normalized_specs = source_sku.get("normalized_specs")
            provenance = source_sku.get("specs_provenance")
            if not isinstance(origin_specs, dict) or not isinstance(
                normalized_specs, dict
            ):
                raise ValueError(f"SKU规格血缘无效: {sku_id}")
            if not normalized_specs:
                raise ValueError(f"SKU标准规格为空: {sku_id}")
            if provenance not in {"observed", "derived_single_sku"}:
                raise ValueError(f"SKU规格来源类型无效: {sku_id}")
            if provenance == "derived_single_sku" and normalized_specs != {
                "规格": "单规格"
            }:
                raise ValueError(f"单规格SKU标准值无效: {sku_id}")
            artifact_text = " ".join(
                [str(source_sku.get("origin_sku_title") or "")]
                + [f"{key}:{value}" for key, value in normalized_specs.items()]
            )
            if UI_ARTIFACT_PATTERN.search(artifact_text):
                raise ValueError(f"SKU仍包含页面控件文本: {sku_id}")
            try:
                sale_price = float(source_sku["origin_sale_price_cny"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"SKU来源售价无效: {sku_id}") from error
            if sale_price <= 0:
                raise ValueError(f"SKU来源售价必须大于0: {sku_id}")
            root_category = str(
                category_by_id[int(row["category_id"])]["root_category_name"]
            )
            lower, upper = CATEGORY_PRICE_RANGES.get(
                root_category,
                (0.5, 200_000.0),
            )
            if not lower <= sale_price <= upper:
                raise ValueError(
                    f"SKU来源售价超出类目区间: {sku_id} {root_category} {sale_price}"
                )
            list_price_value = source_sku.get("origin_list_price_cny")
            if list_price_value is not None:
                try:
                    list_price = float(list_price_value)
                except (TypeError, ValueError) as error:
                    raise ValueError(f"SKU来源划线价无效: {sku_id}") from error
                if list_price <= 0:
                    raise ValueError(f"SKU来源划线价必须大于0: {sku_id}")
                if not lower <= list_price <= upper or list_price < sale_price:
                    raise ValueError(f"SKU来源划线价与售价不一致: {sku_id}")
            lineage_sku_refs[sku_id] = spu_id
            origin_sku_keys.add(origin_sku_key)
        lineage_spu_ids.add(spu_id)
        lineage_spu_refs[spu_id] = (
            int(row["category_id"]),
            int(row["brand_id"]),
            int(row["shop_id"]),
        )
        origin_keys.add(origin_key)
        self_operated_spus += int(row["origin_is_self_operated"])

    spu_ids: set[int] = set()
    chinese_title_count = 0
    spu_refs: dict[int, tuple[int, int, int]] = {}
    root_counts: Counter[str] = Counter()
    brand_usage: Counter[int] = Counter()
    for row in _iter_jsonl(data_dir / "spus.jsonl"):
        spu_id = int(row["spu_id"])
        category_id = int(row["category_id"])
        brand_id = int(row["brand_id"])
        shop_id = int(row["shop_id"])
        if spu_id in spu_ids:
            raise ValueError(f"SPU业务ID重复: {spu_id}")
        lineage_keys = _business_lineage_keys(row)
        if lineage_keys:
            raise ValueError(f"SPU业务文件包含血缘字段: {sorted(lineage_keys)}")
        if not _clean_text(row.get("spu_name"), 256):
            raise ValueError(f"SPU名称缺失: {spu_id}")
        if spu_id not in lineage_spu_ids:
            raise ValueError(f"SPU缺少采集血缘: {spu_id}")
        if category_id not in category_ids:
            raise ValueError(f"SPU类目不存在: {spu_id}")
        if brand_id not in brand_ids:
            raise ValueError(f"SPU品牌不存在: {spu_id}")
        if shop_id not in shop_ids:
            raise ValueError(f"SPU店铺不存在: {spu_id}")
        if lineage_spu_refs[spu_id] != (category_id, brand_id, shop_id):
            raise ValueError(f"SPU业务维度与采集血缘引用不一致: {spu_id}")
        spu_ids.add(spu_id)
        chinese_title_count += int(
            bool(re.search(r"[\u4e00-\u9fff]", str(row["spu_name"])))
        )
        spu_refs[spu_id] = (category_id, brand_id, shop_id)
        root_counts[str(category_by_id[category_id]["root_category_name"])] += 1
        brand_usage[brand_id] += 1

    sku_ids: set[int] = set()
    sku_counts: Counter[int] = Counter()
    for row in _iter_jsonl(data_dir / "skus.jsonl"):
        sku_id = int(row["sku_id"])
        spu_id = int(row["spu_id"])
        if sku_id in sku_ids:
            raise ValueError(f"SKU业务ID重复: {sku_id}")
        if spu_id not in spu_ids:
            raise ValueError(f"SKU引用的SPU不存在: {sku_id}")
        lineage_keys = _business_lineage_keys(row)
        if lineage_keys:
            raise ValueError(f"SKU业务文件包含血缘字段: {sorted(lineage_keys)}")
        if not _clean_text(row.get("sku_name"), 256):
            raise ValueError(f"SKU名称缺失: {sku_id}")
        if lineage_sku_refs.get(sku_id) != spu_id:
            raise ValueError(f"SKU缺少匹配的采集血缘: {sku_id}")
        if not isinstance(row.get("sku_specs_json"), dict):
            raise ValueError(f"SKU规格不是对象: {sku_id}")
        if not row["sku_specs_json"]:
            raise ValueError(f"SKU规格为空: {sku_id}")
        sku_text = " ".join(
            [str(row.get("sku_name") or "")]
            + [
                f"{key}:{value}"
                for key, value in row["sku_specs_json"].items()
            ]
        )
        if UI_ARTIFACT_PATTERN.search(sku_text):
            raise ValueError(f"SKU业务字段包含页面控件文本: {sku_id}")
        refs = (int(row["category_id"]), int(row["brand_id"]), int(row["shop_id"]))
        if refs != spu_refs[spu_id]:
            raise ValueError(f"SKU与SPU维度引用不一致: {sku_id}")
        sku_ids.add(sku_id)
        sku_counts[spu_id] += 1

    if spu_ids != lineage_spu_ids:
        raise ValueError("SPU业务文件与采集血缘集合不一致")
    if sku_ids != set(lineage_sku_refs):
        raise ValueError("SKU业务文件与采集血缘集合不一致")

    actual = {
        "spus": len(spu_ids),
        "skus": len(sku_ids),
        "categories": len(categories),
        "root_categories": len(root_names),
        "brands": len(brands),
        "shops": len(shops),
    }
    for key, value in actual.items():
        if value != int(expected[key]):
            raise ValueError(
                f"目录数量不一致 field={key} expected={expected[key]} actual={value}"
            )
    invalid_variants = [spu_id for spu_id in spu_ids if sku_counts[spu_id] < 1]
    if invalid_variants:
        raise ValueError(f"SPU没有真实SKU: {invalid_variants[:10]}")
    if len(spu_ids) >= 1_000 and chinese_title_count / len(spu_ids) < 0.9:
        raise ValueError(
            f"商品标题中文覆盖率不足: {chinese_title_count}/{len(spu_ids)}"
        )
    if len(spu_ids) >= 1_000:
        if len(root_names) < 8:
            raise ValueError(f"综合电商一级类目不足: {len(root_names)}")
        if len(brands) < 500:
            raise ValueError(f"综合电商品牌数量异常: {len(brands)}")
        if len(shops) < 300:
            raise ValueError(f"综合电商店铺数量异常: {len(shops)}")
        if len(categories) < 100:
            raise ValueError(f"综合电商类目数量异常: {len(categories)}")
        largest_root, largest_count = root_counts.most_common(1)[0]
        if largest_count / len(spu_ids) > 0.35:
            raise ValueError(f"一级类目分布过度集中: {largest_root}={largest_count}")
        largest_brand_id, largest_brand_count = brand_usage.most_common(1)[0]
        if largest_brand_count / len(spu_ids) > 0.1:
            raise ValueError(
                f"品牌分布过度集中: {largest_brand_id}={largest_brand_count}"
            )
        self_operated_share = self_operated_spus / len(spu_ids)
        if not 0.15 <= self_operated_share <= 0.55:
            raise ValueError(f"自营商品占比异常: {self_operated_share:.4f}")
        selection = manifest.get("selection", {})
        if abs(
            float(selection.get("self_operated_spu_share", -1))
            - self_operated_share
        ) > 1e-12:
            raise ValueError("清单自营商品占比与商品血缘不一致")
        if float(selection.get("largest_brand_spu_share", 1)) > 0.1:
            raise ValueError("清单来源品牌分布过度集中")
        if float(selection.get("largest_store_spu_share", 1)) > 0.2:
            raise ValueError("清单来源店铺分布过度集中")
    for name, metadata in manifest["artifacts"].items():
        actual_hash = sha256(data_dir / name)
        if actual_hash != metadata["sha256"]:
            raise ValueError(f"目录文件哈希不一致: {name}")
    source_entries = manifest.get("source", {}).get("origins")
    if not isinstance(source_entries, list) or len(source_entries) != 1:
        raise ValueError("清单必须只包含一个商品来源")
    source_entry = source_entries[0]
    if not isinstance(source_entry, dict):
        raise ValueError("清单采集来源不是对象")
    if not re.fullmatch(r"[0-9a-f]{64}", str(source_entry.get("sha256", ""))):
        raise ValueError("清单采集来源摘要无效")
    if int(source_entry.get("selected_spus", 0)) != len(spu_ids):
        raise ValueError("清单采集来源 SPU 数量不一致")
    if int(source_entry.get("selected_skus", 0)) != len(sku_ids):
        raise ValueError("清单采集来源 SKU 数量不一致")
    return actual
