from typing import Optional
import datetime
import decimal

from sqlalchemy import BigInteger, Column, DECIMAL, Date, DateTime, Index, Integer, String, Table, Text, text
from sqlalchemy.dialects.mysql import INTEGER, TINYINT, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass


class ActivityInfo(Base):
    __tablename__ = 'activity_info'
    __table_args__ = {'comment': '活动表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='活动id')
    activity_name: Mapped[Optional[str]] = mapped_column(String(200), comment='活动名称')
    activity_type: Mapped[Optional[str]] = mapped_column(String(10), comment='活动类型（1：满减，2：折扣）')
    activity_desc: Mapped[Optional[str]] = mapped_column(String(2000), comment='活动描述')
    start_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='开始时间')
    end_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='结束时间')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='修改时间')


class ActivityRule(Base):
    __tablename__ = 'activity_rule'
    __table_args__ = {'comment': '优惠规则'}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment='编号')
    activity_id: Mapped[Optional[int]] = mapped_column(Integer, comment='类型')
    activity_type: Mapped[Optional[str]] = mapped_column(String(20), comment='活动类型')
    condition_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='满减金额')
    condition_num: Mapped[Optional[int]] = mapped_column(BigInteger, comment='满减件数')
    benefit_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='优惠金额')
    benefit_discount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), comment='优惠折扣')
    benefit_level: Mapped[Optional[int]] = mapped_column(BigInteger, comment='优惠级别')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='修改时间')


class ActivitySku(Base):
    __tablename__ = 'activity_sku'
    __table_args__ = {'comment': '活动参与商品'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    activity_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='活动id ')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='sku_id')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='修改时间')


class BaseAttrInfo(Base):
    __tablename__ = 'base_attr_info'
    __table_args__ = {'comment': '属性表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    attr_name: Mapped[str] = mapped_column(String(100), nullable=False, comment='属性名称')
    category_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='分类id')
    category_level: Mapped[Optional[int]] = mapped_column(Integer, comment='分类层级')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='修改时间')


class BaseAttrValue(Base):
    __tablename__ = 'base_attr_value'
    __table_args__ = {'comment': '属性值表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    value_name: Mapped[str] = mapped_column(String(100), nullable=False, comment='属性值名称')
    attr_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='属性id')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class BaseCategory1(Base):
    __tablename__ = 'base_category1'
    __table_args__ = {'comment': '一级分类表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    name: Mapped[str] = mapped_column(String(10), nullable=False, comment='分类名称')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class BaseCategory2(Base):
    __tablename__ = 'base_category2'
    __table_args__ = {'comment': '二级分类表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment='二级分类名称')
    category1_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='一级分类编号')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class BaseCategory3(Base):
    __tablename__ = 'base_category3'
    __table_args__ = {'comment': '三级分类表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment='三级分类名称')
    category2_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='二级分类编号')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class BaseDic(Base):
    __tablename__ = 'base_dic'

    dic_code: Mapped[str] = mapped_column(VARCHAR(10, charset='utf8mb3', collation='utf8mb3_general_ci'), primary_key=True, comment='编号')
    dic_name: Mapped[Optional[str]] = mapped_column(String(100), comment='编码名称')
    parent_code: Mapped[Optional[str]] = mapped_column(String(10), comment='父编号')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建日期')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='修改日期')


class BaseFrontendParam(Base):
    __tablename__ = 'base_frontend_param'
    __table_args__ = {'comment': '前端数据保护表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    code: Mapped[str] = mapped_column(String(100), nullable=False, comment='属性名称')
    delete_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='分类id')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class BaseProvince(Base):
    __tablename__ = 'base_province'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='id')
    name: Mapped[Optional[str]] = mapped_column(String(20), comment='省名称')
    region_id: Mapped[Optional[str]] = mapped_column(String(20), comment='大区id')
    area_code: Mapped[Optional[str]] = mapped_column(String(20), comment='行政区位码')
    iso_code: Mapped[Optional[str]] = mapped_column(String(20), comment='国际编码')
    iso_3166_2: Mapped[Optional[str]] = mapped_column(String(20), comment='ISO3166编码')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


t_base_region = Table(
    'base_region', Base.metadata,
    Column('id', String(20), comment='大区id'),
    Column('region_name', String(20), comment='大区名称'),
    Column('create_time', DateTime),
    Column('operate_time', DateTime)
)


class BaseSaleAttr(Base):
    __tablename__ = 'base_sale_attr'
    __table_args__ = {'comment': '基本销售属性表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    name: Mapped[str] = mapped_column(String(20), nullable=False, comment='销售属性名称')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class BaseTrademark(Base):
    __tablename__ = 'base_trademark'
    __table_args__ = {'comment': '品牌表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    tm_name: Mapped[str] = mapped_column(String(100), nullable=False, comment='属性值')
    logo_url: Mapped[Optional[str]] = mapped_column(String(200), comment='品牌logo的图片路径')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class CartInfo(Base):
    __tablename__ = 'cart_info'
    __table_args__ = (
        Index('idx_uid', 'user_id'),
        {'comment': '购物车表 用户登录系统时更新冗余'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    user_id: Mapped[Optional[str]] = mapped_column(String(200), comment='用户id')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='skuid')
    cart_price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), comment='放入购物车时价格')
    sku_num: Mapped[Optional[int]] = mapped_column(Integer, comment='数量')
    img_url: Mapped[Optional[str]] = mapped_column(String(200), comment='图片文件')
    sku_name: Mapped[Optional[str]] = mapped_column(String(200), comment='sku名称 (冗余)')
    is_checked: Mapped[Optional[int]] = mapped_column(Integer)
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='修改时间')
    is_ordered: Mapped[Optional[int]] = mapped_column(BigInteger, comment='是否已经下单')
    order_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='下单时间')


class CmsBanner(Base):
    __tablename__ = 'cms_banner'
    __table_args__ = {'comment': '首页banner表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='ID')
    image_url: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"), comment='图片地址')
    sort: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False, server_default=text("'0'"), comment='排序')
    title: Mapped[Optional[str]] = mapped_column(String(20), server_default=text("''"), comment='标题')
    link_url: Mapped[Optional[str]] = mapped_column(String(500), server_default=text("''"), comment='链接地址')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class CommentInfo(Base):
    __tablename__ = 'comment_info'
    __table_args__ = {'comment': '商品评论表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='用户id')
    nick_name: Mapped[Optional[str]] = mapped_column(String(20), comment='用户昵称')
    head_img: Mapped[Optional[str]] = mapped_column(String(200))
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='skuid')
    spu_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='商品id')
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='订单编号')
    appraise: Mapped[Optional[str]] = mapped_column(String(10), comment='评价 1 好评 2 中评 3 差评')
    comment_txt: Mapped[Optional[str]] = mapped_column(String(2000), comment='评价内容')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='修改时间')


class CouponInfo(Base):
    __tablename__ = 'coupon_info'
    __table_args__ = {'comment': '优惠券表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='购物券编号')
    limit_num: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("'0'"), comment='最多领用次数')
    taken_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("'0'"), comment='已领用次数')
    coupon_name: Mapped[Optional[str]] = mapped_column(String(100), comment='购物券名称')
    coupon_type: Mapped[Optional[str]] = mapped_column(String(10), comment='购物券类型 1 现金券 2 折扣券 3 满减券 4 满件打折券')
    condition_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), comment='满额数（3）')
    condition_num: Mapped[Optional[int]] = mapped_column(BigInteger, comment='满件数（4）')
    activity_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='活动编号')
    benefit_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='减金额（1 3）')
    benefit_discount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), comment='折扣（2 4）')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    range_type: Mapped[Optional[str]] = mapped_column(String(10), comment='范围类型 1、商品(spuid) 2、品类(三级分类id) 3、品牌')
    start_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='可以领取的开始日期')
    end_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='可以领取的结束日期')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='修改时间')
    expire_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='过期时间')
    range_desc: Mapped[Optional[str]] = mapped_column(String(500), comment='范围描述')


class CouponRange(Base):
    __tablename__ = 'coupon_range'
    __table_args__ = {'comment': '优惠券范围表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='购物券编号')
    coupon_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("'0'"), comment='优惠券id')
    range_type: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("''"), comment='范围类型 1、商品(spuid) 2、品类(三级分类id) 3、品牌')
    range_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("'0'"))
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class CouponUse(Base):
    __tablename__ = 'coupon_use'
    __table_args__ = {'comment': '优惠券领用表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    coupon_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='购物券ID')
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='用户ID')
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='订单ID')
    coupon_status: Mapped[Optional[str]] = mapped_column(String(10), comment='购物券状态（1：未使用 2：已使用）')
    get_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='获取时间')
    using_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='使用时间')
    used_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='支付时间')
    expire_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='过期时间')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class FavorInfo(Base):
    __tablename__ = 'favor_info'
    __table_args__ = {'comment': '商品收藏表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='用户名称')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='skuid')
    spu_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='商品id')
    is_cancel: Mapped[Optional[str]] = mapped_column(String(1), comment='是否已取消 0 正常 1 已取消')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='修改时间')


class FinancialSkuCost(Base):
    __tablename__ = 'financial_sku_cost'

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='sku_id')
    sku_name: Mapped[Optional[str]] = mapped_column(String(20), comment='商品名称')
    busi_date: Mapped[Optional[str]] = mapped_column(String(20), comment='业务日期')
    is_lastest: Mapped[Optional[str]] = mapped_column(String(2), comment='是否最近')
    sku_cost: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='商品结算成本')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')


class OrderDetail(Base):
    __tablename__ = 'order_detail'
    __table_args__ = {'comment': '订单明细表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='订单编号')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='sku_id')
    sku_name: Mapped[Optional[str]] = mapped_column(String(200), comment='sku名称（冗余)')
    img_url: Mapped[Optional[str]] = mapped_column(String(200), comment='图片名称（冗余)')
    order_price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), comment='购买价格(下单时sku价格）')
    sku_num: Mapped[Optional[str]] = mapped_column(String(200), comment='购买个数')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    split_total_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2))
    split_activity_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2))
    split_coupon_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2))
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class OrderDetailActivity(Base):
    __tablename__ = 'order_detail_activity'
    __table_args__ = {'comment': '订单明细购物券表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='订单id')
    order_detail_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='订单明细id')
    activity_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='活动ID')
    activity_rule_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='活动规则')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='skuID')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='获取时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class OrderDetailCoupon(Base):
    __tablename__ = 'order_detail_coupon'
    __table_args__ = {'comment': '订单明细购物券表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='订单id')
    order_detail_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='订单明细id')
    coupon_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='购物券ID')
    coupon_use_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='购物券领用id')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='skuID')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='获取时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class OrderInfo(Base):
    __tablename__ = 'order_info'
    __table_args__ = (
        Index('idx_uid_status', 'order_status', 'user_id'),
        {'comment': '订单表 订单表'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    consignee: Mapped[Optional[str]] = mapped_column(String(100), comment='收货人')
    consignee_tel: Mapped[Optional[str]] = mapped_column(String(20), comment='收件人电话')
    total_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), comment='总金额')
    order_status: Mapped[Optional[str]] = mapped_column(String(20), comment='订单状态')
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='用户id')
    payment_way: Mapped[Optional[str]] = mapped_column(String(20), comment='付款方式')
    delivery_address: Mapped[Optional[str]] = mapped_column(String(1000), comment='送货地址')
    order_comment: Mapped[Optional[str]] = mapped_column(String(200), comment='订单备注')
    out_trade_no: Mapped[Optional[str]] = mapped_column(String(50), comment='订单交易编号（第三方支付用)')
    trade_body: Mapped[Optional[str]] = mapped_column(String(200), comment='订单描述(第三方支付用)')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='操作时间')
    expire_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='失效时间')
    process_status: Mapped[Optional[str]] = mapped_column(String(20), comment='进度状态')
    tracking_no: Mapped[Optional[str]] = mapped_column(String(100), comment='物流单编号')
    parent_order_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='父订单编号')
    img_url: Mapped[Optional[str]] = mapped_column(String(200), comment='图片路径')
    province_id: Mapped[Optional[int]] = mapped_column(Integer, comment='地区')
    activity_reduce_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='促销金额')
    coupon_reduce_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='优惠券')
    original_total_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='原价金额')
    feight_fee: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='运费')
    feight_fee_reduce: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='运费减免')
    refundable_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='可退款日期（签收后30天）')


class OrderRefundInfo(Base):
    __tablename__ = 'order_refund_info'
    __table_args__ = {'comment': '退单表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='用户id')
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='订单id')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='skuid')
    refund_type: Mapped[Optional[str]] = mapped_column(String(20), comment='退款类型')
    refund_num: Mapped[Optional[int]] = mapped_column(BigInteger, comment='退货件数')
    refund_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='退款金额')
    refund_reason_type: Mapped[Optional[str]] = mapped_column(String(200), comment='原因类型')
    refund_reason_txt: Mapped[Optional[str]] = mapped_column(String(20), comment='原因内容')
    refund_status: Mapped[Optional[str]] = mapped_column(String(10), comment='退款状态（0：待审批 1：已退款）')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class OrderStatusLog(Base):
    __tablename__ = 'order_status_log'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    order_status: Mapped[Optional[str]] = mapped_column(String(11))
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class PaymentInfo(Base):
    __tablename__ = 'payment_info'
    __table_args__ = {'comment': '支付信息表'}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment='编号')
    out_trade_no: Mapped[Optional[str]] = mapped_column(String(50), comment='对外业务编号')
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='订单编号')
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    payment_type: Mapped[Optional[str]] = mapped_column(String(20), comment='支付类型（微信 支付宝）')
    trade_no: Mapped[Optional[str]] = mapped_column(String(50), comment='交易编号')
    total_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), comment='支付金额')
    subject: Mapped[Optional[str]] = mapped_column(String(200), comment='交易内容')
    payment_status: Mapped[Optional[str]] = mapped_column(String(20), comment='支付状态')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    callback_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='回调时间')
    callback_content: Mapped[Optional[str]] = mapped_column(Text, comment='回调信息')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class PromotionPos(Base):
    __tablename__ = 'promotion_pos'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pos_location: Mapped[Optional[str]] = mapped_column(VARCHAR(200, charset='utf8mb3', collation='utf8mb3_general_ci'), comment='坑位位置')
    pos_type: Mapped[Optional[str]] = mapped_column(VARCHAR(20, charset='utf8mb3', collation='utf8mb3_general_ci'), comment='坑位类型：banner,宫格,列表, 瀑布')
    promotion_type: Mapped[Optional[str]] = mapped_column(VARCHAR(20, charset='utf8mb3', collation='utf8mb3_general_ci'), comment='营销类型：算法、固定、搜索')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class PromotionRefer(Base):
    __tablename__ = 'promotion_refer'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    refer_name: Mapped[Optional[str]] = mapped_column(VARCHAR(200, charset='utf8mb3', collation='utf8mb3_general_ci'), comment='外链名称')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class RefundPayment(Base):
    __tablename__ = 'refund_payment'
    __table_args__ = (
        Index('idx_order_id', 'order_id'),
        Index('idx_out_trade_no', 'out_trade_no'),
        {'comment': '退款信息表'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment='编号')
    out_trade_no: Mapped[Optional[str]] = mapped_column(String(50), comment='对外业务编号')
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='订单编号')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    payment_type: Mapped[Optional[str]] = mapped_column(String(20), comment='支付类型（微信 支付宝）')
    trade_no: Mapped[Optional[str]] = mapped_column(String(50), comment='交易编号')
    total_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), comment='退款金额')
    subject: Mapped[Optional[str]] = mapped_column(String(200), comment='交易内容')
    refund_status: Mapped[Optional[str]] = mapped_column(String(30), comment='退款状态')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    callback_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='回调时间')
    callback_content: Mapped[Optional[str]] = mapped_column(Text, comment='回调信息')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class SeckillGoods(Base):
    __tablename__ = 'seckill_goods'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    spu_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='spu_id')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='sku_id')
    sku_name: Mapped[Optional[str]] = mapped_column(String(100), comment='标题')
    sku_default_img: Mapped[Optional[str]] = mapped_column(String(150), comment='商品图片')
    price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), comment='原价格')
    cost_price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), comment='秒杀价格')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='添加日期')
    check_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='审核日期')
    status: Mapped[Optional[str]] = mapped_column(String(1), comment='审核状态')
    start_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='开始时间')
    end_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='结束时间')
    num: Mapped[Optional[int]] = mapped_column(Integer, comment='秒杀商品数')
    stock_count: Mapped[Optional[int]] = mapped_column(Integer, comment='剩余库存数')
    sku_desc: Mapped[Optional[str]] = mapped_column(String(2000), comment='描述')


class SkuAttrValue(Base):
    __tablename__ = 'sku_attr_value'
    __table_args__ = {'comment': 'sku平台属性值关联表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    attr_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='属性id（冗余)')
    value_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='属性值id')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='skuid')
    attr_name: Mapped[Optional[str]] = mapped_column(String(30), comment='属性名')
    value_name: Mapped[Optional[str]] = mapped_column(String(30), comment='属性值名称')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class SkuImage(Base):
    __tablename__ = 'sku_image'
    __table_args__ = {'comment': '库存单元图片表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='商品id')
    img_name: Mapped[Optional[str]] = mapped_column(String(200), comment='图片名称（冗余）')
    img_url: Mapped[Optional[str]] = mapped_column(String(300), comment='图片路径(冗余)')
    spu_img_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='商品图片id')
    is_default: Mapped[Optional[str]] = mapped_column(String(4000), comment='是否默认')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class SkuInfo(Base):
    __tablename__ = 'sku_info'
    __table_args__ = {'comment': '库存单元表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='库存id(itemID)')
    is_sale: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("'0'"), comment='是否销售（1：是 0：否）')
    spu_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='商品id')
    price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 0), comment='价格')
    sku_name: Mapped[Optional[str]] = mapped_column(String(200), comment='sku名称')
    sku_desc: Mapped[Optional[str]] = mapped_column(String(2000), comment='商品规格描述')
    weight: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(10, 2), comment='重量')
    tm_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='品牌(冗余)')
    category3_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='三级分类id（冗余)')
    sku_default_img: Mapped[Optional[str]] = mapped_column(String(300), comment='默认显示图片(冗余)')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class SkuSaleAttrValue(Base):
    __tablename__ = 'sku_sale_attr_value'
    __table_args__ = {'comment': 'sku销售属性值'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='id')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='库存单元id')
    spu_id: Mapped[Optional[int]] = mapped_column(Integer, comment='spu_id(冗余)')
    sale_attr_value_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='销售属性值id')
    sale_attr_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    sale_attr_name: Mapped[Optional[str]] = mapped_column(String(30))
    sale_attr_value_name: Mapped[Optional[str]] = mapped_column(String(30))
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class SpuImage(Base):
    __tablename__ = 'spu_image'
    __table_args__ = {'comment': '商品图片表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    spu_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='商品id')
    img_name: Mapped[Optional[str]] = mapped_column(String(200), comment='图片名称')
    img_url: Mapped[Optional[str]] = mapped_column(String(300), comment='图片路径')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class SpuInfo(Base):
    __tablename__ = 'spu_info'
    __table_args__ = {'comment': '商品表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='商品id')
    spu_name: Mapped[Optional[str]] = mapped_column(String(200), comment='商品名称')
    description: Mapped[Optional[str]] = mapped_column(String(1000), comment='商品描述(后台简述）')
    category3_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='三级分类id')
    tm_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='品牌id')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class SpuPoster(Base):
    __tablename__ = 'spu_poster'
    __table_args__ = {'comment': '商品海报表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    create_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='创建时间')
    operate_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='更新时间')
    is_deleted: Mapped[int] = mapped_column(TINYINT, nullable=False, server_default=text("'0'"), comment='逻辑删除 1（true）已删除， 0（false）未删除')
    spu_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='商品id')
    img_name: Mapped[Optional[str]] = mapped_column(String(200), comment='文件名称')
    img_url: Mapped[Optional[str]] = mapped_column(String(200), comment='文件路径')


class SpuSaleAttr(Base):
    __tablename__ = 'spu_sale_attr'
    __table_args__ = {'comment': 'spu销售属性'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号(业务中无关联)')
    spu_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='商品id')
    base_sale_attr_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='销售属性id')
    sale_attr_name: Mapped[Optional[str]] = mapped_column(String(20), comment='销售属性名称(冗余)')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class SpuSaleAttrValue(Base):
    __tablename__ = 'spu_sale_attr_value'
    __table_args__ = {'comment': 'spu销售属性值'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='销售属性值编号')
    spu_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='商品id')
    base_sale_attr_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='销售属性id')
    sale_attr_value_name: Mapped[Optional[str]] = mapped_column(VARCHAR(200, charset='utf8mb3', collation='utf8mb3_general_ci'), comment='销售属性值名称')
    sale_attr_name: Mapped[Optional[str]] = mapped_column(VARCHAR(200, charset='utf8mb3', collation='utf8mb3_general_ci'), comment='销售属性名称(冗余)')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class UserAddress(Base):
    __tablename__ = 'user_address'
    __table_args__ = {'comment': '用户地址表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='用户id')
    province_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='省份id')
    user_address: Mapped[Optional[str]] = mapped_column(String(500), comment='用户地址')
    consignee: Mapped[Optional[str]] = mapped_column(String(40), comment='收件人')
    phone_num: Mapped[Optional[str]] = mapped_column(String(40), comment='联系方式')
    is_default: Mapped[Optional[str]] = mapped_column(String(1), comment='是否是默认')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)


class UserInfo(Base):
    __tablename__ = 'user_info'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    login_name: Mapped[Optional[str]] = mapped_column(String(200), comment='用户名称')
    nick_name: Mapped[Optional[str]] = mapped_column(String(200), comment='用户昵称')
    passwd: Mapped[Optional[str]] = mapped_column(String(200), comment='用户密码')
    name: Mapped[Optional[str]] = mapped_column(String(200), comment='用户姓名')
    phone_num: Mapped[Optional[str]] = mapped_column(String(200), comment='手机号')
    email: Mapped[Optional[str]] = mapped_column(String(200), comment='邮箱')
    head_img: Mapped[Optional[str]] = mapped_column(String(200), comment='头像')
    user_level: Mapped[Optional[str]] = mapped_column(String(200), comment='用户级别')
    birthday: Mapped[Optional[datetime.date]] = mapped_column(Date, comment='用户生日')
    gender: Mapped[Optional[str]] = mapped_column(String(1), comment='性别 M男,F女')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    operate_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='修改时间')
    status: Mapped[Optional[str]] = mapped_column(String(200), comment='状态')


class WareInfo(Base):
    __tablename__ = 'ware_info'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    address: Mapped[Optional[str]] = mapped_column(String(200))
    areacode: Mapped[Optional[str]] = mapped_column(String(20))


class WareOrderTask(Base):
    __tablename__ = 'ware_order_task'
    __table_args__ = {'comment': '库存工作单表 库存工作单表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='订单编号')
    consignee: Mapped[Optional[str]] = mapped_column(String(100), comment='收货人')
    consignee_tel: Mapped[Optional[str]] = mapped_column(String(20), comment='收货人电话')
    delivery_address: Mapped[Optional[str]] = mapped_column(String(1000), comment='送货地址')
    order_comment: Mapped[Optional[str]] = mapped_column(String(200), comment='订单备注')
    payment_way: Mapped[Optional[str]] = mapped_column(String(2), comment='付款方式 1:在线付款 2:货到付款')
    task_status: Mapped[Optional[str]] = mapped_column(String(20), comment='工作单状态')
    order_body: Mapped[Optional[str]] = mapped_column(String(200), comment='订单描述')
    tracking_no: Mapped[Optional[str]] = mapped_column(String(200), comment='物流单号')
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='创建时间')
    ware_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='仓库编号')
    task_comment: Mapped[Optional[str]] = mapped_column(String(500), comment='工作单备注')


class WareOrderTaskDetail(Base):
    __tablename__ = 'ware_order_task_detail'
    __table_args__ = {'comment': '库存工作单明细表 库存工作单明细表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='sku_id')
    sku_name: Mapped[Optional[str]] = mapped_column(String(200), comment='sku名称')
    sku_num: Mapped[Optional[int]] = mapped_column(Integer, comment='购买个数')
    task_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='工作单编号')
    refund_status: Mapped[Optional[str]] = mapped_column(String(20))


class WareSku(Base):
    __tablename__ = 'ware_sku'
    __table_args__ = {'comment': 'sku与仓库关联表'}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment='编号')
    sku_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='skuid')
    warehouse_id: Mapped[Optional[int]] = mapped_column(BigInteger, comment='仓库id')
    stock: Mapped[Optional[int]] = mapped_column(Integer, comment='库存数')
    stock_name: Mapped[Optional[str]] = mapped_column(String(200), comment='存货名称')
    stock_locked: Mapped[Optional[int]] = mapped_column(Integer, comment='锁定库存数')


class ZLog(Base):
    __tablename__ = 'z_log'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    log: Mapped[Optional[str]] = mapped_column(VARCHAR(4000, charset='utf8mb3', collation='utf8mb3_general_ci'))
