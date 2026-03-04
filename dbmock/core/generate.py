"""电商数仓数据生成器

生成符合业务规则的模拟数据，支持异步批量插入。
"""

import asyncio
import random
import string
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

import asyncmy
from faker import Faker
from loguru import logger
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

faker = Faker("zh_CN")
Faker.seed(42)
random.seed(42)

# 数据规模配置
CONFIG = {
    "user_count": 10000,
    "shop_count": 150,
    "category_count": 50,
    "brand_count": 100,
    "region_count": 3500,
    "payment_type_count": 10,
    "logistics_company_count": 20,
    "spu_count": 500,
    "sku_count": 1000,
    "promotion_count": 50,
    "coupon_count": 100,
    "order_count": 100000,
    "cart_add_count": 50000,
    "favor_add_count": 30000,
    "comment_count": 30000,
    "page_view_count": 200000,
    "search_count": 100000,
    "batch_size": 2000,
    "max_workers": 10,
}

# 时间范围配置（动态计算：三年数据）
TODAY = date.today()
END_DATE = datetime.combine(TODAY, datetime.min.time())  # 今天
START_DATE = END_DATE.replace(year=END_DATE.year - 3)  # 三年前
ETL_DATE = TODAY  # 今天

# 数据库配置
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "123321",
    "database": "warehouse",
    "charset": "utf8mb4",
}


@dataclass
class DataCache:
    """数据缓存容器"""

    users: list[dict] = field(default_factory=list)
    shops: list[dict] = field(default_factory=list)
    categories: list[dict] = field(default_factory=list)
    brands: list[dict] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    payment_types: list[dict] = field(default_factory=list)
    logistics_companies: list[dict] = field(default_factory=list)
    spus: list[dict] = field(default_factory=list)
    skus: list[dict] = field(default_factory=list)
    promotions: list[dict] = field(default_factory=list)
    coupons: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)


class WarehouseDataGenerator:
    """电商数仓数据生成器"""

    def __init__(self):
        self.cache = DataCache()
        self.conn = None
        self._id_counters = {}

    def _next_id(self, key: str) -> int:
        """生成自增ID"""
        if key not in self._id_counters:
            self._id_counters[key] = 0
        self._id_counters[key] += 1
        return self._id_counters[key]

    def _random_time_in_range(self, start: datetime, end: datetime) -> datetime:
        """生成指定范围内的随机时间"""
        delta = end - start
        random_seconds = random.randint(0, int(delta.total_seconds()))
        return start + timedelta(seconds=random_seconds)

    def _weighted_date(self) -> datetime:
        """根据促销高峰加权生成日期"""
        # 基础分布：一年内随机
        base_time = self._random_time_in_range(START_DATE, END_DATE)

        # 促销高峰权重
        month = base_time.month
        day = base_time.day

        # 618 (6月1日-18日) 权重 × 8
        if month == 6 and 1 <= day <= 18:
            if random.random() < 0.875:  # 7/8 概率保留
                return base_time

        # 双11 (11月1日-11日) 权重 × 10
        if month == 11 and 1 <= day <= 11:
            if random.random() < 0.9:  # 9/10 概率保留
                return base_time

        # 双12 (12月1日-12日) 权重 × 5
        if month == 12 and 1 <= day <= 12:
            if random.random() < 0.8:  # 4/5 概率保留
                return base_time

        # 其他日期正常返回
        return base_time

    def _mask_phone(self, phone: str) -> str:
        """手机号脱敏"""
        return phone[:3] + "****" + phone[-4:]

    def _mask_email(self, email: str) -> str:
        """邮箱脱敏"""
        if "@" not in email:
            return email
        local, domain = email.split("@")
        if len(local) <= 2:
            return local[0] + "*@" + domain
        return local[0] + "*" * (len(local) - 2) + local[-1] + "@" + domain

    def _mask_name(self, name: str) -> str:
        """姓名脱敏"""
        if len(name) <= 1:
            return name
        return name[0] + "*" * (len(name) - 1)

    def _mask_address(self, address: str) -> str:
        """地址脱敏 - 保留省市，模糊详细地址"""
        # 简单处理：只保留前6个字符，后面用*代替
        if len(address) <= 6:
            return address
        return address[:6] + "*" * min(len(address) - 6, 10)

    # ==================== 批次1: 基础维度数据 ====================

    def generate_users(self) -> list[dict]:
        """生成用户数据"""
        logger.info(f"生成 {CONFIG['user_count']} 个用户...")
        users = []

        for i in range(CONFIG["user_count"]):
            user_id = self._next_id("user")
            phone = faker.phone_number()
            email = faker.email()
            full_name = faker.name()

            # 随机选择省市
            province = faker.province()
            city = faker.city()

            user = {
                "id": user_id,
                "user_id": user_id,
                "user_name": f"user_{user_id}",
                "nick_name": faker.user_name(),
                "gender": random.choice(["男", "女", "未知"]),
                "birthday": faker.date_of_birth(minimum_age=18, maximum_age=60),
                "phone": self._mask_phone(phone),
                "email": self._mask_email(email),
                "register_time": self._random_time_in_range(START_DATE, END_DATE),
                "register_channel_code": random.choice(
                    ["CHANNEL_001", "CHANNEL_002", "CHANNEL_003"]
                ),
                "register_source": random.choice(["APP", "H5", "PC", "MINI"]),
                "user_level": random.randint(1, 5),
                "user_tag": random.choice(["", "高价值", "活跃用户", "新客", None]),
                "is_vip": random.choice([0, 0, 0, 1]),  # 25% VIP
                "province_code": str(random.randint(110000, 650000)),
                "city_code": str(random.randint(110100, 659000)),
                "district_code": str(random.randint(110101, 659004)),
                "occupation": random.choice(
                    ["工程师", "教师", "医生", "销售", "公务员", "自由职业", None]
                ),
                "income_level": random.choice(["低", "中", "高", None]),
                "education_level": random.choice(
                    ["高中", "大专", "本科", "硕士", "博士", None]
                ),
                "marital_status": random.choice(["未婚", "已婚", "离异", None]),
                "user_status": random.choice(
                    ["正常", "正常", "正常", "正常", "禁用", "注销"]
                ),  # 80% 正常
                "first_order_time": None,
                "last_order_time": None,
                "etl_date": ETL_DATE,
            }
            users.append(user)

        self.cache.users = users
        return users

    def generate_shops(self) -> list[dict]:
        """生成店铺数据 - 前10个为固定知名店铺"""
        logger.info("加载固定店铺数据...")

        shops = [
            {
                "shop_id": 1,
                "shop_name": "京东自营旗舰店",
                "shop_type": "自营",
                "seller_id": 1001,
                "seller_name": "京东集团",
                "industry_type": "综合电商",
                "service_score": Decimal("4.8"),
                "logistics_score": Decimal("4.9"),
                "description_score": Decimal("4.7"),
                "open_time": datetime(2010, 1, 1),
                "province_code": "110000",
                "city_code": "110100",
                "district_code": "110101",
                "is_self_operated": 1,
                "is_global": 0,
                "is_deleted": 0,
                "shop_status": "营业",
                "etl_date": ETL_DATE,
            },
            {
                "shop_id": 2,
                "shop_name": "天猫官方旗舰店",
                "shop_type": "旗舰店",
                "seller_id": 1002,
                "seller_name": "天猫商城",
                "industry_type": "综合电商",
                "service_score": Decimal("4.7"),
                "logistics_score": Decimal("4.6"),
                "description_score": Decimal("4.8"),
                "open_time": datetime(2011, 6, 1),
                "province_code": "310000",
                "city_code": "310100",
                "district_code": "310101",
                "is_self_operated": 0,
                "is_global": 0,
                "is_deleted": 0,
                "shop_status": "营业",
                "etl_date": ETL_DATE,
            },
            {
                "shop_id": 3,
                "shop_name": "小米官方旗舰店",
                "shop_type": "旗舰店",
                "seller_id": 1003,
                "seller_name": "小米科技",
                "industry_type": "数码家电",
                "service_score": Decimal("4.6"),
                "logistics_score": Decimal("4.7"),
                "description_score": Decimal("4.8"),
                "open_time": datetime(2011, 1, 1),
                "province_code": "110000",
                "city_code": "110100",
                "district_code": "110108",
                "is_self_operated": 1,
                "is_global": 0,
                "is_deleted": 0,
                "shop_status": "营业",
                "etl_date": ETL_DATE,
            },
            {
                "shop_id": 4,
                "shop_name": "华为官方旗舰店",
                "shop_type": "旗舰店",
                "seller_id": 1004,
                "seller_name": "华为技术",
                "industry_type": "数码通信",
                "service_score": Decimal("4.9"),
                "logistics_score": Decimal("4.8"),
                "description_score": Decimal("4.9"),
                "open_time": datetime(2012, 3, 1),
                "province_code": "440000",
                "city_code": "440300",
                "district_code": "440303",
                "is_self_operated": 1,
                "is_global": 1,
                "is_deleted": 0,
                "shop_status": "营业",
                "etl_date": ETL_DATE,
            },
            {
                "shop_id": 5,
                "shop_name": "Apple官方店",
                "shop_type": "自营",
                "seller_id": 1005,
                "seller_name": "苹果中国",
                "industry_type": "数码电子",
                "service_score": Decimal("4.9"),
                "logistics_score": Decimal("4.7"),
                "description_score": Decimal("4.9"),
                "open_time": datetime(2008, 7, 1),
                "province_code": "310000",
                "city_code": "310100",
                "district_code": "310115",
                "is_self_operated": 1,
                "is_global": 1,
                "is_deleted": 0,
                "shop_status": "营业",
                "etl_date": ETL_DATE,
            },
            {
                "shop_id": 6,
                "shop_name": "耐克运动旗舰店",
                "shop_type": "旗舰店",
                "seller_id": 1006,
                "seller_name": "耐克体育",
                "industry_type": "运动户外",
                "service_score": Decimal("4.5"),
                "logistics_score": Decimal("4.4"),
                "description_score": Decimal("4.6"),
                "open_time": datetime(2012, 1, 1),
                "province_code": "310000",
                "city_code": "310100",
                "district_code": "310101",
                "is_self_operated": 0,
                "is_global": 1,
                "is_deleted": 0,
                "shop_status": "营业",
                "etl_date": ETL_DATE,
            },
            {
                "shop_id": 7,
                "shop_name": "阿迪达斯旗舰店",
                "shop_type": "旗舰店",
                "seller_id": 1007,
                "seller_name": "阿迪达斯",
                "industry_type": "运动户外",
                "service_score": Decimal("4.4"),
                "logistics_score": Decimal("4.3"),
                "description_score": Decimal("4.5"),
                "open_time": datetime(2012, 6, 1),
                "province_code": "310000",
                "city_code": "310100",
                "district_code": "310101",
                "is_self_operated": 0,
                "is_global": 1,
                "is_deleted": 0,
                "shop_status": "营业",
                "etl_date": ETL_DATE,
            },
            {
                "shop_id": 8,
                "shop_name": "优衣库官方旗舰店",
                "shop_type": "旗舰店",
                "seller_id": 1008,
                "seller_name": "迅销中国",
                "industry_type": "服装服饰",
                "service_score": Decimal("4.6"),
                "logistics_score": Decimal("4.5"),
                "description_score": Decimal("4.7"),
                "open_time": datetime(2009, 9, 1),
                "province_code": "310000",
                "city_code": "310100",
                "district_code": "310101",
                "is_self_operated": 0,
                "is_global": 1,
                "is_deleted": 0,
                "shop_status": "营业",
                "etl_date": ETL_DATE,
            },
            {
                "shop_id": 9,
                "shop_name": "ZARA官方旗舰店",
                "shop_type": "专卖店",
                "seller_id": 1009,
                "seller_name": "飒拉中国",
                "industry_type": "服装服饰",
                "service_score": Decimal("4.3"),
                "logistics_score": Decimal("4.2"),
                "description_score": Decimal("4.4"),
                "open_time": datetime(2010, 5, 1),
                "province_code": "310000",
                "city_code": "310100",
                "district_code": "310101",
                "is_self_operated": 0,
                "is_global": 1,
                "is_deleted": 0,
                "shop_status": "营业",
                "etl_date": ETL_DATE,
            },
            {
                "shop_id": 10,
                "shop_name": "星巴克官方旗舰店",
                "shop_type": "专卖店",
                "seller_id": 1010,
                "seller_name": "星巴克中国",
                "industry_type": "食品饮品",
                "service_score": Decimal("4.7"),
                "logistics_score": Decimal("4.6"),
                "description_score": Decimal("4.8"),
                "open_time": datetime(2015, 1, 1),
                "province_code": "310000",
                "city_code": "310100",
                "district_code": "310101",
                "is_self_operated": 0,
                "is_global": 0,
                "is_deleted": 0,
                "shop_status": "营业",
                "etl_date": ETL_DATE,
            },
        ]

        # 补充到CONFIG数量
        for i in range(len(shops) + 1, CONFIG["shop_count"] + 1):
            shops.append(
                {
                    "shop_id": i,
                    "shop_name": f"店铺{i}",
                    "shop_type": random.choice(["专卖店", "普通店"]),
                    "seller_id": 1000 + i,
                    "seller_name": f"商家{i}",
                    "industry_type": random.choice(
                        ["服装", "数码", "食品", "家居", "美妆"]
                    ),
                    "service_score": round(random.uniform(3.5, 5.0), 2),
                    "logistics_score": round(random.uniform(3.5, 5.0), 2),
                    "description_score": round(random.uniform(3.5, 5.0), 2),
                    "open_time": datetime(2015, 1, 1),
                    "province_code": "110000",
                    "city_code": "110100",
                    "district_code": "110101",
                    "is_self_operated": 0,
                    "is_global": 0,
                    "is_deleted": 0,
                    "shop_status": "营业",
                    "etl_date": ETL_DATE,
                }
            )

        self.cache.shops = shops
        return shops

    def generate_categories(self) -> list[dict]:
        """生成类目数据 - 三级类目结构"""
        logger.info(f"生成 {CONFIG['category_count']} 个类目...")
        categories = []

        # 定义类目结构
        category_tree = {
            "手机数码": ["手机", "电脑", "摄影摄像", "数码配件"],
            "服装鞋包": ["女装", "男装", "鞋靴", "箱包"],
            "家居家装": ["家具", "家纺", "厨具", "灯具"],
            "美妆个护": ["面部护肤", "彩妆", "洗护", "口腔"],
            "食品生鲜": ["休闲零食", "酒水饮料", "粮油调味", "生鲜"],
        }

        cat_id = 1
        for root_name, sub_names in category_tree.items():
            root_id = cat_id
            root_cat = {
                "id": cat_id,
                "category_id": cat_id,
                "category_name": root_name,
                "category_level": 1,
                "parent_category_id": None,
                "parent_category_name": None,
                "root_category_id": root_id,
                "root_category_name": root_name,
                "is_leaf": 0,
                "sort_order": random.randint(1, 100),
                "category_path": root_name,
                "status": 1,
                "etl_date": ETL_DATE,
            }
            categories.append(root_cat)
            cat_id += 1

            for sub_name in sub_names:
                sub_id = cat_id
                sub_cat = {
                    "id": sub_id,
                    "category_id": sub_id,
                    "category_name": sub_name,
                    "category_level": 2,
                    "parent_category_id": root_id,
                    "parent_category_name": root_name,
                    "root_category_id": root_id,
                    "root_category_name": root_name,
                    "is_leaf": 0,
                    "sort_order": random.randint(1, 100),
                    "category_path": f"{root_name}>{sub_name}",
                    "status": 1,
                    "etl_date": ETL_DATE,
                }
                categories.append(sub_cat)
                cat_id += 1

                # 生成三级类目
                for j in range(3):
                    leaf_id = cat_id
                    leaf_name = f"{sub_name}{j + 1}"
                    leaf_cat = {
                        "id": leaf_id,
                        "category_id": leaf_id,
                        "category_name": leaf_name,
                        "category_level": 3,
                        "parent_category_id": sub_id,
                        "parent_category_name": sub_name,
                        "root_category_id": root_id,
                        "root_category_name": root_name,
                        "is_leaf": 1,
                        "sort_order": random.randint(1, 100),
                        "category_path": f"{root_name}>{sub_name}>{leaf_name}",
                        "status": 1,
                        "etl_date": ETL_DATE,
                    }
                    categories.append(leaf_cat)
                    cat_id += 1

                    if len(categories) >= CONFIG["category_count"]:
                        break
                if len(categories) >= CONFIG["category_count"]:
                    break
            if len(categories) >= CONFIG["category_count"]:
                break

        self.cache.categories = categories
        return categories

    def generate_brands(self) -> list[dict]:
        """生成品牌数据"""
        logger.info(f"生成 {CONFIG['brand_count']} 个品牌...")
        brands = []

        brand_names = [
            "Apple",
            "Samsung",
            "Huawei",
            "Xiaomi",
            "OPPO",
            "vivo",
            "OnePlus",
            "Nike",
            "Adidas",
            "Puma",
            "Uniqlo",
            "ZARA",
            "H&M",
            "Gap",
            "IKEA",
            "Muji",
            "Philips",
            "Sony",
            "Bose",
            "Dyson",
            "L'Oreal",
            "Estee Lauder",
            "SK-II",
            "Lancome",
            "Chanel",
            "Nespresso",
            "Starbucks",
            "Coca-Cola",
            "Pepsi",
            "Nestle",
        ]

        for i in range(CONFIG["brand_count"]):
            brand_id = self._next_id("brand")
            brand = {
                "id": brand_id,
                "brand_id": brand_id,
                "brand_name": brand_names[i % len(brand_names)]
                if i < len(brand_names)
                else f"品牌{brand_id}",
                "etl_date": ETL_DATE,
            }
            brands.append(brand)

        self.cache.brands = brands
        return brands

    def generate_regions(self) -> list[dict]:
        """生成行政区域数据"""
        logger.info(f"生成 {CONFIG['region_count']} 个区域...")
        regions = []

        # 使用faker生成省市数据
        provinces = [
            ("110000", "北京市"),
            ("120000", "天津市"),
            ("310000", "上海市"),
            ("500000", "重庆市"),
            ("130000", "河北省"),
            ("140000", "山西省"),
            ("150000", "内蒙古自治区"),
            ("210000", "辽宁省"),
            ("220000", "吉林省"),
            ("230000", "黑龙江省"),
            ("320000", "江苏省"),
            ("330000", "浙江省"),
            ("340000", "安徽省"),
            ("350000", "福建省"),
            ("360000", "江西省"),
            ("370000", "山东省"),
            ("410000", "河南省"),
            ("420000", "湖北省"),
            ("430000", "湖南省"),
            ("440000", "广东省"),
            ("450000", "广西壮族自治区"),
            ("460000", "海南省"),
            ("510000", "四川省"),
            ("520000", "贵州省"),
            ("530000", "云南省"),
            ("540000", "西藏自治区"),
            ("610000", "陕西省"),
            ("620000", "甘肃省"),
            ("630000", "青海省"),
            ("640000", "宁夏回族自治区"),
            ("650000", "新疆维吾尔自治区"),
        ]

        region_id = 1
        for prov_code, prov_name in provinces:
            # 省
            regions.append(
                {
                    "id": region_id,
                    "region_code": prov_code,
                    "region_name": prov_name,
                    "region_level": 1,
                    "parent_region_code": None,
                    "parent_region_name": None,
                    "province_code": prov_code,
                    "province_name": prov_name,
                    "city_code": None,
                    "city_name": None,
                    "district_code": None,
                    "district_name": None,
                    "zip_code": str(random.randint(100000, 999999)),
                    "status": 1,
                    "etl_date": ETL_DATE,
                }
            )
            region_id += 1

            if len(regions) >= CONFIG["region_count"]:
                break

        self.cache.regions = regions
        return regions

    def generate_payment_types(self) -> list[dict]:
        """生成支付方式数据 - 固定参考数据"""
        logger.info("加载固定支付方式数据...")

        payment_types = [
            {
                "payment_type_code": "ALIPAY",
                "payment_type_name": "支付宝",
                "channel_code": "ALIPAY",
                "channel_name": "支付宝",
                "is_online": 1,
                "is_installment": 0,
                "fee_rate": Decimal("0.006"),
                "status": 1,
                "etl_date": ETL_DATE,
            },
            {
                "payment_type_code": "WECHAT",
                "payment_type_name": "微信支付",
                "channel_code": "WECHAT",
                "channel_name": "微信支付",
                "is_online": 1,
                "is_installment": 0,
                "fee_rate": Decimal("0.006"),
                "status": 1,
                "etl_date": ETL_DATE,
            },
            {
                "payment_type_code": "UNIONPAY",
                "payment_type_name": "银联支付",
                "channel_code": "UNIONPAY",
                "channel_name": "银联",
                "is_online": 1,
                "is_installment": 0,
                "fee_rate": Decimal("0.005"),
                "status": 1,
                "etl_date": ETL_DATE,
            },
            {
                "payment_type_code": "CREDIT",
                "payment_type_name": "信用卡",
                "channel_code": "BANK",
                "channel_name": "银行",
                "is_online": 1,
                "is_installment": 1,
                "fee_rate": Decimal("0.008"),
                "status": 1,
                "etl_date": ETL_DATE,
            },
            {
                "payment_type_code": "DEBIT",
                "payment_type_name": "借记卡",
                "channel_code": "BANK",
                "channel_name": "银行",
                "is_online": 1,
                "is_installment": 0,
                "fee_rate": Decimal("0.003"),
                "status": 1,
                "etl_date": ETL_DATE,
            },
            {
                "payment_type_code": "JD_PAY",
                "payment_type_name": "京东支付",
                "channel_code": "JD",
                "channel_name": "京东",
                "is_online": 1,
                "is_installment": 0,
                "fee_rate": Decimal("0.005"),
                "status": 1,
                "etl_date": ETL_DATE,
            },
            {
                "payment_type_code": "HUABEI",
                "payment_type_name": "花呗",
                "channel_code": "ALIPAY",
                "channel_name": "支付宝",
                "is_online": 1,
                "is_installment": 1,
                "fee_rate": Decimal("0.008"),
                "status": 1,
                "etl_date": ETL_DATE,
            },
            {
                "payment_type_code": "COD",
                "payment_type_name": "货到付款",
                "channel_code": "COD",
                "channel_name": "线下",
                "is_online": 0,
                "is_installment": 0,
                "fee_rate": None,
                "status": 1,
                "etl_date": ETL_DATE,
            },
            {
                "payment_type_code": "BALANCE",
                "payment_type_name": "余额支付",
                "channel_code": "PLATFORM",
                "channel_name": "平台",
                "is_online": 1,
                "is_installment": 0,
                "fee_rate": Decimal("0"),
                "status": 1,
                "etl_date": ETL_DATE,
            },
            {
                "payment_type_code": "POINTS",
                "payment_type_name": "积分兑换",
                "channel_code": "PLATFORM",
                "channel_name": "平台",
                "is_online": 1,
                "is_installment": 0,
                "fee_rate": Decimal("0"),
                "status": 1,
                "etl_date": ETL_DATE,
            },
        ]

        self.cache.payment_types = payment_types
        return payment_types

    def generate_logistics_companies(self) -> list[dict]:
        """生成物流公司数据"""
        logger.info(f"生成 {CONFIG['logistics_company_count']} 个物流公司...")
        companies = []

        logistics_list = [
            (1, "SF", "顺丰速运", "快递", "95338", 1),
            (2, "JD", "京东物流", "快递", "950616", 1),
            (3, "YTO", "圆通速递", "快递", "95554", 1),
            (4, "ZTO", "中通快递", "快递", "95311", 1),
            (5, "YUNDA", "韵达速递", "快递", "95546", 1),
            (6, "EMS", "中国邮政", "快递", "11183", 1),
            (7, "DB", "德邦快递", "快递", "95353", 1),
            (8, "JT", "极兔速递", "快递", "956025", 1),
            (9, "SF_COLD", "顺丰冷链", "冷链", "95338", 1),
            (10, "DADA", "达达快送", "同城", "400-991-9512", 1),
            (11, "MEITUAN", "美团配送", "同城", "10107888", 1),
            (12, "ELEME", "蜂鸟即配", "同城", "10105757", 1),
            (13, "UPS", "UPS", "国际", "400-820-8388", 1),
            (14, "DHL", "DHL", "国际", "95380", 1),
            (15, "FEDEX", "FedEx", "国际", "400-886-1888", 1),
            (16, "TNT", "TNT", "国际", "800-820-9868", 1),
            (17, "KY", "跨越速运", "快递", "95324", 1),
            (18, "UC", "优速快递", "快递", "95349", 1),
            (19, "STO", "申通快递", "快递", "95543", 1),
            (20, "HTKY", "百世快递", "快递", "95320", 1),
        ]

        for i, (comp_id, code, name, logistics_type, phone, is_trace) in enumerate(
            logistics_list
        ):
            companies.append(
                {
                    "id": comp_id,
                    "logistics_company_id": comp_id,
                    "logistics_company_code": code,
                    "logistics_company_name": name,
                    "logistics_type": logistics_type,
                    "service_phone": phone,
                    "is_trace_supported": is_trace,
                    "status": 1,
                    "etl_date": ETL_DATE,
                }
            )

        self.cache.logistics_companies = companies
        return companies

    # ==================== 批次2: 商品维度数据 ====================

    def generate_spus(self) -> list[dict]:
        """生成SPU数据"""
        logger.info(f"生成 {CONFIG['spu_count']} 个SPU...")
        spus = []

        # 获取叶子类目
        leaf_categories = [c for c in self.cache.categories if c.get("is_leaf") == 1]
        if not leaf_categories:
            leaf_categories = self.cache.categories

        for i in range(CONFIG["spu_count"]):
            spu_id = self._next_id("spu")
            category = random.choice(leaf_categories)
            shop = random.choice(self.cache.shops)
            brand = random.choice(self.cache.brands)

            spu = {
                "id": spu_id,
                "spu_id": spu_id,
                "spu_name": faker.product_name()
                if hasattr(faker, "product_name")
                else f"商品{spu_id}",
                "spu_sub_title": faker.sentence()[:50]
                if random.random() > 0.3
                else None,
                "category_id": category["category_id"],
                "shop_id": shop["shop_id"],
                "brand_id": brand["brand_id"],
                "brand_name": brand["brand_name"],
                "is_virtual": random.choice([0, 0, 0, 1]),  # 25%虚拟商品
                "is_presale": random.choice([0, 0, 1]),  # 33%预售
                "presale_start_time": None,
                "presale_end_time": None,
                "weight": round(random.uniform(0.1, 10), 3),
                "volume": round(random.uniform(0.001, 1), 3),
                "shelf_status": random.choice([1, 1, 1, 2]),  # 75%上架
                "on_shelf_time": self._random_time_in_range(START_DATE, END_DATE),
                "off_shelf_time": None,
                "etl_date": ETL_DATE,
            }

            if spu["is_presale"]:
                spu["presale_start_time"] = self._random_time_in_range(
                    START_DATE, END_DATE
                )
                spu["presale_end_time"] = spu["presale_start_time"] + timedelta(
                    days=random.randint(1, 30)
                )

            spus.append(spu)

        self.cache.spus = spus
        return spus

    def generate_skus(self) -> list[dict]:
        """生成SKU数据"""
        logger.info(f"生成 {CONFIG['sku_count']} 个SKU...")
        skus = []

        for i in range(CONFIG["sku_count"]):
            sku_id = self._next_id("sku")
            spu = random.choice(self.cache.spus)
            shop = random.choice(self.cache.shops)
            brand = random.choice(self.cache.brands)

            origin_price = Decimal(str(round(random.uniform(10, 5000), 2)))
            sale_price = origin_price * Decimal(str(round(random.uniform(0.5, 1.0), 2)))
            cost_price = origin_price * Decimal(str(round(random.uniform(0.3, 0.6), 2)))

            sku = {
                "id": sku_id,
                "sku_id": sku_id,
                "sku_name": f"{spu['spu_name']}-{random.choice(['标准版', '高配版', '低配版', '豪华版', '简约版'])}",
                "spu_id": spu["spu_id"],
                "shop_id": shop["shop_id"],
                "category_id": spu["category_id"],
                "brand_id": brand["brand_id"],
                "bar_code": "".join(random.choices(string.digits, k=13)),
                "sku_specs_json": {
                    "颜色": random.choice(["红", "蓝", "黑", "白"]),
                    "尺寸": random.choice(["S", "M", "L", "XL"]),
                },
                "unit": random.choice(["件", "个", "箱", "套"]),
                "origin_price": origin_price,
                "sale_price": sale_price,
                "cost_price": cost_price,
                "warning_stock": random.randint(10, 100),
                "is_hot_sale": random.choice([0, 0, 0, 1]),  # 25%热销
                "is_new": random.choice([0, 0, 1]),  # 33%新品
                "is_deleted": 0,
                "shelf_status": random.choice([1, 1, 1, 2]),  # 75%上架
                "etl_date": ETL_DATE,
            }
            skus.append(sku)

        self.cache.skus = skus
        return skus

    # ==================== 批次3: 活动与优惠券 ====================

    def generate_promotions(self) -> list[dict]:
        """生成促销活动数据"""
        logger.info(f"生成 {CONFIG['promotion_count']} 个促销活动...")
        promotions = []

        promotion_types = ["满减", "折扣", "秒杀", "拼团"]
        scenes = ["商品", "店铺", "平台"]

        for i in range(CONFIG["promotion_count"]):
            promo_id = self._next_id("promotion")
            start_time = self._random_time_in_range(START_DATE, END_DATE)
            end_time = start_time + timedelta(days=random.randint(1, 30))
            promo_type = random.choice(promotion_types)

            threshold = Decimal(str(round(random.uniform(100, 1000), 2)))

            if promo_type == "满减":
                discount = threshold * Decimal(str(round(random.uniform(0.1, 0.3), 2)))
                discount_rate = None
            elif promo_type == "折扣":
                discount = None
                discount_rate = Decimal(str(round(random.uniform(0.7, 0.95), 4)))
            else:
                discount = None
                discount_rate = None

            promotion = {
                "id": promo_id,
                "promotion_id": promo_id,
                "promotion_name": f"{random.choice(['春季', '夏季', '秋季', '冬季', '年中', '年终'])}大促-{promo_type}",
                "promotion_type": promo_type,
                "promotion_scene": random.choice(scenes),
                "promotion_level": random.randint(1, 10),
                "start_time": start_time,
                "end_time": end_time,
                "rule_desc": f"满{threshold}减{discount}"
                if discount
                else f"打{discount_rate * 10}折"
                if discount_rate
                else "限时特惠",
                "threshold_amount": threshold,
                "discount_amount": discount,
                "discount_rate": discount_rate,
                "max_discount_amount": discount * Decimal("2") if discount else None,
                "sponsor_type": random.choice([1, 2, 3]),
                "sponsor_id": random.choice([s["shop_id"] for s in self.cache.shops])
                if random.random() > 0.5
                else None,
                "status": 1 if end_time > datetime.now() else random.choice([0, 1]),
                "etl_date": ETL_DATE,
            }
            promotions.append(promotion)

        self.cache.promotions = promotions
        return promotions

    def generate_coupons(self) -> list[dict]:
        """生成优惠券数据"""
        logger.info(f"生成 {CONFIG['coupon_count']} 张优惠券...")
        coupons = []

        coupon_types = ["满减券", "折扣券", "运费券", "品类券"]
        scope_types = ["全平台", "店铺", "SPU", "SKU", "类目"]

        for i in range(CONFIG["coupon_count"]):
            coupon_id = self._next_id("coupon")
            coupon_type = random.choice(coupon_types)

            issue_start = self._random_time_in_range(START_DATE, END_DATE)
            issue_end = issue_start + timedelta(days=random.randint(7, 60))
            use_start = issue_start
            use_end = issue_end + timedelta(days=random.randint(7, 30))

            threshold = Decimal(str(round(random.uniform(50, 500), 2)))

            if coupon_type == "满减券":
                discount = threshold * Decimal(str(round(random.uniform(0.1, 0.2), 2)))
                rate = None
            elif coupon_type == "折扣券":
                discount = None
                rate = Decimal(str(round(random.uniform(0.8, 0.95), 4)))
            elif coupon_type == "运费券":
                discount = Decimal("10")
                rate = None
                threshold = Decimal("0")
            else:
                discount = threshold * Decimal("0.15")
                rate = None

            total = random.randint(1000, 10000)
            received = random.randint(0, total)
            used = random.randint(0, received)

            coupon = {
                "id": coupon_id,
                "coupon_id": coupon_id,
                "coupon_name": f"{random.choice(['新人', '会员', '节日', '限时'])}专享{coupon_type}",
                "coupon_type": coupon_type,
                "coupon_scope_type": random.choice(scope_types),
                "coupon_scope_id": None,
                "threshold_amount": threshold,
                "discount_amount": discount,
                "discount_rate": rate,
                "max_discount_amount": discount * Decimal("2")
                if discount and discount > 50
                else discount,
                "issue_start_time": issue_start,
                "issue_end_time": issue_end,
                "use_start_time": use_start,
                "use_end_time": use_end,
                "total_issue_cnt": total,
                "received_cnt": received,
                "used_cnt": used,
                "status": 1 if use_end > datetime.now() else random.choice([0, 1]),
                "etl_date": ETL_DATE,
            }
            coupons.append(coupon)

        self.cache.coupons = coupons
        return coupons

    # 继续添加批次4-6的方法到 WarehouseDataGenerator 类

    # ==================== 批次4: 核心交易数据 ====================

    def generate_orders(self) -> tuple[list[dict], list[dict], list[dict]]:
        """生成订单及相关分摊数据

        返回: (orders, activity_apportions, coupon_apportions)
        """
        logger.info(f"生成 {CONFIG['order_count']} 个订单...")
        orders = []
        activity_apportions = []
        coupon_apportions = []

        # 跟踪用户首单状态
        user_first_order = {}

        for i in range(CONFIG["order_count"]):
            order_detail_id = self._next_id("order_detail")
            order_id = order_detail_id  # 简化：一对一关系

            # 选择用户、店铺、SKU
            user = random.choice(self.cache.users)
            shop = random.choice(self.cache.shops)
            sku = random.choice(self.cache.skus)
            spu = next(
                (s for s in self.cache.spus if s["spu_id"] == sku["spu_id"]), None
            )

            # 确定是否首单
            is_first = 0
            if user["user_id"] not in user_first_order:
                is_first = 1
                user_first_order[user["user_id"]] = order_id

            # 生成订单时间（促销高峰加权）
            order_time = self._weighted_date()

            # 基础价格计算
            sku_num = random.randint(1, 5)
            sku_price = sku["sale_price"]
            origin_amount = sku_price * sku_num

            # 优惠分摊
            platform_discount = Decimal("0")
            shop_discount = Decimal("0")
            activity_discount = Decimal("0")
            coupon_discount = Decimal("0")
            points_discount = Decimal("0")

            # 40%概率参与活动
            if random.random() < 0.4 and self.cache.promotions:
                promo = random.choice(self.cache.promotions)
                if origin_amount >= (promo["threshold_amount"] or Decimal("0")):
                    if promo["discount_amount"]:
                        activity_discount = min(
                            promo["discount_amount"], origin_amount * Decimal("0.3")
                        )
                    elif promo["discount_rate"]:
                        activity_discount = origin_amount * (
                            Decimal("1") - promo["discount_rate"]
                        )

                    # 记录活动分摊
                    activity_apportions.append(
                        {
                            "id": self._next_id("activity_apportion"),
                            "order_detail_activity_id": self._next_id(
                                "order_detail_activity"
                            ),
                            "order_detail_id": order_detail_id,
                            "order_id": order_id,
                            "promotion_id": promo["promotion_id"],
                            "promotion_type": promo["promotion_type"],
                            "promotion_level": promo["promotion_level"],
                            "promotion_discount_amount": activity_discount,
                            "rule_snapshot": promo["rule_desc"],
                            "order_create_time": order_time,
                            "etl_date": ETL_DATE,
                        }
                    )

            # 40%概率使用优惠券
            if random.random() < 0.4 and self.cache.coupons:
                coupon = random.choice(self.cache.coupons)
                payable_after_activity = origin_amount - activity_discount
                if payable_after_activity >= (
                    coupon["threshold_amount"] or Decimal("0")
                ):
                    if coupon["discount_amount"]:
                        coupon_discount = min(
                            coupon["discount_amount"],
                            payable_after_activity * Decimal("0.2"),
                        )
                    elif coupon["discount_rate"]:
                        coupon_discount = payable_after_activity * (
                            Decimal("1") - coupon["discount_rate"]
                        )

                    # 记录优惠券分摊
                    coupon_apportions.append(
                        {
                            "id": self._next_id("coupon_apportion"),
                            "order_detail_coupon_id": self._next_id(
                                "order_detail_coupon"
                            ),
                            "order_detail_id": order_detail_id,
                            "order_id": order_id,
                            "coupon_id": coupon["coupon_id"],
                            "coupon_user_id": user["user_id"],
                            "coupon_type": coupon["coupon_type"],
                            "coupon_scope_type": coupon["coupon_scope_type"],
                            "coupon_discount_amount": coupon_discount,
                            "coupon_batch_no": f"BATCH{coupon['coupon_id']:06d}",
                            "coupon_receive_time": order_time
                            - timedelta(days=random.randint(1, 7)),
                            "coupon_use_time": order_time,
                            "order_create_time": order_time,
                            "etl_date": ETL_DATE,
                        }
                    )

            # 计算应付金额
            total_discount = (
                platform_discount
                + shop_discount
                + activity_discount
                + coupon_discount
                + points_discount
            )
            payable_amount = origin_amount - total_discount
            freight = (
                Decimal(str(round(random.uniform(0, 20), 2)))
                if payable_amount < Decimal("99")
                else Decimal("0")
            )
            payable_amount += freight

            # 订单状态流转
            # 15% 未支付
            if random.random() < 0.15:
                order_status = "待支付"
                pay_time = None
                cancel_time = None
                paid_amount = Decimal("0")
            else:
                # 85% 已支付
                order_status = "已支付"
                pay_delay = random.randint(1, 3600)  # 1秒到1小时内支付
                pay_time = order_time + timedelta(seconds=pay_delay)
                paid_amount = payable_amount
                cancel_time = None

            order = {
                "id": order_detail_id,
                "order_detail_id": order_detail_id,
                "order_id": order_id,
                "parent_order_id": None,
                "trade_no": f"TN{order_time.strftime('%Y%m%d%H%M%S')}{order_id:010d}",
                "order_no": f"ON{order_time.strftime('%Y%m%d%H%M%S')}{order_id:010d}",
                "order_source": random.choice(["APP", "H5", "PC", "MINI"]),
                "order_scene": random.choice(
                    ["普通", "普通", "普通", "秒杀", "拼团", "预售"]
                ),
                "order_status": order_status,
                "user_id": user["user_id"],
                "shop_id": shop["shop_id"],
                "seller_id": shop["seller_id"],
                "sku_id": sku["sku_id"],
                "spu_id": sku["spu_id"],
                "category_id": sku["category_id"],
                "brand_id": sku["brand_id"],
                "province_code": user["province_code"],
                "city_code": user["city_code"],
                "district_code": user["district_code"],
                "is_first_order": is_first,
                "is_cross_border": random.choice([0, 0, 0, 1]),
                "is_pre_sale": spu["is_presale"] if spu else 0,
                "is_gift": random.choice([0, 0, 0, 0, 1]),
                "is_risk_order": random.choice([0, 0, 0, 0, 0, 1]),
                "sku_num": sku_num,
                "sku_origin_price": sku["origin_price"],
                "sku_sale_price": sku_price,
                "order_detail_amount": origin_amount,
                "platform_discount_amount": platform_discount,
                "shop_discount_amount": shop_discount,
                "activity_discount_amount": activity_discount,
                "coupon_discount_amount": coupon_discount,
                "points_discount_amount": points_discount,
                "freight_amount": freight,
                "tax_amount": Decimal("0"),
                "payable_amount": payable_amount,
                "paid_amount": paid_amount,
                "cost_amount": sku["cost_price"] * sku_num,
                "order_create_time": order_time,
                "order_confirm_time": order_time
                + timedelta(minutes=random.randint(1, 10)),
                "order_pay_time": pay_time,
                "order_cancel_time": cancel_time,
                "etl_date": order_time.date(),
            }
            orders.append(order)

        self.cache.orders = orders
        return orders, activity_apportions, coupon_apportions

    # ==================== 批次5: 履约数据 ====================

    def generate_payments(self, orders: list[dict]) -> list[dict]:
        """根据订单生成支付明细"""
        logger.info("生成支付明细...")
        payments = []

        paid_orders = [o for o in orders if o["order_pay_time"] is not None]
        logger.info(f"已支付订单: {len(paid_orders)}")

        for order in paid_orders:
            pay_id = self._next_id("payment")
            payment_type = random.choice(self.cache.payment_types)

            payment = {
                "id": pay_id,
                "pay_detail_id": pay_id,
                "pay_order_no": f"PAY{order['order_pay_time'].strftime('%Y%m%d%H%M%S')}{pay_id:010d}",
                "third_party_pay_no": f"TP{random.randint(100000000000, 999999999999)}"
                if payment_type["is_online"]
                else None,
                "order_id": order["order_id"],
                "order_detail_id": order["order_detail_id"],
                "user_id": order["user_id"],
                "shop_id": order["shop_id"],
                "seller_id": order["seller_id"],
                "payment_type_code": payment_type["payment_type_code"],
                "payment_channel_code": payment_type["channel_code"],
                "pay_scene": random.choice(["收银台", "自动扣款"]),
                "pay_status": "成功",
                "currency_code": "CNY",
                "total_pay_amount": order["paid_amount"],
                "cash_pay_amount": order["paid_amount"],
                "coupon_pay_amount": Decimal("0"),
                "points_pay_amount": Decimal("0"),
                "balance_pay_amount": Decimal("0"),
                "installment_cnt": random.choice([3, 6, 12])
                if payment_type["is_installment"]
                else None,
                "installment_fee_amount": Decimal(str(round(random.uniform(5, 50), 2)))
                if payment_type["is_installment"]
                else Decimal("0"),
                "pay_success_time": order["order_pay_time"],
                "pay_fail_reason": None,
                "etl_date": order["order_pay_time"].date(),
            }
            payments.append(payment)

        return payments

    def generate_deliveries(self, orders: list[dict]) -> list[dict]:
        """根据已支付订单生成发货明细"""
        logger.info("生成发货明细...")
        deliveries = []

        paid_orders = [o for o in orders if o["order_pay_time"] is not None]

        for order in paid_orders:
            # 5% 支付后取消，不发货
            if random.random() < 0.05:
                continue

            delivery_id = self._next_id("delivery")
            logistics = random.choice(self.cache.logistics_companies)

            # 发货时间：支付后 1-48 小时
            pay_time = order["order_pay_time"]
            delivery_time = pay_time + timedelta(hours=random.randint(1, 48))

            # 出库时间：发货前 1-12 小时
            outbound_time = delivery_time - timedelta(hours=random.randint(1, 12))

            # 签收状态
            delivery_status = random.choice(
                [
                    "运输中",
                    "运输中",
                    "运输中",
                    "已签收",
                    "已签收",
                    "已签收",
                    "已签收",
                    "拒收",
                ]
            )

            if delivery_status == "已签收":
                # 签收时间：发货后 1-5 天
                sign_time = delivery_time + timedelta(days=random.randint(1, 5))
            elif delivery_status == "拒收":
                sign_time = None
            else:
                sign_time = None

            # 生成运单号
            tracking_no = "".join(random.choices(string.digits, k=15))

            delivery = {
                "id": delivery_id,
                "delivery_detail_id": delivery_id,
                "delivery_no": f"DEL{delivery_time.strftime('%Y%m%d%H%M%S')}{delivery_id:010d}",
                "order_id": order["order_id"],
                "order_detail_id": order["order_detail_id"],
                "user_id": order["user_id"],
                "shop_id": order["shop_id"],
                "warehouse_id": random.randint(1, 50),
                "logistics_company_id": logistics["logistics_company_id"],
                "tracking_no": tracking_no,
                "delivery_status": delivery_status,
                "delivery_type": random.choice(
                    ["快递", "快递", "快递", "同城", "门店自提"]
                ),
                "receiver_name": self._mask_name(faker.name()),
                "receiver_phone": self._mask_phone(faker.phone_number()),
                "receiver_province_code": order["province_code"],
                "receiver_city_code": order["city_code"],
                "receiver_district_code": order["district_code"],
                "receiver_address": self._mask_address(faker.address()),
                "package_cnt": random.randint(1, 3),
                "total_weight": round(random.uniform(0.5, 5), 3),
                "freight_amount": order["freight_amount"],
                "outbound_time": outbound_time,
                "delivery_time": delivery_time,
                "sign_time": sign_time,
                "etl_date": delivery_time.date(),
            }
            deliveries.append(delivery)

        return deliveries

    def generate_refunds(
        self, orders: list[dict], deliveries: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """生成退款明细和退款支付明细"""
        logger.info("生成退款数据...")
        refunds = []
        refund_payments = []

        # 已支付订单中约10%申请退款
        paid_orders = [o for o in orders if o["order_pay_time"] is not None]
        refund_candidates = random.sample(
            paid_orders, min(len(paid_orders) // 10, CONFIG["order_count"] // 10)
        )

        # 已签收订单可以退货退款，未签收可以仅退款
        delivery_map = {d["order_id"]: d for d in deliveries}

        for order in refund_candidates:
            refund_id = self._next_id("refund")
            delivery = delivery_map.get(order["order_id"])

            # 根据是否签收决定退款类型
            if delivery and delivery["delivery_status"] == "已签收":
                refund_type = random.choice(["退货退款", "退货退款", "仅退款"])
            else:
                refund_type = "仅退款"

            # 申请时间：支付后/签收后 1-30 天
            base_time = (
                delivery["sign_time"]
                if delivery and delivery["sign_time"]
                else order["order_pay_time"]
            )
            apply_time = base_time + timedelta(days=random.randint(1, 30))

            # 退款金额：全额或部分
            if random.random() < 0.8:
                refund_amount = order["paid_amount"]
            else:
                refund_amount = order["paid_amount"] * Decimal(
                    str(round(random.uniform(0.3, 0.9), 2))
                )

            refund_status = random.choice(
                ["退款成功", "退款成功", "退款成功", "退款中", "退款关闭"]
            )

            refund = {
                "id": refund_id,
                "refund_detail_id": refund_id,
                "refund_no": f"REF{apply_time.strftime('%Y%m%d%H%M%S')}{refund_id:010d}",
                "order_id": order["order_id"],
                "order_detail_id": order["order_detail_id"],
                "user_id": order["user_id"],
                "shop_id": order["shop_id"],
                "sku_id": order["sku_id"],
                "refund_type": refund_type,
                "refund_reason_code": f"RR{random.randint(1, 20):02d}",
                "refund_reason_desc": random.choice(
                    ["不喜欢", "质量问题", "与描述不符", "未按时间发货", "其他"]
                ),
                "refund_status": refund_status,
                "refund_apply_amount": refund_amount,
                "refund_approve_amount": refund_amount
                if refund_status in ["退款成功", "退款中"]
                else Decimal("0"),
                "refund_success_amount": refund_amount
                if refund_status == "退款成功"
                else Decimal("0"),
                "refund_freight_amount": Decimal("0"),
                "refund_tax_amount": Decimal("0"),
                "is_quality_issue": 1
                if "质量" in str(refund["refund_reason_desc"])
                else 0,
                "need_return_goods": 1 if refund_type == "退货退款" else 0,
                "return_tracking_no": "".join(random.choices(string.digits, k=15))
                if refund_type == "退货退款"
                else None,
                "apply_time": apply_time,
                "audit_time": apply_time + timedelta(hours=random.randint(1, 48))
                if refund_status != "退款关闭"
                else None,
                "receive_return_time": apply_time
                + timedelta(days=random.randint(3, 10))
                if refund_type == "退货退款" and refund_status == "退款成功"
                else None,
                "refund_success_time": apply_time
                + timedelta(days=random.randint(1, 15))
                if refund_status == "退款成功"
                else None,
                "close_time": apply_time + timedelta(days=7)
                if refund_status == "退款关闭"
                else None,
                "etl_date": apply_time.date(),
            }
            refunds.append(refund)

            # 退款支付明细
            if refund_status == "退款成功":
                rp_id = self._next_id("refund_payment")
                refund_payment = {
                    "id": rp_id,
                    "refund_pay_detail_id": rp_id,
                    "refund_no": refund["refund_no"],
                    "refund_detail_id": refund_id,
                    "pay_detail_id": None,  # 简化处理
                    "order_id": order["order_id"],
                    "order_detail_id": order["order_detail_id"],
                    "user_id": order["user_id"],
                    "payment_type_code": random.choice(self.cache.payment_types)[
                        "payment_type_code"
                    ],
                    "refund_channel_code": None,
                    "refund_status": "成功",
                    "refund_amount": refund_amount,
                    "refund_account_type": random.choice(["原路退回", "余额"]),
                    "refund_apply_time": apply_time,
                    "refund_pay_time": refund["refund_success_time"],
                    "refund_fail_reason": None,
                    "etl_date": refund["refund_success_time"].date()
                    if refund["refund_success_time"]
                    else apply_time.date(),
                }
                refund_payments.append(refund_payment)

        return refunds, refund_payments

    # ==================== 批次6: 互动与流量数据 ====================

    def generate_cart_adds(self) -> list[dict]:
        """生成加购数据"""
        logger.info(f"生成 {CONFIG['cart_add_count']} 条加购记录...")
        cart_adds = []

        for i in range(CONFIG["cart_add_count"]):
            event_time = self._weighted_date()
            user = random.choice(self.cache.users)
            sku = random.choice(self.cache.skus)

            cart_add = {
                "id": self._next_id("cart_add"),
                "cart_add_id": self._next_id("cart_add_id"),
                "event_no": f"CE{event_time.strftime('%Y%m%d%H%M%S')}{random.randint(100000, 999999)}",
                "user_id": user["user_id"]
                if random.random() > 0.1
                else None,  # 10%游客
                "device_id": faker.uuid4()[:32],
                "session_id": faker.uuid4()[:32],
                "shop_id": sku["shop_id"],
                "sku_id": sku["sku_id"],
                "spu_id": sku["spu_id"],
                "category_id": sku["category_id"],
                "cart_source": random.choice(["商品详情", "搜索", "推荐", "活动页"]),
                "client_type": random.choice(["iOS", "Android", "H5", "PC", "小程序"]),
                "channel_code": random.choice(["APP", "WEB", "MINI"]),
                "add_sku_num": random.randint(1, 5),
                "sku_price": sku["sale_price"],
                "event_time": event_time,
                "etl_date": event_time.date(),
            }
            cart_adds.append(cart_add)

        return cart_adds

    def generate_favor_adds(self) -> list[dict]:
        """生成收藏数据"""
        logger.info(f"生成 {CONFIG['favor_add_count']} 条收藏记录...")
        favor_adds = []

        for i in range(CONFIG["favor_add_count"]):
            event_time = self._weighted_date()
            user = random.choice(self.cache.users)
            favor_type = random.choice(["商品", "店铺"])

            if favor_type == "商品":
                sku = random.choice(self.cache.skus)
                spu_id = sku["spu_id"]
                shop_id = sku["shop_id"]
                category_id = sku["category_id"]
            else:
                shop = random.choice(self.cache.shops)
                spu_id = None
                shop_id = shop["shop_id"]
                category_id = None

            favor_add = {
                "id": self._next_id("favor_add"),
                "favor_add_id": self._next_id("favor_add_id"),
                "event_no": f"FE{event_time.strftime('%Y%m%d%H%M%S')}{random.randint(100000, 999999)}",
                "user_id": user["user_id"],
                "shop_id": shop_id,
                "sku_id": sku["sku_id"] if favor_type == "商品" else None,
                "spu_id": spu_id,
                "favor_type": favor_type,
                "client_type": random.choice(["iOS", "Android", "H5", "PC", "小程序"]),
                "channel_code": random.choice(["APP", "WEB", "MINI"]),
                "event_time": event_time,
                "etl_date": event_time.date(),
            }
            favor_adds.append(favor_add)

        return favor_adds

    def generate_comments(
        self, orders: list[dict], deliveries: list[dict]
    ) -> list[dict]:
        """生成评价数据 - 只评价已签收的订单"""
        logger.info(f"生成 {CONFIG['comment_count']} 条评价...")
        comments = []

        # 已签收订单
        signed_deliveries = [d for d in deliveries if d["delivery_status"] == "已签收"]

        # 随机选择30%进行评价
        comment_count = min(CONFIG["comment_count"], len(signed_deliveries))
        candidates = random.sample(signed_deliveries, comment_count)

        order_map = {o["order_id"]: o for o in orders}

        for delivery in candidates:
            order = order_map.get(delivery["order_id"])
            if not order:
                continue

            comment_time = delivery["sign_time"] + timedelta(days=random.randint(0, 30))
            comment_level = random.choices([1, 2, 3, 4, 5], weights=[2, 3, 5, 15, 25])[
                0
            ]

            comment = {
                "id": self._next_id("comment"),
                "comment_detail_id": self._next_id("comment_detail_id"),
                "comment_id": self._next_id("comment_id"),
                "order_id": order["order_id"],
                "order_detail_id": order["order_detail_id"],
                "user_id": order["user_id"],
                "shop_id": order["shop_id"],
                "sku_id": order["sku_id"],
                "spu_id": order["spu_id"],
                "category_id": order["category_id"],
                "comment_level": comment_level,
                "is_anonymous": random.choice([0, 0, 0, 1]),
                "is_with_image": random.choice([0, 0, 0, 0, 1]),
                "is_with_video": random.choice([0, 0, 0, 0, 0, 1]),
                "is_append_comment": random.choice([0, 0, 0, 0, 1]),
                "comment_content": faker.sentence()[:200]
                if random.random() > 0.1
                else None,
                "service_score": random.randint(1, 5),
                "logistics_score": random.randint(1, 5),
                "description_score": random.randint(1, 5),
                "sensitive_tag": random.choice(["", "", "", "", "敏感词"])
                if random.random() > 0.95
                else None,
                "sentiment": random.choice(["正向", "正向", "正向", "中性", "负向"]),
                "comment_time": comment_time,
                "etl_date": comment_time.date(),
            }
            comments.append(comment)

        return comments

    def generate_inventory_changes(self, orders: list[dict]) -> list[dict]:
        """生成库存变更数据"""
        logger.info("生成库存变更数据...")
        inventory_changes = []

        for order in orders:
            # 下单锁定
            lock_id = self._next_id("inventory")
            lock_time = order["order_create_time"]

            inventory_changes.append(
                {
                    "id": lock_id,
                    "inventory_change_id": lock_id,
                    "change_no": f"IC{lock_time.strftime('%Y%m%d%H%M%S')}{lock_id:010d}",
                    "sku_id": order["sku_id"],
                    "spu_id": order["spu_id"],
                    "shop_id": order["shop_id"],
                    "warehouse_id": random.randint(1, 50),
                    "change_type": "锁定",
                    "biz_type": "下单",
                    "biz_id": str(order["order_id"]),
                    "before_stock_qty": random.randint(100, 1000),
                    "change_qty": -order["sku_num"],
                    "after_stock_qty": 0,  # 简化
                    "before_lock_qty": 0,
                    "change_lock_qty": order["sku_num"],
                    "after_lock_qty": order["sku_num"],
                    "unit_cost": order["cost_amount"] / order["sku_num"]
                    if order["sku_num"] > 0
                    else Decimal("0"),
                    "total_cost_change": Decimal("0"),
                    "operator_id": None,
                    "operator_type": "系统",
                    "remark": "订单下单锁定库存",
                    "change_time": lock_time,
                    "etl_date": lock_time.date(),
                }
            )

            # 支付扣减
            if order["order_pay_time"]:
                deduct_id = self._next_id("inventory")
                deduct_time = order["order_pay_time"]

                inventory_changes.append(
                    {
                        "id": deduct_id,
                        "inventory_change_id": deduct_id,
                        "change_no": f"IC{deduct_time.strftime('%Y%m%d%H%M%S')}{deduct_id:010d}",
                        "sku_id": order["sku_id"],
                        "spu_id": order["spu_id"],
                        "shop_id": order["shop_id"],
                        "warehouse_id": random.randint(1, 50),
                        "change_type": "出库",
                        "biz_type": "支付",
                        "biz_id": str(order["order_id"]),
                        "before_stock_qty": random.randint(100, 1000),
                        "change_qty": -order["sku_num"],
                        "after_stock_qty": 0,
                        "before_lock_qty": order["sku_num"],
                        "change_lock_qty": -order["sku_num"],
                        "after_lock_qty": 0,
                        "unit_cost": order["cost_amount"] / order["sku_num"]
                        if order["sku_num"] > 0
                        else Decimal("0"),
                        "total_cost_change": -order["cost_amount"],
                        "operator_id": None,
                        "operator_type": "系统",
                        "remark": "支付成功扣减库存",
                        "change_time": deduct_time,
                        "etl_date": deduct_time.date(),
                    }
                )
            else:
                # 未支付释放锁定
                release_id = self._next_id("inventory")
                release_time = order["order_create_time"] + timedelta(
                    hours=24
                )  # 24小时后释放

                inventory_changes.append(
                    {
                        "id": release_id,
                        "inventory_change_id": release_id,
                        "change_no": f"IC{release_time.strftime('%Y%m%d%H%M%S')}{release_id:010d}",
                        "sku_id": order["sku_id"],
                        "spu_id": order["spu_id"],
                        "shop_id": order["shop_id"],
                        "warehouse_id": random.randint(1, 50),
                        "change_type": "解锁",
                        "biz_type": "取消",
                        "biz_id": str(order["order_id"]),
                        "before_stock_qty": random.randint(100, 1000),
                        "change_qty": order["sku_num"],
                        "after_stock_qty": 0,
                        "before_lock_qty": order["sku_num"],
                        "change_lock_qty": -order["sku_num"],
                        "after_lock_qty": 0,
                        "unit_cost": Decimal("0"),
                        "total_cost_change": Decimal("0"),
                        "operator_id": None,
                        "operator_type": "系统",
                        "remark": "超时未支付释放库存",
                        "change_time": release_time,
                        "etl_date": release_time.date(),
                    }
                )

        return inventory_changes

    def generate_page_views(self) -> list[dict]:
        """生成页面访问数据"""
        logger.info(f"生成 {CONFIG['page_view_count']} 条页面访问记录...")
        page_views = []

        page_types = ["首页", "详情", "活动", "搜索", "下单", "分类"]

        for i in range(CONFIG["page_view_count"]):
            event_time = self._weighted_date()
            user = random.choice(self.cache.users) if random.random() > 0.3 else None
            page_type = random.choice(page_types)

            # 根据页面类型确定业务ID
            if page_type == "详情":
                sku = random.choice(self.cache.skus)
                business_id = str(sku["sku_id"])
                business_type = "SKU"
            elif page_type == "活动":
                promo = random.choice(self.cache.promotions)
                business_id = str(promo["promotion_id"])
                business_type = "ACTIVITY"
            else:
                business_id = None
                business_type = None

            page_view = {
                "id": self._next_id("page_view"),
                "page_view_id": self._next_id("page_view_id"),
                "event_no": f"PV{event_time.strftime('%Y%m%d%H%M%S')}{random.randint(100000, 999999)}",
                "user_id": user["user_id"] if user else None,
                "device_id": faker.uuid4()[:32],
                "session_id": faker.uuid4()[:32],
                "page_id": f"PAGE_{page_type}_{random.randint(1, 100)}",
                "page_name": f"{page_type}页面",
                "last_page_id": None,
                "page_type": page_type,
                "business_id": business_id,
                "business_type": business_type,
                "channel_code": random.choice(["APP", "WEB", "MINI"]),
                "client_type": random.choice(["iOS", "Android", "H5", "PC", "小程序"]),
                "app_version": random.choice(["1.0.0", "1.1.0", "1.2.0", "2.0.0"]),
                "os_type": random.choice(["iOS", "Android", "Windows", "MacOS"]),
                "ip": faker.ipv4(),
                "province_code": user["province_code"]
                if user
                else str(random.randint(110000, 650000)),
                "city_code": user["city_code"]
                if user
                else str(random.randint(110100, 659000)),
                "stay_duration_sec": random.randint(1, 300),
                "is_bounce": random.choice([0, 0, 0, 1]),
                "event_time": event_time,
                "etl_date": event_time.date(),
            }
            page_views.append(page_view)

        return page_views

    def generate_searches(self) -> list[dict]:
        """生成搜索数据"""
        logger.info(f"生成 {CONFIG['search_count']} 条搜索记录...")
        searches = []

        # 常见搜索词
        keywords = [
            "手机",
            "电脑",
            "衣服",
            "鞋子",
            "包包",
            "零食",
            "饮料",
            "化妆品",
            "家具",
            "家电",
        ]

        for i in range(CONFIG["search_count"]):
            event_time = self._weighted_date()
            user = random.choice(self.cache.users) if random.random() > 0.3 else None
            keyword = random.choice(keywords)

            has_result = random.random() > 0.1  # 90%有结果
            clicked = has_result and random.random() > 0.3  # 70%点击

            search = {
                "id": self._next_id("search"),
                "search_detail_id": self._next_id("search_detail_id"),
                "event_no": f"SE{event_time.strftime('%Y%m%d%H%M%S')}{random.randint(100000, 999999)}",
                "user_id": user["user_id"] if user else None,
                "device_id": faker.uuid4()[:32],
                "session_id": faker.uuid4()[:32],
                "search_keyword": keyword,
                "search_source": random.choice(["首页", "分类页", "店铺页"]),
                "result_total_cnt": random.randint(0, 1000) if has_result else 0,
                "click_rank": random.randint(1, 10) if clicked else None,
                "click_sku_id": random.choice(self.cache.skus)["sku_id"]
                if clicked
                else None,
                "click_spu_id": None,
                "is_no_result": 0 if has_result else 1,
                "is_search_success": 1 if has_result else 0,
                "channel_code": random.choice(["APP", "WEB", "MINI"]),
                "client_type": random.choice(["iOS", "Android", "H5", "PC", "小程序"]),
                "event_time": event_time,
                "etl_date": event_time.date(),
            }
            searches.append(search)

        return searches

    # ==================== 数据库操作 ====================

    async def _insert_batch(
        self, conn, table_name: str, data: list[dict], columns: list[str]
    ):
        """批量插入数据"""
        if not data:
            return 0

        # 构建INSERT语句
        placeholders = ", ".join(["%s"] * len(columns))
        columns_str = ", ".join(columns)
        sql = f"INSERT INTO `{table_name}` ({columns_str}) VALUES ({placeholders})"

        # 分批插入
        total_inserted = 0
        batch_size = CONFIG["batch_size"]

        async with conn.cursor() as cur:
            for i in range(0, len(data), batch_size):
                batch = data[i : i + batch_size]
                values = []
                for row in batch:
                    row_values = []
                    for col in columns:
                        val = row.get(col)
                        # 处理datetime和date类型
                        if isinstance(val, datetime):
                            val = val.strftime("%Y-%m-%d %H:%M:%S")
                        elif isinstance(val, date) and not isinstance(val, datetime):
                            val = val.strftime("%Y-%m-%d")
                        elif isinstance(val, Decimal):
                            val = float(val)
                        row_values.append(val)
                    values.append(tuple(row_values))

                await cur.executemany(sql, values)
                total_inserted += len(batch)

        return total_inserted

    async def insert_all_data(self, all_data: dict):
        """将所有数据插入数据库"""
        self.conn = await asyncmy.connect(**DB_CONFIG)

        try:
            # 批次1: 维度基础数据
            logger.info("插入维度基础数据...")

            await self._insert_batch(
                self.conn,
                "dwd_dim_user_info_df",
                all_data["users"],
                [
                    "user_id",
                    "user_name",
                    "nick_name",
                    "gender",
                    "birthday",
                    "phone",
                    "email",
                    "register_time",
                    "register_channel_code",
                    "register_source",
                    "user_level",
                    "user_tag",
                    "is_vip",
                    "province_code",
                    "city_code",
                    "district_code",
                    "occupation",
                    "income_level",
                    "education_level",
                    "marital_status",
                    "user_status",
                    "first_order_time",
                    "last_order_time",
                    "etl_date",
                ],
            )
            logger.info(f"  用户表: {len(all_data['users'])} 条")

            await self._insert_batch(
                self.conn,
                "dwd_dim_shop_info_df",
                all_data["shops"],
                [
                    "shop_id",
                    "shop_name",
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
                    "is_global",
                    "is_deleted",
                    "shop_status",
                    "etl_date",
                ],
            )
            logger.info(f"  店铺表: {len(all_data['shops'])} 条")

            await self._insert_batch(
                self.conn,
                "dwd_dim_category_info_df",
                all_data["categories"],
                [
                    "category_id",
                    "category_name",
                    "category_level",
                    "parent_category_id",
                    "parent_category_name",
                    "root_category_id",
                    "root_category_name",
                    "is_leaf",
                    "sort_order",
                    "category_path",
                    "status",
                    "etl_date",
                ],
            )
            logger.info(f"  类目表: {len(all_data['categories'])} 条")

            # 批次2: 商品数据
            logger.info("插入商品数据...")

            # SPU 表结构简化插入
            await self._insert_batch(
                self.conn,
                "dwd_dim_spu_info_df",
                all_data["spus"],
                [
                    "spu_id",
                    "spu_name",
                    "category_id",
                    "shop_id",
                    "brand_id",
                    "brand_name",
                    "is_virtual",
                    "is_presale",
                    "shelf_status",
                    "on_shelf_time",
                    "etl_date",
                ],
            )
            logger.info(f"  SPU表: {len(all_data['spus'])} 条")

            # SKU 表
            await self._insert_batch(
                self.conn,
                "dwd_dim_sku_info_df",
                all_data["skus"],
                [
                    "sku_id",
                    "sku_name",
                    "spu_id",
                    "shop_id",
                    "category_id",
                    "brand_id",
                    "bar_code",
                    "sku_specs_json",
                    "unit",
                    "origin_price",
                    "sale_price",
                    "cost_price",
                    "warning_stock",
                    "is_hot_sale",
                    "is_new",
                    "shelf_status",
                    "etl_date",
                ],
            )
            logger.info(f"  SKU表: {len(all_data['skus'])} 条")

            # 批次3: 活动与优惠券
            logger.info("插入活动与优惠券数据...")

            await self._insert_batch(
                self.conn,
                "dwd_dim_promotion_info_df",
                all_data["promotions"],
                [
                    "promotion_id",
                    "promotion_name",
                    "promotion_type",
                    "promotion_scene",
                    "promotion_level",
                    "start_time",
                    "end_time",
                    "rule_desc",
                    "threshold_amount",
                    "discount_amount",
                    "discount_rate",
                    "max_discount_amount",
                    "sponsor_type",
                    "sponsor_id",
                    "status",
                    "etl_date",
                ],
            )
            logger.info(f"  活动表: {len(all_data['promotions'])} 条")

            await self._insert_batch(
                self.conn,
                "dwd_dim_coupon_info_df",
                all_data["coupons"],
                [
                    "coupon_id",
                    "coupon_name",
                    "coupon_type",
                    "coupon_scope_type",
                    "coupon_scope_id",
                    "threshold_amount",
                    "discount_amount",
                    "discount_rate",
                    "max_discount_amount",
                    "issue_start_time",
                    "issue_end_time",
                    "use_start_time",
                    "use_end_time",
                    "total_issue_cnt",
                    "received_cnt",
                    "used_cnt",
                    "status",
                    "etl_date",
                ],
            )
            logger.info(f"  优惠券表: {len(all_data['coupons'])} 条")

            # 批次4: 订单数据
            logger.info("插入订单数据...")

            await self._insert_batch(
                self.conn,
                "dwd_fact_trade_order_detail_di",
                all_data["orders"],
                [
                    "order_detail_id",
                    "order_id",
                    "order_status",
                    "user_id",
                    "shop_id",
                    "sku_id",
                    "spu_id",
                    "category_id",
                    "sku_num",
                    "sku_origin_price",
                    "sku_sale_price",
                    "order_detail_amount",
                    "platform_discount_amount",
                    "shop_discount_amount",
                    "activity_discount_amount",
                    "coupon_discount_amount",
                    "points_discount_amount",
                    "freight_amount",
                    "tax_amount",
                    "payable_amount",
                    "paid_amount",
                    "cost_amount",
                    "order_create_time",
                    "order_confirm_time",
                    "order_pay_time",
                    "order_cancel_time",
                    "province_code",
                    "city_code",
                    "district_code",
                    "is_first_order",
                    "is_cross_border",
                    "is_pre_sale",
                    "order_source",
                    "order_scene",
                    "etl_date",
                ],
            )
            logger.info(f"  订单明细表: {len(all_data['orders'])} 条")

            # 批次5: 履约数据
            logger.info("插入履约数据...")

            await self._insert_batch(
                self.conn,
                "dwd_fact_trade_pay_detail_di",
                all_data["payments"],
                [
                    "pay_detail_id",
                    "pay_order_no",
                    "order_id",
                    "user_id",
                    "shop_id",
                    "payment_type_code",
                    "pay_status",
                    "total_pay_amount",
                    "cash_pay_amount",
                    "pay_success_time",
                    "etl_date",
                ],
            )
            logger.info(f"  支付明细表: {len(all_data['payments'])} 条")

            await self._insert_batch(
                self.conn,
                "dwd_fact_trade_delivery_detail_di",
                all_data["deliveries"],
                [
                    "delivery_detail_id",
                    "delivery_no",
                    "order_id",
                    "user_id",
                    "shop_id",
                    "logistics_company_id",
                    "tracking_no",
                    "delivery_status",
                    "delivery_time",
                    "sign_time",
                    "etl_date",
                ],
            )
            logger.info(f"  发货明细表: {len(all_data['deliveries'])} 条")

            # 批次6: 互动数据
            logger.info("插入互动与流量数据...")

            await self._insert_batch(
                self.conn,
                "dwd_fact_interaction_cart_add_di",
                all_data["cart_adds"],
                [
                    "cart_add_id",
                    "user_id",
                    "sku_id",
                    "shop_id",
                    "add_sku_num",
                    "sku_price",
                    "event_time",
                    "etl_date",
                ],
            )
            logger.info(f"  加购表: {len(all_data['cart_adds'])} 条")

            await self._insert_batch(
                self.conn,
                "dwd_fact_service_comment_detail_di",
                all_data["comments"],
                [
                    "comment_detail_id",
                    "comment_id",
                    "order_id",
                    "user_id",
                    "shop_id",
                    "sku_id",
                    "comment_level",
                    "comment_content",
                    "comment_time",
                    "etl_date",
                ],
            )
            logger.info(f"  评价表: {len(all_data['comments'])} 条")

            logger.info("所有数据插入完成！")

        finally:
            await self.conn.ensure_closed()


async def main():
    """主入口"""
    logger.info("=" * 50)
    logger.info("开始生成电商数仓数据")
    logger.info("=" * 50)

    generator = WarehouseDataGenerator()

    # 存储所有生成的数据
    all_data = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[cyan]{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=Console(),
    ) as progress:
        # 批次1: 基础维度
        task1 = progress.add_task("批次1: 基础维度数据", total=6)

        all_data["users"] = generator.generate_users()
        progress.update(task1, advance=1)

        all_data["shops"] = generator.generate_shops()
        progress.update(task1, advance=1)

        all_data["categories"] = generator.generate_categories()
        progress.update(task1, advance=1)

        all_data["brands"] = generator.generate_brands()
        progress.update(task1, advance=1)

        all_data["regions"] = generator.generate_regions()
        progress.update(task1, advance=1)

        all_data["payment_types"] = generator.generate_payment_types()
        all_data["logistics_companies"] = generator.generate_logistics_companies()
        progress.update(task1, advance=1)

        # 批次2: 商品维度
        task2 = progress.add_task("批次2: 商品维度数据", total=2)

        all_data["spus"] = generator.generate_spus()
        progress.update(task2, advance=1)

        all_data["skus"] = generator.generate_skus()
        progress.update(task2, advance=1)

        # 批次3: 活动与优惠券
        task3 = progress.add_task("批次3: 活动与优惠券", total=2)

        all_data["promotions"] = generator.generate_promotions()
        progress.update(task3, advance=1)

        all_data["coupons"] = generator.generate_coupons()
        progress.update(task3, advance=1)

        # 批次4: 核心交易数据
        task4 = progress.add_task("批次4: 订单数据", total=1)

        orders, activity_apportions, coupon_apportions = generator.generate_orders()
        all_data["orders"] = orders
        all_data["activity_apportions"] = activity_apportions
        all_data["coupon_apportions"] = coupon_apportions
        progress.update(task4, advance=1)

        # 批次5: 履约数据
        task5 = progress.add_task("批次5: 履约数据", total=4)

        all_data["payments"] = generator.generate_payments(orders)
        progress.update(task5, advance=1)

        all_data["deliveries"] = generator.generate_deliveries(orders)
        progress.update(task5, advance=1)

        refunds, refund_payments = generator.generate_refunds(
            orders, all_data["deliveries"]
        )
        all_data["refunds"] = refunds
        all_data["refund_payments"] = refund_payments
        progress.update(task5, advance=2)

        # 批次6: 互动与流量数据
        task6 = progress.add_task("批次6: 互动与流量数据", total=6)

        all_data["cart_adds"] = generator.generate_cart_adds()
        progress.update(task6, advance=1)

        all_data["favor_adds"] = generator.generate_favor_adds()
        progress.update(task6, advance=1)

        all_data["comments"] = generator.generate_comments(
            orders, all_data["deliveries"]
        )
        progress.update(task6, advance=1)

        all_data["inventory_changes"] = generator.generate_inventory_changes(orders)
        progress.update(task6, advance=1)

        all_data["page_views"] = generator.generate_page_views()
        progress.update(task6, advance=1)

        all_data["searches"] = generator.generate_searches()
        progress.update(task6, advance=1)

    # 打印生成统计
    logger.info("=" * 50)
    logger.info("数据生成完成！统计如下:")
    logger.info("=" * 50)
    for key, data in all_data.items():
        logger.info(f"  {key}: {len(data)} 条")

    # 插入数据库
    logger.info("=" * 50)
    logger.info("开始插入数据到数据库...")
    logger.info("=" * 50)

    await generator.insert_all_data(all_data)

    logger.info("=" * 50)
    logger.info("所有数据插入完成！")
    logger.info("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
