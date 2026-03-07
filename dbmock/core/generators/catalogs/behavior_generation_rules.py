"""批次5行为、流量、评价与库存生成规则。"""

CART_EVENTS_PER_USER = 8
FAVOR_EVENTS_PER_USER = 3
PAGE_VIEW_EVENTS_PER_USER = 60
SEARCH_EVENTS_PER_USER = 10
COMMENT_RATE = 0.25

CHANNEL_CODES = ["app_store", "xiaomi", "huawei", "wechat", "web", "douyin"]
APP_VERSIONS = ["6.2.1", "6.3.0", "6.4.2", "6.5.1", "6.6.0"]
APP_CLIENT_TYPES = {"iOS", "Android", "小程序"}
CHANNEL_CLIENT_OPTIONS = {
    "app_store": ["iOS"],
    "xiaomi": ["Android"],
    "huawei": ["Android"],
    "wechat": ["小程序", "H5"],
    "web": ["PC", "H5"],
    "douyin": ["H5", "小程序"],
}
CLIENT_OS_OPTIONS = {
    "iOS": ["iOS 17"],
    "Android": ["Android 14", "Android 15"],
    "H5": ["iOS 17", "Android 14", "Android 15", "Windows 11", "macOS 15"],
    "PC": ["Windows 11", "macOS 15"],
    "小程序": ["iOS 17", "Android 14", "Android 15"],
}

CART_SOURCES = ["商品详情", "搜索", "推荐", "活动页"]
SEARCH_SOURCES = ["首页", "分类页", "店铺页"]
PAGE_DEFINITIONS = [
    ("home", "首页", "首页"),
    ("detail", "商品详情页", "详情"),
    ("search", "搜索结果页", "搜索"),
    ("campaign", "活动会场页", "活动"),
    ("order", "下单页", "下单"),
]

SEARCH_KEYWORDS = [
    "手机",
    "电脑",
    "零食",
    "面膜",
    "羽绒服",
    "运动鞋",
    "奶粉",
    "洗发水",
    "咖啡",
    "充电宝",
    "耳机",
    "行李箱",
]

POSITIVE_COMMENTS = [
    "发货很快，商品和描述一致，整体体验很好。",
    "包装完整，做工不错，价格也合适。",
    "质量满意，尺寸合适，下次还会再买。",
    "物流速度快，客服响应及时，值得推荐。",
]
NEUTRAL_COMMENTS = [
    "整体还可以，和预期基本一致。",
    "商品正常使用，没有明显问题。",
    "体验中规中矩，性价比还行。",
]
NEGATIVE_COMMENTS = [
    "到货后体验一般，细节处理有待提升。",
    "和预期有一些差距，整体不算满意。",
    "物流和商品体验都比较普通。",
]

SENSITIVE_TAG_OPTIONS = ["催发货", "包装破损", "客服响应慢", "色差", "尺寸偏小"]

INITIAL_STOCK_BASE = 5000
WAREHOUSE_ID_FALLBACK = 1001
