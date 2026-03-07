from typing import Optional
import datetime
import decimal

from sqlalchemy import BigInteger, CHAR, DECIMAL, Date, DateTime, Index, Integer, JSON, String, text
from sqlalchemy.dialects.mysql import BIGINT, TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass


class DwdDimBrandInfoDf(Base):
    __tablename__ = 'dwd_dim_brand_info_df'
    __table_args__ = (
        Index('idx_brand_current', 'brand_id', 'is_current'),
        Index('idx_brand_name', 'brand_name'),
        Index('idx_country', 'country_code'),
        Index('idx_first_letter', 'first_letter'),
        Index('uk_brand_start', 'brand_id', 'start_date', unique=True),
        {'comment': '品牌维度拉链表'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    brand_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='品牌ID')
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效开始日期')
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效结束日期')
    brand_name: Mapped[str] = mapped_column(String(128), nullable=False, comment='品牌名称')
    brand_name_en: Mapped[Optional[str]] = mapped_column(String(128), comment='品牌英文名')
    brand_alias: Mapped[Optional[str]] = mapped_column(String(128), comment='品牌别名')
    brand_logo_url: Mapped[Optional[str]] = mapped_column(String(512), comment='品牌Logo地址')
    brand_story: Mapped[Optional[str]] = mapped_column(String(1024), comment='品牌故事')
    country_code: Mapped[Optional[str]] = mapped_column(String(8), comment='国家编码')
    country_name: Mapped[Optional[str]] = mapped_column(String(64), comment='国家名称')
    first_letter: Mapped[Optional[str]] = mapped_column(CHAR(1), comment='首字母')
    status: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='状态:1有效 0失效')
    is_current: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否当前版本:0否 1是')


class DwdDimCategoryInfoDf(Base):
    __tablename__ = 'dwd_dim_category_info_df'
    __table_args__ = (
        Index('idx_category_current', 'category_id', 'is_current'),
        Index('idx_parent_id', 'parent_category_id'),
        Index('idx_root_id', 'root_category_id'),
        Index('uk_category_start', 'category_id', 'start_date', unique=True),
        {'comment': '类目维度拉链表'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    category_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='类目ID')
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效开始日期')
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效结束日期')
    category_name: Mapped[str] = mapped_column(String(128), nullable=False, comment='类目名称')
    category_level: Mapped[str] = mapped_column(String(16), nullable=False, comment='层级:一级/二级/三级')
    parent_category_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='父类目ID')
    parent_category_name: Mapped[Optional[str]] = mapped_column(String(128), comment='父类目名称')
    root_category_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='一级类目ID')
    root_category_name: Mapped[Optional[str]] = mapped_column(String(128), comment='一级类目名称')
    is_leaf: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否叶子节点:0否 1是')
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"), comment='排序')
    category_path: Mapped[Optional[str]] = mapped_column(String(512), comment='类目路径')
    status: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='状态:0禁用 1启用')
    is_current: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否当前版本:0否 1是')


class DwdDimCouponInfoDf(Base):
    __tablename__ = 'dwd_dim_coupon_info_df'
    __table_args__ = (
        Index('idx_coupon_type', 'coupon_type'),
        Index('idx_use_time', 'use_start_time', 'use_end_time'),
        Index('uk_coupon_etl', 'coupon_id', 'etl_date', unique=True),
        {'comment': '优惠券维度明细快照'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    coupon_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='优惠券ID')
    coupon_name: Mapped[str] = mapped_column(String(256), nullable=False, comment='优惠券名称')
    coupon_type: Mapped[str] = mapped_column(String(32), nullable=False, comment='类型:满减券/折扣券/运费券/品类券')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期')
    coupon_scope_type: Mapped[Optional[str]] = mapped_column(String(32), comment='适用范围:全平台/店铺/SPU/SKU/类目')
    coupon_scope_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='适用范围ID')
    threshold_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='使用门槛金额')
    discount_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='减免金额')
    discount_rate: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(8, 4), comment='折扣率')
    max_discount_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='封顶优惠金额')
    issue_start_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='发券开始时间')
    issue_end_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='发券结束时间')
    use_start_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='可用开始时间')
    use_end_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='可用结束时间')
    total_issue_cnt: Mapped[Optional[int]] = mapped_column(BigInteger, comment='总发行量')


class DwdDimGeoRegionDf(Base):
    __tablename__ = 'dwd_dim_geo_region_df'
    __table_args__ = (
        Index('idx_parent_region', 'parent_region_code'),
        Index('idx_province_city', 'province_code', 'city_code'),
        Index('idx_region_current', 'region_code', 'is_current'),
        Index('uk_region_start', 'region_code', 'start_date', unique=True),
        {'comment': '行政区域维度拉链表'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    region_code: Mapped[str] = mapped_column(String(20), nullable=False, comment='区域编码')
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效开始日期')
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效结束日期')
    region_name: Mapped[str] = mapped_column(String(128), nullable=False, comment='区域名称')
    region_level: Mapped[int] = mapped_column(TINYINT, nullable=False, comment='级别:1省 2市 3区县 4街道')
    parent_region_code: Mapped[Optional[str]] = mapped_column(String(20), comment='父级区域编码')
    parent_region_name: Mapped[Optional[str]] = mapped_column(String(128), comment='父级区域名称')
    province_code: Mapped[Optional[str]] = mapped_column(String(20), comment='省编码')
    province_name: Mapped[Optional[str]] = mapped_column(String(128), comment='省名称')
    city_code: Mapped[Optional[str]] = mapped_column(String(20), comment='市编码')
    city_name: Mapped[Optional[str]] = mapped_column(String(128), comment='市名称')
    district_code: Mapped[Optional[str]] = mapped_column(String(20), comment='区县编码')
    district_name: Mapped[Optional[str]] = mapped_column(String(128), comment='区县名称')
    zip_code: Mapped[Optional[str]] = mapped_column(String(16), comment='邮编')
    status: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='状态:1有效 0失效')
    is_current: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否当前版本:0否 1是')


class DwdDimLogisticsCompanyDf(Base):
    __tablename__ = 'dwd_dim_logistics_company_df'
    __table_args__ = (
        Index('idx_company_code', 'logistics_company_code'),
        Index('idx_logistics_current', 'logistics_company_id', 'is_current'),
        Index('uk_logistics_start', 'logistics_company_id', 'start_date', unique=True),
        {'comment': '物流公司维度拉链表'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    logistics_company_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='物流公司ID')
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效开始日期')
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效结束日期')
    logistics_company_code: Mapped[str] = mapped_column(String(32), nullable=False, comment='物流公司编码')
    logistics_company_name: Mapped[str] = mapped_column(String(128), nullable=False, comment='物流公司名称')
    logistics_type: Mapped[Optional[str]] = mapped_column(String(32), comment='物流类型:快递/同城/冷链/国际')
    service_phone: Mapped[Optional[str]] = mapped_column(String(32), comment='客服电话')
    is_trace_supported: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否支持轨迹查询')
    status: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='状态:1有效 0失效')
    is_current: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否当前版本:0否 1是')


class DwdDimPaymentTypeDf(Base):
    __tablename__ = 'dwd_dim_payment_type_df'
    __table_args__ = (
        Index('idx_payment_type_current', 'payment_type_code', 'is_current'),
        Index('uk_payment_type_start', 'payment_type_code', 'start_date', unique=True),
        {'comment': '支付方式维度拉链表'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    payment_type_code: Mapped[str] = mapped_column(String(32), nullable=False, comment='支付方式编码')
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效开始日期')
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效结束日期')
    payment_type_name: Mapped[str] = mapped_column(String(64), nullable=False, comment='支付方式名称')
    channel_code: Mapped[Optional[str]] = mapped_column(String(32), comment='支付渠道编码')
    channel_name: Mapped[Optional[str]] = mapped_column(String(64), comment='支付渠道名称')
    is_online: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否线上支付')
    is_installment: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否分期支付')
    fee_rate: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(8, 6), comment='支付手续费率')
    status: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='状态:1有效 0失效')
    is_current: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否当前版本:0否 1是')


class DwdDimPromotionInfoDf(Base):
    __tablename__ = 'dwd_dim_promotion_info_df'
    __table_args__ = (
        Index('idx_promotion_type', 'promotion_type'),
        Index('idx_time_range', 'start_time', 'end_time'),
        Index('uk_promotion_etl', 'promotion_id', 'etl_date', unique=True),
        {'comment': '促销活动维度明细快照'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    promotion_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='活动ID')
    promotion_name: Mapped[str] = mapped_column(String(256), nullable=False, comment='活动名称')
    promotion_type: Mapped[str] = mapped_column(String(32), nullable=False, comment='活动类型:满减/折扣/秒杀/拼团')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期')
    promotion_scene: Mapped[Optional[str]] = mapped_column(String(32), comment='活动场景:商品/店铺/平台')
    promotion_level: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='活动优先级(数值越小优先级越高)')
    start_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='生效开始时间')
    end_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='生效结束时间')
    rule_desc: Mapped[Optional[str]] = mapped_column(String(1024), comment='规则描述')
    threshold_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='门槛金额')
    discount_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='减免金额')
    discount_rate: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(8, 4), comment='折扣率')
    max_discount_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='封顶减免')
    sponsor_type: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='发起方:1平台 2店铺 3品牌')
    sponsor_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='发起方ID')


class DwdDimShopInfoDf(Base):
    __tablename__ = 'dwd_dim_shop_info_df'
    __table_args__ = (
        Index('idx_seller_id', 'seller_id'),
        Index('idx_shop_current', 'shop_id', 'is_current'),
        Index('idx_shop_type', 'shop_type'),
        Index('uk_shop_start', 'shop_id', 'start_date', unique=True),
        {'comment': '店铺维度拉链表'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    shop_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='店铺ID')
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效开始日期')
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效结束日期')
    shop_name: Mapped[str] = mapped_column(String(128), nullable=False, comment='店铺名称')
    shop_type: Mapped[Optional[str]] = mapped_column(String(16), server_default=text("'普通店'"), comment='店铺类型:自营/旗舰店/专卖店/普通店')
    seller_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='商家ID')
    seller_name: Mapped[Optional[str]] = mapped_column(String(128), comment='商家名称')
    industry_type: Mapped[Optional[str]] = mapped_column(String(64), comment='行业类型')
    service_score: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(4, 2), comment='服务评分')
    logistics_score: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(4, 2), comment='物流评分')
    description_score: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(4, 2), comment='描述评分')
    open_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='开店时间')
    province_code: Mapped[Optional[str]] = mapped_column(String(20), comment='店铺省编码')
    city_code: Mapped[Optional[str]] = mapped_column(String(20), comment='店铺市编码')
    district_code: Mapped[Optional[str]] = mapped_column(String(20), comment='店铺区编码')
    is_self_operated: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否自营:0否 1是')
    is_global: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否跨境:0否 1是')
    is_deleted: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='逻辑删除:0否 1是')
    shop_status: Mapped[Optional[str]] = mapped_column(String(16), server_default=text("'营业'"), comment='状态:关店/营业')
    is_current: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否当前版本:0否 1是')


class DwdDimSkuInfoDf(Base):
    __tablename__ = 'dwd_dim_sku_info_df'
    __table_args__ = (
        Index('idx_brand_id', 'brand_id'),
        Index('idx_category_id', 'category_id'),
        Index('idx_shop_id', 'shop_id'),
        Index('idx_sku_current', 'sku_id', 'is_current'),
        Index('idx_spu_id', 'spu_id'),
        Index('uk_sku_start', 'sku_id', 'start_date', unique=True),
        {'comment': 'SKU维度拉链表'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    sku_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='SKU ID')
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效开始日期')
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效结束日期')
    sku_name: Mapped[str] = mapped_column(String(256), nullable=False, comment='SKU名称')
    spu_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='SPU ID')
    shop_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='店铺ID')
    category_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='三级类目ID')
    brand_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='品牌ID')
    bar_code: Mapped[Optional[str]] = mapped_column(String(64), comment='条码')
    sku_specs_json: Mapped[Optional[dict]] = mapped_column(JSON, comment='SKU规格JSON')
    unit: Mapped[Optional[str]] = mapped_column(String(16), server_default=text("'件'"), comment='单位')
    origin_price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='原价')
    sale_price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='销售价')
    cost_price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='成本价')
    warning_stock: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"), comment='预警库存')
    is_hot_sale: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否热销')
    is_new: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否新品')
    is_deleted: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='逻辑删除')
    is_current: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否当前版本:0否 1是')


class DwdDimSpuInfoDf(Base):
    __tablename__ = 'dwd_dim_spu_info_df'
    __table_args__ = (
        Index('idx_brand_id', 'brand_id'),
        Index('idx_category_id', 'category_id'),
        Index('idx_shop_id', 'shop_id'),
        Index('idx_spu_current', 'spu_id', 'is_current'),
        Index('uk_spu_start', 'spu_id', 'start_date', unique=True),
        {'comment': 'SPU维度拉链表'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    spu_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='SPU ID')
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效开始日期')
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效结束日期')
    spu_name: Mapped[str] = mapped_column(String(256), nullable=False, comment='SPU名称')
    category_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='三级类目ID')
    shop_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='店铺ID')
    spu_sub_title: Mapped[Optional[str]] = mapped_column(String(512), comment='副标题')
    brand_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='品牌ID')
    brand_name: Mapped[Optional[str]] = mapped_column(String(128), comment='品牌名称')
    is_virtual: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否虚拟商品')
    is_presale: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否预售')
    presale_start_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='预售开始时间')
    presale_end_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='预售结束时间')
    weight: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(12, 3), server_default=text("'0.000'"), comment='重量kg')
    volume: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(12, 3), server_default=text("'0.000'"), comment='体积m3')
    on_shelf_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='上架时间')
    is_current: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否当前版本:0否 1是')


class DwdDimUserInfoDf(Base):
    __tablename__ = 'dwd_dim_user_info_df'
    __table_args__ = (
        Index('idx_province_city', 'province_code', 'city_code'),
        Index('idx_register_time', 'register_time'),
        Index('idx_user_current', 'user_id', 'is_current'),
        Index('uk_user_start', 'user_id', 'start_date', unique=True),
        {'comment': '用户维度拉链表'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, comment='自增主键')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='用户业务ID')
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效开始日期')
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='生效结束日期')
    user_name: Mapped[Optional[str]] = mapped_column(String(64), comment='用户名')
    nick_name: Mapped[Optional[str]] = mapped_column(String(64), comment='昵称')
    gender: Mapped[Optional[str]] = mapped_column(String(8), server_default=text("'未知'"), comment='性别:未知/男/女')
    birthday: Mapped[Optional[datetime.date]] = mapped_column(Date, comment='生日')
    phone: Mapped[Optional[str]] = mapped_column(String(20), comment='手机号(脱敏)')
    email: Mapped[Optional[str]] = mapped_column(String(128), comment='邮箱(脱敏)')
    register_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='注册时间')
    register_channel_code: Mapped[Optional[str]] = mapped_column(String(32), comment='注册渠道编码')
    register_source: Mapped[Optional[str]] = mapped_column(String(32), comment='注册来源(APP/H5/PC等)')
    user_level: Mapped[Optional[str]] = mapped_column(String(16), server_default=text("'1'"), comment='会员等级')
    user_tag: Mapped[Optional[str]] = mapped_column(String(128), comment='用户标签')
    is_vip: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否VIP:0否 1是')
    province_code: Mapped[Optional[str]] = mapped_column(String(20), comment='省编码')
    city_code: Mapped[Optional[str]] = mapped_column(String(20), comment='市编码')
    district_code: Mapped[Optional[str]] = mapped_column(String(20), comment='区编码')
    occupation: Mapped[Optional[str]] = mapped_column(String(64), comment='职业')
    income_level: Mapped[Optional[str]] = mapped_column(String(32), comment='收入等级')
    education_level: Mapped[Optional[str]] = mapped_column(String(32), comment='学历等级')
    marital_status: Mapped[Optional[str]] = mapped_column(String(16), comment='婚姻状态')
    user_status: Mapped[Optional[str]] = mapped_column(String(16), server_default=text("'正常'"), comment='状态:正常/禁用/注销')
    is_current: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否当前版本:0否 1是')


class DwdFactInteractionCartAddDi(Base):
    __tablename__ = 'dwd_fact_interaction_cart_add_di'
    __table_args__ = (
        Index('idx_etl_date', 'etl_date'),
        Index('idx_event_time', 'event_time'),
        Index('idx_sku_id', 'sku_id'),
        Index('idx_user_id', 'user_id'),
        Index('uk_cart_add', 'cart_add_id', unique=True),
        {'comment': '互动域-加购事实明细'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    cart_add_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='加购明细ID(业务主键)')
    sku_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='SKU ID')
    add_sku_num: Mapped[int] = mapped_column(Integer, nullable=False, comment='加购件数')
    event_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='加购时间')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期')
    event_no: Mapped[Optional[str]] = mapped_column(String(64), comment='事件流水号')
    user_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='用户ID(游客为空)')
    device_id: Mapped[Optional[str]] = mapped_column(String(128), comment='设备ID')
    session_id: Mapped[Optional[str]] = mapped_column(String(128), comment='会话ID')
    shop_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='店铺ID')
    spu_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='SPU ID')
    category_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='类目ID')
    cart_source: Mapped[Optional[str]] = mapped_column(String(32), comment='加购来源:商品详情/搜索/推荐/活动页')
    client_type: Mapped[Optional[str]] = mapped_column(String(32), comment='客户端类型:iOS/Android/H5/PC/小程序')
    channel_code: Mapped[Optional[str]] = mapped_column(String(32), comment='渠道编码')
    sku_price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='加购时单价')


class DwdFactInteractionFavorAddDi(Base):
    __tablename__ = 'dwd_fact_interaction_favor_add_di'
    __table_args__ = (
        Index('idx_etl_date', 'etl_date'),
        Index('idx_event_time', 'event_time'),
        Index('idx_spu_id', 'spu_id'),
        Index('idx_user_id', 'user_id'),
        Index('uk_favor_add', 'favor_add_id', unique=True),
        {'comment': '互动域-收藏事实明细'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    favor_add_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='收藏明细ID(业务主键)')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='用户ID')
    favor_type: Mapped[str] = mapped_column(String(32), nullable=False, comment='收藏类型:商品/店铺')
    event_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='收藏时间')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期')
    event_no: Mapped[Optional[str]] = mapped_column(String(64), comment='事件流水号')
    shop_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='店铺ID')
    sku_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='SKU ID')
    spu_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='SPU ID')
    client_type: Mapped[Optional[str]] = mapped_column(String(32), comment='客户端类型')
    channel_code: Mapped[Optional[str]] = mapped_column(String(32), comment='渠道编码')


class DwdFactInventoryChangeDi(Base):
    __tablename__ = 'dwd_fact_inventory_change_di'
    __table_args__ = (
        Index('idx_biz', 'biz_type', 'biz_id'),
        Index('idx_change_time', 'change_time'),
        Index('idx_etl_date', 'etl_date'),
        Index('idx_sku_id', 'sku_id'),
        Index('idx_warehouse_id', 'warehouse_id'),
        Index('uk_inventory_change', 'inventory_change_id', unique=True),
        {'comment': '库存域-库存变更事实明细'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    inventory_change_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='库存变更明细ID(业务主键)')
    sku_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='SKU ID')
    change_type: Mapped[str] = mapped_column(String(32), nullable=False, comment='变更类型:入库/出库/锁定/解锁/盘点')
    before_stock_qty: Mapped[int] = mapped_column(Integer, nullable=False, comment='变更前库存')
    change_qty: Mapped[int] = mapped_column(Integer, nullable=False, comment='变更数量(可正可负)')
    after_stock_qty: Mapped[int] = mapped_column(Integer, nullable=False, comment='变更后库存')
    change_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='变更时间')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期')
    change_no: Mapped[Optional[str]] = mapped_column(String(64), comment='库存变更流水号')
    spu_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='SPU ID')
    shop_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='店铺ID')
    warehouse_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='仓库ID')
    biz_type: Mapped[Optional[str]] = mapped_column(String(32), comment='业务类型:下单/取消/发货/退款/调拨')
    biz_id: Mapped[Optional[str]] = mapped_column(String(64), comment='业务单据ID')
    before_lock_qty: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"), comment='变更前锁定库存')
    change_lock_qty: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"), comment='锁定库存变更量')
    after_lock_qty: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'0'"), comment='变更后锁定库存')
    unit_cost: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 4), comment='单位成本')
    total_cost_change: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 4), comment='总成本变动')
    operator_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='操作人ID')
    operator_type: Mapped[Optional[str]] = mapped_column(String(32), comment='操作人类型:系统/用户/商家/仓管')
    remark: Mapped[Optional[str]] = mapped_column(String(512), comment='备注')


class DwdFactServiceCommentDetailDi(Base):
    __tablename__ = 'dwd_fact_service_comment_detail_di'
    __table_args__ = (
        Index('idx_comment_id', 'comment_id'),
        Index('idx_comment_time', 'comment_time'),
        Index('idx_etl_date', 'etl_date'),
        Index('idx_order_detail_id', 'order_detail_id'),
        Index('idx_sku_id', 'sku_id'),
        Index('uk_comment_detail', 'comment_detail_id', unique=True),
        {'comment': '服务域-评价事实明细'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    comment_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='评价明细ID(业务主键)')
    comment_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='评价ID')
    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单ID')
    order_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单明细ID')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='用户ID')
    shop_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='店铺ID')
    sku_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='SKU ID')
    spu_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='SPU ID')
    comment_level: Mapped[int] = mapped_column(TINYINT, nullable=False, comment='评分(1-5)')
    comment_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='评价时间')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期')
    category_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='类目ID')
    is_anonymous: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否匿名')
    is_with_image: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否晒图')
    is_with_video: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否晒视频')
    is_append_comment: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否追评')
    comment_content: Mapped[Optional[str]] = mapped_column(String(2000), comment='评价内容(可脱敏)')
    service_score: Mapped[Optional[int]] = mapped_column(TINYINT, comment='服务评分')
    logistics_score: Mapped[Optional[int]] = mapped_column(TINYINT, comment='物流评分')
    description_score: Mapped[Optional[int]] = mapped_column(TINYINT, comment='描述评分')
    sensitive_tag: Mapped[Optional[str]] = mapped_column(String(128), comment='敏感标签')
    sentiment: Mapped[Optional[str]] = mapped_column(String(16), comment='情感分析结果:正向/中性/负向')


class DwdFactTradeDeliveryDetailDi(Base):
    __tablename__ = 'dwd_fact_trade_delivery_detail_di'
    __table_args__ = (
        Index('idx_delivery_no', 'delivery_no'),
        Index('idx_delivery_time', 'delivery_time'),
        Index('idx_etl_date', 'etl_date'),
        Index('idx_order_detail_id', 'order_detail_id'),
        Index('idx_order_id', 'order_id'),
        Index('idx_tracking_no', 'tracking_no'),
        Index('uk_delivery_detail', 'delivery_detail_id', unique=True),
        {'comment': '交易域-履约发货事实明细'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    delivery_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='发货明细ID(业务主键)')
    delivery_no: Mapped[str] = mapped_column(String(64), nullable=False, comment='发货单号')
    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单ID')
    order_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单明细ID')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='用户ID')
    shop_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='店铺ID')
    delivery_status: Mapped[str] = mapped_column(String(32), nullable=False, comment='发货状态:待发货/已发货/运输中/已签收/拒收')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期(按发货日期)')
    warehouse_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='仓库ID')
    logistics_company_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='物流公司ID')
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128), comment='运单号')
    delivery_type: Mapped[Optional[str]] = mapped_column(String(32), comment='配送类型:快递/同城/门店自提')
    receiver_name: Mapped[Optional[str]] = mapped_column(String(64), comment='收件人(脱敏)')
    receiver_phone: Mapped[Optional[str]] = mapped_column(String(20), comment='收件电话(脱敏)')
    receiver_province_code: Mapped[Optional[str]] = mapped_column(String(20), comment='收货省编码')
    receiver_city_code: Mapped[Optional[str]] = mapped_column(String(20), comment='收货市编码')
    receiver_district_code: Mapped[Optional[str]] = mapped_column(String(20), comment='收货区编码')
    receiver_address: Mapped[Optional[str]] = mapped_column(String(512), comment='收货地址(脱敏)')
    package_cnt: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("'1'"), comment='包裹数量')
    total_weight: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 3), server_default=text("'0.000'"), comment='总重量kg')
    freight_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='运费金额')
    outbound_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='出库时间')
    delivery_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='发货时间')
    sign_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='签收时间')


class DwdFactTradeOrderDetailActivityDi(Base):
    __tablename__ = 'dwd_fact_trade_order_detail_activity_di'
    __table_args__ = (
        Index('idx_etl_date', 'etl_date'),
        Index('idx_order_detail_id', 'order_detail_id'),
        Index('idx_promotion_id', 'promotion_id'),
        Index('uk_detail_activity', 'order_detail_activity_id', unique=True),
        {'comment': '交易域-订单明细活动分摊事实'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    order_detail_activity_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单明细活动摊销ID')
    order_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单明细ID')
    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单ID')
    promotion_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='活动ID')
    promotion_discount_amount: Mapped[decimal.Decimal] = mapped_column(DECIMAL(16, 2), nullable=False, comment='活动分摊金额')
    order_create_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='下单时间')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期')
    promotion_type: Mapped[Optional[str]] = mapped_column(String(32), comment='活动类型')
    promotion_level: Mapped[Optional[int]] = mapped_column(TINYINT, comment='活动层级优先级')
    rule_snapshot: Mapped[Optional[str]] = mapped_column(String(1024), comment='规则快照')


class DwdFactTradeOrderDetailCouponDi(Base):
    __tablename__ = 'dwd_fact_trade_order_detail_coupon_di'
    __table_args__ = (
        Index('idx_coupon_id', 'coupon_id'),
        Index('idx_etl_date', 'etl_date'),
        Index('idx_order_detail_id', 'order_detail_id'),
        Index('uk_detail_coupon', 'order_detail_coupon_id', unique=True),
        {'comment': '交易域-订单明细优惠券分摊事实'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    order_detail_coupon_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单明细优惠券摊销ID')
    order_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单明细ID')
    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单ID')
    coupon_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='优惠券ID')
    coupon_discount_amount: Mapped[decimal.Decimal] = mapped_column(DECIMAL(16, 2), nullable=False, comment='优惠券分摊金额')
    order_create_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='下单时间')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期')
    coupon_user_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='领券用户ID')
    coupon_type: Mapped[Optional[str]] = mapped_column(String(32), comment='券类型')
    coupon_scope_type: Mapped[Optional[str]] = mapped_column(String(32), comment='券适用范围')
    coupon_batch_no: Mapped[Optional[str]] = mapped_column(String(64), comment='券批次号')
    coupon_receive_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='领券时间')
    coupon_use_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='用券时间')


class DwdFactTradeOrderDetailDi(Base):
    __tablename__ = 'dwd_fact_trade_order_detail_di'
    __table_args__ = (
        Index('idx_etl_date', 'etl_date'),
        Index('idx_order_create_time', 'order_create_time'),
        Index('idx_order_id', 'order_id'),
        Index('idx_shop_id', 'shop_id'),
        Index('idx_sku_id', 'sku_id'),
        Index('idx_user_id', 'user_id'),
        Index('uk_order_detail', 'order_detail_id', unique=True),
        {'comment': '交易域-下单事实明细'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    order_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单明细ID(业务主键)')
    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单ID')
    order_status: Mapped[str] = mapped_column(String(32), nullable=False, comment='订单状态')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='用户ID')
    shop_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='店铺ID')
    sku_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='SKU ID')
    spu_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='SPU ID')
    category_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='三级类目ID')
    sku_num: Mapped[int] = mapped_column(Integer, nullable=False, comment='购买件数')
    order_detail_amount: Mapped[decimal.Decimal] = mapped_column(DECIMAL(16, 2), nullable=False, comment='明细总金额(不含优惠)')
    payable_amount: Mapped[decimal.Decimal] = mapped_column(DECIMAL(16, 2), nullable=False, comment='应付金额')
    order_create_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='下单时间')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期(按下单日期)')
    parent_order_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='父订单ID(拆单场景)')
    trade_no: Mapped[Optional[str]] = mapped_column(String(64), comment='交易流水号')
    order_no: Mapped[Optional[str]] = mapped_column(String(64), comment='订单编号')
    order_source: Mapped[Optional[str]] = mapped_column(String(32), comment='下单来源:APP/H5/PC/小程序')
    order_scene: Mapped[Optional[str]] = mapped_column(String(32), comment='订单场景:普通/秒杀/拼团/预售')
    seller_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='商家ID')
    brand_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='品牌ID')
    province_code: Mapped[Optional[str]] = mapped_column(String(20), comment='收货省编码')
    city_code: Mapped[Optional[str]] = mapped_column(String(20), comment='收货市编码')
    district_code: Mapped[Optional[str]] = mapped_column(String(20), comment='收货区编码')
    is_first_order: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否首单')
    is_cross_border: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否跨境')
    is_pre_sale: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否预售')
    is_gift: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否赠品')
    is_risk_order: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否风控单')
    is_order_finish: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='订单是否完结:0否 1是')
    sku_origin_price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='SKU原价')
    sku_sale_price: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), comment='SKU成交单价')
    activity_discount_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='活动优惠金额')
    coupon_discount_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='优惠券优惠金额')
    points_discount_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='积分抵扣金额')
    freight_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='运费')
    tax_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='税费')
    paid_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='实付金额')
    cost_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='成本金额')
    order_pay_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='支付时间')
    order_delivery_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='发货时间')
    order_receive_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='收货时间')
    order_cancel_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='取消时间')
    cancel_stage: Mapped[Optional[str]] = mapped_column(String(32), comment='取消阶段:未支付取消/支付后取消/拒收')


class DwdFactTradePayDetailDi(Base):
    __tablename__ = 'dwd_fact_trade_pay_detail_di'
    __table_args__ = (
        Index('idx_etl_date', 'etl_date'),
        Index('idx_order_id', 'order_id'),
        Index('idx_pay_order_no', 'pay_order_no'),
        Index('idx_pay_success_time', 'pay_success_time'),
        Index('idx_shop_id', 'shop_id'),
        Index('idx_user_id', 'user_id'),
        Index('uk_pay_detail', 'pay_detail_id', unique=True),
        {'comment': '交易域-支付事实明细'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    pay_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='支付明细ID(业务主键)')
    pay_order_no: Mapped[str] = mapped_column(String(64), nullable=False, comment='支付单号')
    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单ID')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='用户ID')
    shop_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='店铺ID')
    payment_type_code: Mapped[str] = mapped_column(String(32), nullable=False, comment='支付方式编码')
    pay_status: Mapped[str] = mapped_column(String(32), nullable=False, comment='支付状态:成功/失败/关闭')
    total_pay_amount: Mapped[decimal.Decimal] = mapped_column(DECIMAL(16, 2), nullable=False, comment='支付总金额')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期(按支付日期)')
    third_party_pay_no: Mapped[Optional[str]] = mapped_column(String(128), comment='第三方支付流水号')
    seller_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='商家ID')
    payment_channel_code: Mapped[Optional[str]] = mapped_column(String(32), comment='支付渠道编码')
    pay_scene: Mapped[Optional[str]] = mapped_column(String(32), comment='支付场景:收银台/自动扣款/合并支付')
    currency_code: Mapped[Optional[str]] = mapped_column(String(8), server_default=text("'CNY'"), comment='币种')
    cash_pay_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='现金支付金额')
    coupon_pay_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='券抵扣金额')
    points_pay_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='积分抵扣金额')
    balance_pay_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='余额支付金额')
    installment_cnt: Mapped[Optional[int]] = mapped_column(Integer, comment='分期期数')
    installment_fee_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='分期手续费')
    pay_success_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='支付成功时间')
    pay_fail_reason: Mapped[Optional[str]] = mapped_column(String(512), comment='支付失败原因')


class DwdFactTradeRefundDetailDi(Base):
    __tablename__ = 'dwd_fact_trade_refund_detail_di'
    __table_args__ = (
        Index('idx_apply_time', 'apply_time'),
        Index('idx_etl_date', 'etl_date'),
        Index('idx_order_detail_id', 'order_detail_id'),
        Index('idx_refund_no', 'refund_no'),
        Index('idx_user_id', 'user_id'),
        Index('uk_refund_detail', 'refund_detail_id', unique=True),
        {'comment': '交易域-退款事实明细'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    refund_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='退款明细ID(业务主键)')
    refund_no: Mapped[str] = mapped_column(String(64), nullable=False, comment='退款单号')
    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单ID')
    order_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单明细ID')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='用户ID')
    shop_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='店铺ID')
    sku_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='SKU ID')
    refund_type: Mapped[str] = mapped_column(String(32), nullable=False, comment='退款类型:仅退款/退货退款')
    refund_status: Mapped[str] = mapped_column(String(32), nullable=False, comment='退款状态')
    refund_apply_amount: Mapped[decimal.Decimal] = mapped_column(DECIMAL(16, 2), nullable=False, comment='申请退款金额')
    apply_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='申请时间')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期(按申请日期)')
    refund_reason_code: Mapped[Optional[str]] = mapped_column(String(32), comment='退款原因编码')
    refund_reason_desc: Mapped[Optional[str]] = mapped_column(String(256), comment='退款原因描述')
    refund_approve_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='审核通过退款金额')
    refund_success_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='退款成功金额')
    refund_freight_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='退运费金额')
    refund_tax_amount: Mapped[Optional[decimal.Decimal]] = mapped_column(DECIMAL(16, 2), server_default=text("'0.00'"), comment='退税金额')
    is_quality_issue: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否质量问题')
    need_return_goods: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否需要退货')
    return_tracking_no: Mapped[Optional[str]] = mapped_column(String(128), comment='退货运单号')
    audit_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='审核时间')
    receive_return_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='商家收货时间')
    refund_success_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='退款成功时间')
    close_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='退款关闭时间')


class DwdFactTradeRefundPayDetailDi(Base):
    __tablename__ = 'dwd_fact_trade_refund_pay_detail_di'
    __table_args__ = (
        Index('idx_etl_date', 'etl_date'),
        Index('idx_refund_detail_id', 'refund_detail_id'),
        Index('idx_refund_no', 'refund_no'),
        Index('idx_refund_pay_time', 'refund_pay_time'),
        Index('uk_refund_pay_detail', 'refund_pay_detail_id', unique=True),
        {'comment': '交易域-退款打款事实明细'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    refund_pay_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='退款支付明细ID(业务主键)')
    refund_no: Mapped[str] = mapped_column(String(64), nullable=False, comment='退款单号')
    refund_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='退款明细ID')
    order_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单ID')
    order_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='订单明细ID')
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='用户ID')
    payment_type_code: Mapped[str] = mapped_column(String(32), nullable=False, comment='原支付方式编码')
    refund_status: Mapped[str] = mapped_column(String(32), nullable=False, comment='退款打款状态')
    refund_amount: Mapped[decimal.Decimal] = mapped_column(DECIMAL(16, 2), nullable=False, comment='退款金额')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期(按退款到账日期)')
    pay_detail_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='原支付明细ID')
    refund_channel_code: Mapped[Optional[str]] = mapped_column(String(32), comment='退款渠道编码')
    refund_account_type: Mapped[Optional[str]] = mapped_column(String(32), comment='退款账户类型')
    refund_apply_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='退款申请时间')
    refund_pay_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment='退款到账时间')
    refund_fail_reason: Mapped[Optional[str]] = mapped_column(String(512), comment='退款失败原因')


class DwdFactTrafficPageViewDi(Base):
    __tablename__ = 'dwd_fact_traffic_page_view_di'
    __table_args__ = (
        Index('idx_business', 'business_type', 'business_id'),
        Index('idx_etl_date', 'etl_date'),
        Index('idx_event_time', 'event_time'),
        Index('idx_page_id', 'page_id'),
        Index('idx_user_id', 'user_id'),
        Index('uk_page_view', 'page_view_id', unique=True),
        {'comment': '流量域-页面访问事实明细'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    page_view_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='页面访问明细ID(业务主键)')
    page_id: Mapped[str] = mapped_column(String(64), nullable=False, comment='页面ID')
    event_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='访问时间')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期')
    event_no: Mapped[Optional[str]] = mapped_column(String(64), comment='事件流水号')
    user_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='用户ID(游客为空)')
    device_id: Mapped[Optional[str]] = mapped_column(String(128), comment='设备ID')
    session_id: Mapped[Optional[str]] = mapped_column(String(128), comment='会话ID')
    page_name: Mapped[Optional[str]] = mapped_column(String(128), comment='页面名称')
    last_page_id: Mapped[Optional[str]] = mapped_column(String(64), comment='上一个页面ID')
    page_type: Mapped[Optional[str]] = mapped_column(String(32), comment='页面类型:首页/详情/活动/搜索/下单')
    business_id: Mapped[Optional[str]] = mapped_column(String(64), comment='业务实体ID(如 sku_id/spu_id/activity_id)')
    business_type: Mapped[Optional[str]] = mapped_column(String(32), comment='业务实体类型')
    channel_code: Mapped[Optional[str]] = mapped_column(String(32), comment='渠道编码')
    client_type: Mapped[Optional[str]] = mapped_column(String(32), comment='客户端类型')
    app_version: Mapped[Optional[str]] = mapped_column(String(32), comment='APP版本')
    os_type: Mapped[Optional[str]] = mapped_column(String(32), comment='操作系统')
    ip: Mapped[Optional[str]] = mapped_column(String(64), comment='访问IP(脱敏)')
    province_code: Mapped[Optional[str]] = mapped_column(String(20), comment='访问省编码')
    city_code: Mapped[Optional[str]] = mapped_column(String(20), comment='访问市编码')
    stay_duration_sec: Mapped[Optional[int]] = mapped_column(Integer, comment='停留秒数')
    is_bounce: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否跳出')


class DwdFactTrafficSearchDi(Base):
    __tablename__ = 'dwd_fact_traffic_search_di'
    __table_args__ = (
        Index('idx_click_sku_id', 'click_sku_id'),
        Index('idx_etl_date', 'etl_date'),
        Index('idx_event_time', 'event_time'),
        Index('idx_keyword', 'search_keyword'),
        Index('idx_user_id', 'user_id'),
        Index('uk_search_detail', 'search_detail_id', unique=True),
        {'comment': '流量域-搜索事实明细'}
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    search_detail_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), nullable=False, comment='搜索明细ID(业务主键)')
    search_keyword: Mapped[str] = mapped_column(String(256), nullable=False, comment='搜索词')
    event_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, comment='搜索时间')
    etl_date: Mapped[datetime.date] = mapped_column(Date, nullable=False, comment='分区日期')
    event_no: Mapped[Optional[str]] = mapped_column(String(64), comment='事件流水号')
    user_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='用户ID(游客为空)')
    device_id: Mapped[Optional[str]] = mapped_column(String(128), comment='设备ID')
    session_id: Mapped[Optional[str]] = mapped_column(String(128), comment='会话ID')
    search_source: Mapped[Optional[str]] = mapped_column(String(32), comment='搜索入口:首页/分类页/店铺页')
    result_total_cnt: Mapped[Optional[int]] = mapped_column(Integer, comment='搜索结果总数')
    click_rank: Mapped[Optional[int]] = mapped_column(Integer, comment='点击结果位次')
    click_sku_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='点击SKU ID')
    click_spu_id: Mapped[Optional[int]] = mapped_column(BIGINT(unsigned=True), comment='点击SPU ID')
    is_no_result: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'0'"), comment='是否无结果')
    is_search_success: Mapped[Optional[int]] = mapped_column(TINYINT, server_default=text("'1'"), comment='是否成功返回结果')
    channel_code: Mapped[Optional[str]] = mapped_column(String(32), comment='渠道编码')
    client_type: Mapped[Optional[str]] = mapped_column(String(32), comment='客户端类型')
