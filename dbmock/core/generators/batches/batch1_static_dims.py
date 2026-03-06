"""从冻结种子文件加载店铺、类目、品牌、支付方式、物流公司、地理区域和用户维度数据。"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from faker import Faker
from loguru import logger
from sqlalchemy import Date, DateTime, MetaData, Numeric, String, func, select

from ..settings import RunContext
from ..utils.loaders import bulk_insert

USER_TABLE_NAME = "dwd_dim_user_info_df"
TABLE_TO_SEED_FILE = {
    "dwd_dim_shop_info_df": "shops.json",
    "dwd_dim_category_info_df": "categories.json",
    "dwd_dim_brand_info_df": "brands.json",
    "dwd_dim_payment_type_df": "payment_types.json",
    "dwd_dim_logistics_company_df": "logistics_companies.json",
    "dwd_dim_geo_region_df": "geo_regions.json",
}

REQUIRED_FIELDS = {
    "dwd_dim_shop_info_df": [
        "shop_id",
        "shop_name",
        "shop_type",
        "seller_id",
        "seller_name",
        "industry_type",
        "shop_status",
    ],
    "dwd_dim_category_info_df": [
        "category_id",
        "category_name",
        "category_level",
        "root_category_id",
        "root_category_name",
        "is_leaf",
        "status",
    ],
    "dwd_dim_brand_info_df": ["brand_id", "brand_name", "status"],
    "dwd_dim_payment_type_df": [
        "payment_type_code",
        "payment_type_name",
        "is_online",
        "is_installment",
        "status",
    ],
    "dwd_dim_logistics_company_df": [
        "logistics_company_id",
        "logistics_company_code",
        "logistics_company_name",
        "logistics_type",
        "status",
    ],
    "dwd_dim_geo_region_df": [
        "region_code",
        "region_name",
        "region_level",
        "status",
    ],
}

UNIQUE_KEYS = {
    "dwd_dim_shop_info_df": "shop_id",
    "dwd_dim_category_info_df": "category_id",
    "dwd_dim_brand_info_df": "brand_id",
    "dwd_dim_payment_type_df": "payment_type_code",
    "dwd_dim_logistics_company_df": "logistics_company_id",
    "dwd_dim_geo_region_df": "region_code",
}

SHOP_TYPES = {"自营", "旗舰店", "专卖店", "普通店"}
SHOP_STATUS = {"营业", "关店"}
CATEGORY_LEVELS = {"一级", "二级", "三级"}
LOGISTICS_TYPES = {"快递", "同城", "冷链", "国际"}
USER_INITIAL_COUNT = 10_000
USER_FINAL_COUNT = 30_000
USER_END_OF_TIME = date(9999, 12, 31)
USER_GENDERS = ("男", "女")
REGISTER_CHANNELS = (
    ("APP", "APP"),
    ("H5", "H5"),
    ("PC", "PC"),
    ("MINI_PROGRAM", "小程序"),
    ("OFFLINE", "线下"),
)
USER_LEVELS = ("1", "2", "3", "4", "5")
USER_TAGS = (
    "新客",
    "价格敏感",
    "高活跃",
    "母婴人群",
    "数码爱好者",
    "服饰偏好",
    "家居消费",
    "食品偏好",
    "运动用户",
    "潜力会员",
)
OCCUPATIONS = (
    "互联网",
    "制造业",
    "金融",
    "教育",
    "医疗",
    "自由职业",
    "学生",
    "公务员",
    "服务业",
    "个体经营",
)
INCOME_LEVELS = ("3k以下", "3k-8k", "8k-15k", "15k-30k", "30k以上")
EDUCATION_LEVELS = ("高中及以下", "大专", "本科", "硕士", "博士")
MARITAL_STATUSES = ("未婚", "已婚")
USER_STATUSES = ("正常", "正常", "正常", "正常", "禁用")


def _load_seed(path: Path) -> list[dict[str, Any]]:
    """将种子文件加载为 JSON 对象列表。"""
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise ValueError(f"Seed file {path} must contain a JSON array")
    if not all(isinstance(row, dict) for row in payload):
        raise ValueError(f"Seed file {path} must contain JSON objects")
    return payload


def _validate_required_fields(table_name: str, rows: list[dict[str, Any]]) -> None:
    """校验每条种子记录都包含目标表要求的必填字段。"""
    required = REQUIRED_FIELDS[table_name]
    for idx, row in enumerate(rows, start=1):
        missing = [
            field for field in required if field not in row or row[field] in ("", None)
        ]
        if missing:
            raise ValueError(
                f"{table_name} row {idx} missing required fields: {missing}"
            )


def _validate_unique_key(table_name: str, rows: list[dict[str, Any]]) -> None:
    """校验种子文件中的业务主键唯一。"""
    key = UNIQUE_KEYS[table_name]
    seen: set[Any] = set()
    for row in rows:
        value = row[key]
        if value in seen:
            raise ValueError(f"{table_name} duplicate seed key: {key}={value}")
        seen.add(value)


def _validate_lengths(table_name: str, rows: list[dict[str, Any]], table) -> None:
    """校验字符串字段长度不超过数据库列定义。"""
    for idx, row in enumerate(rows, start=1):
        for col in table.columns:
            if col.name not in row:
                continue
            value = row[col.name]
            if (
                isinstance(col.type, String)
                and isinstance(value, str)
                and col.type.length
            ):
                if len(value) > col.type.length:
                    raise ValueError(
                        f"{table_name} row {idx} field {col.name} exceeds max length {col.type.length}"
                    )


def _normalize_row(table, row: dict[str, Any]) -> dict[str, Any]:
    """将 JSON 原始值转换为与表结构一致的 Python 类型。"""
    normalized: dict[str, Any] = {}
    for col in table.columns:
        if col.name in {"id", "etl_date"}:
            continue
        if col.name not in row:
            continue
        value = row[col.name]
        if isinstance(value, float):
            value = Decimal(str(value))
        elif isinstance(col.type, Numeric) and isinstance(value, str):
            value = Decimal(value)
        elif isinstance(col.type, DateTime) and isinstance(value, str):
            value = datetime.fromisoformat(value)
        elif isinstance(col.type, Date) and isinstance(value, str):
            value = date.fromisoformat(value)
        normalized[col.name] = value
    return normalized


def _serialize_value(value: Any) -> Any:
    """将类型化后的值转换为可稳定比较的值。"""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _comparable_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """规范化并排序记录，便于稳定比较两个版本的数据。"""
    comparable = []
    for row in rows:
        comparable.append(
            {field: _serialize_value(value) for field, value in row.items()}
        )
    return sorted(comparable, key=lambda row: row[key])


def _latest_etl_date(conn, table) -> date | None:
    """返回目标维度表已存储数据中的最新版本日期。"""
    stmt = select(func.max(table.c.etl_date))
    return conn.execute(stmt).scalar_one()


def _load_version_rows(conn, table, etl_date: date) -> list[dict[str, Any]]:
    """加载目标维度表在指定版本日期下的全部业务字段。"""
    stmt = select(table).where(table.c.etl_date == etl_date)
    rows = []
    for row in conn.execute(stmt).mappings():
        payload = {
            col.name: row[col.name]
            for col in table.columns
            if col.name not in {"id", "etl_date"}
        }
        rows.append(payload)
    return rows


def _validate_categories(rows: list[dict[str, Any]]) -> set[str]:
    """校验类目层级关系，并收集一级类目名称集合。"""
    category_ids = {row["category_id"] for row in rows}
    level1_names: set[str] = set()
    for row in rows:
        if row["category_level"] not in CATEGORY_LEVELS:
            raise ValueError(f"Invalid category level: {row['category_level']}")
        if row["category_level"] == "一级":
            level1_names.add(row["category_name"])
        parent_id = row.get("parent_category_id")
        if row["category_level"] != "一级" and parent_id not in category_ids:
            raise ValueError(
                f"Missing parent category for category_id={row['category_id']}"
            )
        if row["category_level"] == "三级" and row.get("is_leaf") != 1:
            raise ValueError(
                f"三级类目必须是叶子节点: category_id={row['category_id']}"
            )
    return level1_names


def _validate_shops(rows: list[dict[str, Any]], level1_names: set[str]) -> None:
    """校验店铺枚举值，并确保行业映射到一级类目。"""
    for row in rows:
        if row["shop_type"] not in SHOP_TYPES:
            raise ValueError(f"Invalid shop_type: {row['shop_type']}")
        if row["shop_status"] not in SHOP_STATUS:
            raise ValueError(f"Invalid shop_status: {row['shop_status']}")
        if row["industry_type"] not in level1_names:
            raise ValueError(
                f"Shop industry_type must map to level1 category: {row['shop_name']} -> {row['industry_type']}"
            )


def _validate_payments(rows: list[dict[str, Any]]) -> None:
    """校验支付方式种子的标记位和状态值。"""
    for row in rows:
        if row["is_online"] not in {0, 1}:
            raise ValueError(
                f"Invalid is_online for payment: {row['payment_type_code']}"
            )
        if row["is_installment"] not in {0, 1}:
            raise ValueError(
                f"Invalid is_installment for payment: {row['payment_type_code']}"
            )
        if row["status"] not in {0, 1}:
            raise ValueError(f"Invalid payment status: {row['payment_type_code']}")


def _validate_logistics(rows: list[dict[str, Any]]) -> None:
    """校验物流类型和轨迹支持标记。"""
    for row in rows:
        if row["logistics_type"] not in LOGISTICS_TYPES:
            raise ValueError(
                f"Invalid logistics_type for company {row['logistics_company_code']}: {row['logistics_type']}"
            )
        if row.get("is_trace_supported") not in {0, 1, None}:
            raise ValueError(
                f"Invalid is_trace_supported for company {row['logistics_company_code']}"
            )


def _validate_regions(rows: list[dict[str, Any]]) -> None:
    """校验行政区划层级关系和层级取值。"""
    region_codes = {row["region_code"] for row in rows}
    for row in rows:
        if row["region_level"] not in {1, 2, 3, 4}:
            raise ValueError(f"Invalid region_level: {row['region_code']}")
        parent_code = row.get("parent_region_code")
        if row["region_level"] > 1 and parent_code not in region_codes:
            raise ValueError(
                f"Missing parent region for region_code={row['region_code']}"
            )


def _validate_seed_bundle(seed_rows: dict[str, list[dict[str, Any]]], tables) -> None:
    """对店铺、类目、品牌、支付方式、物流公司和地理区域种子执行跨表与单表校验。"""
    level1_names = _validate_categories(seed_rows["dwd_dim_category_info_df"])
    _validate_shops(seed_rows["dwd_dim_shop_info_df"], level1_names)
    _validate_payments(seed_rows["dwd_dim_payment_type_df"])
    _validate_logistics(seed_rows["dwd_dim_logistics_company_df"])
    _validate_regions(seed_rows["dwd_dim_geo_region_df"])

    for table_name, rows in seed_rows.items():
        _validate_required_fields(table_name, rows)
        _validate_unique_key(table_name, rows)
        _validate_lengths(table_name, rows, tables[table_name])


def _mask_phone(prefix: str, suffix_seed: int) -> str:
    """生成脱敏手机号。"""
    return f"{prefix}****{suffix_seed:04d}"


def _mask_email(user_name: str, domain: str) -> str:
    """生成脱敏邮箱。"""
    return f"{user_name[:1]}***@{domain}"


def _build_register_time(register_date: date, seed: int) -> datetime:
    """根据注册日期生成稳定的注册时间。"""
    hour = 9 + seed % 10
    minute = (seed * 7) % 60
    second = (seed * 13) % 60
    return datetime(
        register_date.year,
        register_date.month,
        register_date.day,
        hour,
        minute,
        second,
    )


def _build_user_names(total_count: int, seed: int) -> list[str]:
    """使用 Faker 批量生成稳定的中文姓名。"""
    faker = Faker("zh_CN")
    faker.seed_instance(seed)
    return [faker.name() for _ in range(total_count)]


def _build_user_region_candidates(
    region_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """预先构造用户可用的行政区划候选集合。"""
    level4_rows = [row for row in region_rows if row["region_level"] == 4]
    if level4_rows:
        return level4_rows
    return [row for row in region_rows if row["region_level"] == 3]


def _pick_region(region_candidates: list[dict[str, Any]], idx: int) -> dict[str, Any]:
    """为用户选择一个稳定的行政区划。"""
    return region_candidates[idx % len(region_candidates)]


def _build_user_base_row(
    user_id: int,
    register_date: date,
    region: dict[str, Any],
    idx: int,
    nick_name: str,
) -> dict[str, Any]:
    """构造用户的首个版本记录。"""
    gender = USER_GENDERS[idx % len(USER_GENDERS)]
    user_name = f"user_{user_id}"
    birthday = date(1970 + idx % 30, idx % 12 + 1, idx % 28 + 1)
    register_channel_code, register_source = REGISTER_CHANNELS[
        idx % len(REGISTER_CHANNELS)
    ]
    user_level = USER_LEVELS[min(idx % 3, len(USER_LEVELS) - 1)]
    is_vip = 1 if user_level in {"3", "4", "5"} else 0
    user_tag = ",".join(
        sorted(
            {
                USER_TAGS[idx % len(USER_TAGS)],
                USER_TAGS[(idx + 3) % len(USER_TAGS)],
            }
        )
    )
    return {
        "user_id": user_id,
        "user_name": user_name,
        "nick_name": nick_name,
        "gender": gender,
        "birthday": birthday,
        "phone": _mask_phone(f"13{idx % 10}", user_id % 10_000),
        "email": _mask_email(user_name, ["gmail.com", "qq.com", "163.com"][idx % 3]),
        "register_time": _build_register_time(register_date, idx),
        "register_channel_code": register_channel_code,
        "register_source": register_source,
        "user_level": user_level,
        "user_tag": user_tag,
        "is_vip": is_vip,
        "province_code": region["province_code"],
        "city_code": region["city_code"],
        "district_code": region["district_code"],
        "occupation": OCCUPATIONS[idx % len(OCCUPATIONS)],
        "income_level": INCOME_LEVELS[idx % len(INCOME_LEVELS)],
        "education_level": EDUCATION_LEVELS[idx % len(EDUCATION_LEVELS)],
        "marital_status": MARITAL_STATUSES[idx % len(MARITAL_STATUSES)],
        "user_status": USER_STATUSES[idx % len(USER_STATUSES)],
    }


def _build_user_change_row(base_row: dict[str, Any], idx: int) -> dict[str, Any]:
    """基于首版记录构造用户变更版本。"""
    changed = dict(base_row)
    next_level_index = min(
        USER_LEVELS.index(base_row["user_level"]) + 1,
        len(USER_LEVELS) - 1,
    )
    changed["user_level"] = USER_LEVELS[next_level_index]
    changed["is_vip"] = 1 if changed["user_level"] in {"3", "4", "5"} else 0
    changed["user_tag"] = ",".join(
        sorted(
            {
                USER_TAGS[idx % len(USER_TAGS)],
                USER_TAGS[(idx + 5) % len(USER_TAGS)],
                "复购用户",
            }
        )
    )
    changed["income_level"] = INCOME_LEVELS[
        min(idx % len(INCOME_LEVELS) + 1, len(INCOME_LEVELS) - 1)
    ]
    changed["user_status"] = "正常"
    return changed


def _build_user_rows(
    start_date: date,
    end_date: date,
    region_rows: list[dict[str, Any]],
    seed: int,
) -> list[dict[str, Any]]:
    """生成首日一万用户、最终三万用户且包含后续属性变更版本的用户维度数据。"""
    if USER_FINAL_COUNT < USER_INITIAL_COUNT:
        raise ValueError("USER_FINAL_COUNT 不能小于 USER_INITIAL_COUNT")

    total_days = max((end_date - start_date).days, 1)
    growth_count = USER_FINAL_COUNT - USER_INITIAL_COUNT
    region_candidates = _build_user_region_candidates(region_rows)
    user_names = _build_user_names(USER_FINAL_COUNT, seed)
    rows: list[dict[str, Any]] = []

    for idx in range(USER_FINAL_COUNT):
        user_id = 1_000_000 + idx + 1
        if idx < USER_INITIAL_COUNT:
            register_date = start_date
        else:
            growth_idx = idx - USER_INITIAL_COUNT + 1
            day_offset = max(1, growth_idx * total_days // max(growth_count, 1))
            register_date = date.fromordinal(start_date.toordinal() + day_offset)
            if register_date > end_date:
                register_date = end_date

        region = _pick_region(region_candidates, idx)
        base_row = _build_user_base_row(
            user_id,
            register_date,
            region,
            idx,
            user_names[idx],
        )
        change_date = None

        if idx % 7 == 0:
            change_offset = 30 + idx % 240
            candidate_change_date = register_date + timedelta(days=change_offset)
            if candidate_change_date <= end_date:
                change_date = candidate_change_date

        if change_date is None:
            rows.append(
                base_row
                | {
                    "start_date": register_date,
                    "end_date": USER_END_OF_TIME,
                    "is_current": 1,
                }
            )
            continue

        rows.append(
            base_row
            | {
                "start_date": register_date,
                "end_date": change_date - timedelta(days=1),
                "is_current": 0,
            }
        )
        rows.append(
            _build_user_change_row(base_row, idx)
            | {
                "start_date": change_date,
                "end_date": USER_END_OF_TIME,
                "is_current": 1,
            }
        )

    return rows


def _existing_user_keys(conn, table) -> set[tuple[int, date]]:
    """查询用户维表中已存在的用户版本键。"""
    stmt = select(table.c.user_id, table.c.start_date)
    return {(row.user_id, row.start_date) for row in conn.execute(stmt)}


def _has_user_rows(conn, table) -> bool:
    """判断用户维表是否已经存在数据。"""
    stmt = select(table.c.id).limit(1)
    return conn.execute(stmt).first() is not None


def run(ctx: RunContext) -> None:
    """将用户维度和静态维度数据加载、校验后写入 MySQL。"""
    logger.info("Run batch1_static_dims")
    metadata = MetaData()
    table_names = [USER_TABLE_NAME, *TABLE_TO_SEED_FILE.keys()]
    metadata.reflect(bind=ctx.engine, only=table_names)
    tables = {name: metadata.tables[name] for name in table_names}

    seed_rows: dict[str, list[dict[str, Any]]] = {}
    for table_name, file_name in TABLE_TO_SEED_FILE.items():
        seed_path = ctx.gen.seed_dir / file_name
        if not seed_path.exists():
            raise FileNotFoundError(f"Missing seed file: {seed_path}")
        rows = _load_seed(seed_path)
        seed_rows[table_name] = [
            _normalize_row(tables[table_name], row) for row in rows
        ]
        logger.info(
            "Loaded {} rows from {}", len(seed_rows[table_name]), seed_path.name
        )

    _validate_seed_bundle(seed_rows, tables)

    start_date = date.fromisoformat(ctx.gen.start_date)
    end_date = date.fromisoformat(ctx.gen.end_date)
    user_rows = _build_user_rows(
        start_date,
        end_date,
        seed_rows["dwd_dim_geo_region_df"],
        ctx.gen.seed,
    )

    with ctx.engine.begin() as conn:
        user_table = tables[USER_TABLE_NAME]
        if _has_user_rows(conn, user_table):
            existing_user_keys = _existing_user_keys(conn, user_table)
            pending_user_rows = [
                row
                for row in user_rows
                if (row["user_id"], row["start_date"]) not in existing_user_keys
            ]
        else:
            pending_user_rows = user_rows

        user_inserted = bulk_insert(
            conn,
            user_table,
            pending_user_rows,
            batch_size=ctx.gen.batch_size,
        )
        logger.info(
            "{} -> generated_rows={}, inserted_rows={}",
            USER_TABLE_NAME,
            len(user_rows),
            user_inserted,
        )

        for table_name in table_names:
            if table_name == USER_TABLE_NAME:
                continue
            table = tables[table_name]
            latest_etl_date = _latest_etl_date(conn, table)
            if latest_etl_date is None:
                snapshot_rows = [
                    row | {"etl_date": start_date} for row in seed_rows[table_name]
                ]
                inserted = bulk_insert(
                    conn,
                    table,
                    snapshot_rows,
                    batch_size=ctx.gen.batch_size,
                )
                logger.info(
                    "{} -> seed_rows={}, inserted_rows={}, initial_version={}",
                    table_name,
                    len(seed_rows[table_name]),
                    inserted,
                    start_date,
                )
                continue

            latest_rows = _load_version_rows(conn, table, latest_etl_date)
            if _comparable_rows(
                latest_rows, UNIQUE_KEYS[table_name]
            ) == _comparable_rows(seed_rows[table_name], UNIQUE_KEYS[table_name]):
                logger.info(
                    "{} -> seed_rows={}, latest_version={}, unchanged, skipped",
                    table_name,
                    len(seed_rows[table_name]),
                    latest_etl_date,
                )
                continue

            if latest_etl_date >= end_date:
                raise ValueError(
                    f"{table_name} 检测到种子变更，但数据库中的最新版本日期 {latest_etl_date} 已不早于本次生成结束日期 {end_date}"
                )

            snapshot_rows = [
                row | {"etl_date": end_date} for row in seed_rows[table_name]
            ]
            inserted = bulk_insert(
                conn,
                table,
                snapshot_rows,
                batch_size=ctx.gen.batch_size,
            )
            logger.info(
                "{} -> seed_rows={}, latest_version={}, inserted_rows={}, new_version={}",
                table_name,
                len(seed_rows[table_name]),
                latest_etl_date,
                inserted,
                end_date,
            )
