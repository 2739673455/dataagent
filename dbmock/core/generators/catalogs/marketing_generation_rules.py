"""批次3营销活动和优惠券生成规则。"""

PROMOTION_TARGET_COUNT = 150
COUPON_TARGET_COUNT = 300

PROMOTION_TYPES = ["满减", "折扣", "秒杀", "拼团"]
PROMOTION_SCENES = ["平台", "店铺", "品牌"]
COUPON_TYPES = ["满减券", "折扣券", "运费券", "品类券"]
COUPON_SCOPE_TYPES = ["全平台", "店铺", "类目"]

CAMPAIGN_SERIES = [
    "开门红",
    "春季焕新",
    "女神节",
    "踏青季",
    "五一大促",
    "618预热",
    "618狂欢",
    "暑期焕新",
    "开学季",
    "国庆焕新",
    "双11预售",
    "双11狂欢",
    "双12年终盛典",
    "年货节",
]

PROMOTION_RULE_TEMPLATES = {
    "满减": "满{threshold}元减{discount}元",
    "折扣": "满{threshold}元打{discount_rate}折，最高减{max_discount}元",
    "秒杀": "限时秒杀直降{discount}元",
    "拼团": "{group_size}人团直降{discount}元",
}

COUPON_NAME_TEMPLATES = {
    "满减券": "{campaign}{scope_name}满{threshold}减{discount}券",
    "折扣券": "{campaign}{scope_name}{discount_rate}折券",
    "运费券": "{campaign}{scope_name}运费券",
    "品类券": "{campaign}{scope_name}品类优惠券",
}

COUPON_THRESHOLD_OPTIONS = [39, 59, 99, 129, 199, 299, 399, 599, 899, 1299]
DISCOUNT_OPTIONS = [5, 10, 15, 20, 30, 50, 80, 100, 150, 200]
DISCOUNT_RATE_OPTIONS = [95, 90, 88, 85, 80, 75, 70]
MAX_DISCOUNT_OPTIONS = [20, 30, 50, 80, 100, 150, 200, 300]
GROUP_SIZE_OPTIONS = [2, 3, 4, 5]
TRANSPORT_COUPON_OPTIONS = [5, 8, 10, 12, 15]
