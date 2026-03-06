"""批次5履约与退款生成规则。"""

PAY_SCENE_OPTIONS = ["收银台", "自动扣款", "合并支付"]
DELIVERY_TYPE_OPTIONS = ["快递", "同城", "门店自提"]
WAREHOUSE_IDS = [1001, 1002, 1003, 1004, 1005, 1006]
REFUND_REASON_OPTIONS = [
    ("NO_WANT", "不想要了"),
    ("SIZE_ERROR", "尺寸/规格不符"),
    ("QUALITY", "商品质量问题"),
    ("DAMAGED", "商品破损"),
    ("LATE", "物流时效不满意"),
]
