-- 用户主表 (分库分表: user_id % 128)
CREATE TABLE `t_user` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT COMMENT '主键ID',
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '用户唯一ID',
    `username` VARCHAR(32) NOT NULL COMMENT '用户名',
    `password` VARCHAR(256) NOT NULL COMMENT '加密密码',
    `salt` VARCHAR(32) NOT NULL COMMENT '密码盐值',
    `nickname` VARCHAR(64) DEFAULT NULL COMMENT '昵称',
    `avatar` VARCHAR(512) DEFAULT NULL COMMENT '头像URL',
    `real_name` VARCHAR(64) DEFAULT NULL COMMENT '真实姓名',
    `id_card` VARCHAR(18) DEFAULT NULL COMMENT '身份证号(加密存储)',
    `gender` TINYINT DEFAULT 0 COMMENT '性别:0未知 1男 2女',
    `birthday` DATE DEFAULT NULL COMMENT '生日',
    `mobile` VARCHAR(20) DEFAULT NULL COMMENT '手机号',
    `email` VARCHAR(128) DEFAULT NULL COMMENT '邮箱',
    `user_level` TINYINT DEFAULT 1 COMMENT '用户等级 1-10',
    `user_type` TINYINT DEFAULT 1 COMMENT '用户类型:1普通 2VIP 3企业',
    `status` TINYINT DEFAULT 1 COMMENT '状态:0禁用 1正常 2注销',
    `register_source` TINYINT DEFAULT 1 COMMENT '注册来源:1APP 2小程序 3H5 4PC',
    `register_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `last_login_time` DATETIME DEFAULT NULL,
    `deleted` TINYINT DEFAULT 0 COMMENT '逻辑删除',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_id` (`user_id`),
    UNIQUE KEY `uk_username` (`username`),
    UNIQUE KEY `uk_mobile` (`mobile`),
    KEY `idx_register_time` (`register_time`),
    KEY `idx_user_level` (`user_level`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户主表';

-- 用户地址表 (按user_id分表)
CREATE TABLE `t_user_address` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `address_id` BIGINT UNSIGNED NOT NULL COMMENT '地址ID',
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
    `receiver_name` VARCHAR(64) NOT NULL COMMENT '收货人',
    `receiver_mobile` VARCHAR(20) NOT NULL COMMENT '收货手机',
    `province_code` VARCHAR(20) NOT NULL COMMENT '省份编码',
    `city_code` VARCHAR(20) NOT NULL COMMENT '城市编码',
    `district_code` VARCHAR(20) NOT NULL COMMENT '区县编码',
    `street_code` VARCHAR(20) DEFAULT NULL COMMENT '街道编码',
    `detail_address` VARCHAR(256) NOT NULL COMMENT '详细地址',
    `zip_code` VARCHAR(10) DEFAULT NULL COMMENT '邮编',
    `is_default` TINYINT DEFAULT 0 COMMENT '是否默认:0否 1是',
    `address_tag` VARCHAR(32) DEFAULT NULL COMMENT '地址标签:家/公司/学校',
    `longitude` DECIMAL(10, 7) DEFAULT NULL COMMENT '经度',
    `latitude` DECIMAL(10, 7) DEFAULT NULL COMMENT '纬度',
    `status` TINYINT DEFAULT 1 COMMENT '状态:0删除 1正常',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_address_id` (`address_id`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_location` (`province_code`, `city_code`, `district_code`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户收货地址';

-- 用户安全/认证表
CREATE TABLE `t_user_auth` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `auth_type` TINYINT NOT NULL COMMENT '认证类型:1实名 2人脸 3银行卡',
    `auth_status` TINYINT DEFAULT 0 COMMENT '认证状态:0未认证 1认证中 2已认证 3失败',
    `auth_data` JSON COMMENT '认证数据',
    `cert_no` VARCHAR(64) DEFAULT NULL COMMENT '证件号(脱敏)',
    `cert_img_front` VARCHAR(512) DEFAULT NULL COMMENT '证件正面',
    `cert_img_back` VARCHAR(512) DEFAULT NULL COMMENT '证件反面',
    `verified_at` DATETIME DEFAULT NULL,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_auth_type` (`user_id`, `auth_type`),
    KEY `idx_auth_status` (`auth_status`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户认证信息';

-- 用户第三方登录绑定
CREATE TABLE `t_user_oauth` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `oauth_type` TINYINT NOT NULL COMMENT '类型:1微信 2QQ 3微博 4支付宝 5Apple',
    `oauth_id` VARCHAR(128) NOT NULL COMMENT '第三方唯一ID',
    `union_id` VARCHAR(128) DEFAULT NULL COMMENT 'UnionID(微信)',
    `access_token` VARCHAR(512) DEFAULT NULL COMMENT '访问令牌(加密)',
    `refresh_token` VARCHAR(512) DEFAULT NULL COMMENT '刷新令牌(加密)',
    `expires_at` DATETIME DEFAULT NULL COMMENT '令牌过期时间',
    `oauth_nickname` VARCHAR(64) DEFAULT NULL,
    `oauth_avatar` VARCHAR(512) DEFAULT NULL,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_oauth` (`oauth_type`, `oauth_id`),
    KEY `idx_user_id` (`user_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '第三方登录绑定';

-- 商品分类表 (树形结构)
CREATE TABLE `t_category` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `category_id` BIGINT UNSIGNED NOT NULL COMMENT '分类ID',
    `parent_id` BIGINT UNSIGNED DEFAULT 0 COMMENT '父分类ID,0为根',
    `category_name` VARCHAR(64) NOT NULL COMMENT '分类名称',
    `category_level` TINYINT NOT NULL COMMENT '层级:1一级 2二级 3三级',
    `category_code` VARCHAR(32) NOT NULL COMMENT '分类编码',
    `icon` VARCHAR(512) DEFAULT NULL COMMENT '分类图标',
    `image` VARCHAR(512) DEFAULT NULL COMMENT '分类图片',
    `sort_order` INT DEFAULT 0 COMMENT '排序',
    `is_show` TINYINT DEFAULT 1 COMMENT '是否显示:0否 1是',
    `is_leaf` TINYINT DEFAULT 0 COMMENT '是否叶子节点:0否 1是',
    `path` VARCHAR(256) DEFAULT NULL COMMENT '路径 /1/2/3/',
    `seo_title` VARCHAR(128) DEFAULT NULL,
    `seo_keywords` VARCHAR(256) DEFAULT NULL,
    `seo_description` VARCHAR(512) DEFAULT NULL,
    `status` TINYINT DEFAULT 1 COMMENT '状态:0禁用 1启用',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_category_id` (`category_id`),
    UNIQUE KEY `uk_category_code` (`category_code`),
    KEY `idx_parent_id` (`parent_id`),
    KEY `idx_level_show` (`category_level`, `is_show`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '商品分类';

-- 品牌表
CREATE TABLE `t_brand` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `brand_id` BIGINT UNSIGNED NOT NULL,
    `brand_name` VARCHAR(64) NOT NULL COMMENT '品牌名称',
    `brand_en_name` VARCHAR(64) DEFAULT NULL COMMENT '英文名称',
    `brand_logo` VARCHAR(512) DEFAULT NULL COMMENT '品牌LOGO',
    `brand_desc` TEXT COMMENT '品牌介绍',
    `brand_story` TEXT COMMENT '品牌故事',
    `official_site` VARCHAR(256) DEFAULT NULL COMMENT '官网',
    `country` VARCHAR(32) DEFAULT NULL COMMENT '所属国家',
    `category_ids` JSON COMMENT '关联分类ID数组',
    `sort_order` INT DEFAULT 0,
    `is_hot` TINYINT DEFAULT 0 COMMENT '是否热门',
    `status` TINYINT DEFAULT 1,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_brand_id` (`brand_id`),
    KEY `idx_brand_name` (`brand_name`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '品牌表';

-- SPU表 (标准产品单元) - 按category_id分表
CREATE TABLE `t_spu` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `spu_id` BIGINT UNSIGNED NOT NULL COMMENT 'SPU ID',
    `spu_name` VARCHAR(256) NOT NULL COMMENT 'SPU名称',
    `spu_sub_title` VARCHAR(512) DEFAULT NULL COMMENT '副标题',
    `category_id` BIGINT UNSIGNED NOT NULL COMMENT '三级分类ID',
    `brand_id` BIGINT UNSIGNED DEFAULT NULL COMMENT '品牌ID',
    `shop_id` BIGINT UNSIGNED NOT NULL COMMENT '店铺ID',
    `spu_code` VARCHAR(64) DEFAULT NULL COMMENT 'SPU编码',
    `spu_type` TINYINT DEFAULT 1 COMMENT '类型:1普通商品 2虚拟商品 3组合商品',
    `main_image` VARCHAR(512) NOT NULL COMMENT '主图',
    `sub_images` JSON COMMENT '副图数组',
    `detail_content` LONGTEXT COMMENT '详情HTML',
    `detail_images` JSON COMMENT '详情图片数组',
    `video_url` VARCHAR(512) DEFAULT NULL COMMENT '视频URL',
    `spec_type` TINYINT DEFAULT 1 COMMENT '规格类型:1单规格 2多规格',
    `unit` VARCHAR(16) DEFAULT '件' COMMENT '单位',
    `weight` DECIMAL(10, 3) DEFAULT 0 COMMENT '重量kg',
    `volume` DECIMAL(10, 3) DEFAULT 0 COMMENT '体积m³',
    `delivery_mode` TINYINT DEFAULT 1 COMMENT '配送方式:1快递 2同城 3自提 4电子',
    `after_sale` JSON COMMENT '售后政策',
    `attributes` JSON COMMENT '商品属性 {key:value}',
    `params` JSON COMMENT '规格参数',
    `status` TINYINT DEFAULT 0 COMMENT '状态:0待审核 1上架 2下架 3违规下架',
    `sale_mode` TINYINT DEFAULT 1 COMMENT '销售模式:1现货 2预售 3众筹',
    `sale_num` INT UNSIGNED DEFAULT 0 COMMENT '销量',
    `virtual_num` INT UNSIGNED DEFAULT 0 COMMENT '虚拟销量',
    `comment_num` INT UNSIGNED DEFAULT 0 COMMENT '评论数',
    `good_comment_rate` DECIMAL(3, 2) DEFAULT 1.00 COMMENT '好评率',
    `search_keywords` VARCHAR(512) DEFAULT NULL COMMENT '搜索关键词',
    `seo_info` JSON COMMENT 'SEO信息',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `onsale_time` DATETIME DEFAULT NULL COMMENT '上架时间',
    `offsale_time` DATETIME DEFAULT NULL COMMENT '下架时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_spu_id` (`spu_id`),
    KEY `idx_category_id` (`category_id`),
    KEY `idx_brand_id` (`brand_id`),
    KEY `idx_shop_status` (`shop_id`, `status`),
    KEY `idx_onsale_time` (`onsale_time`),
    FULLTEXT KEY `ft_spu_name` (`spu_name`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'SPU表';

-- SKU表 (库存单元) - 按spu_id分表
CREATE TABLE `t_sku` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `sku_id` BIGINT UNSIGNED NOT NULL COMMENT 'SKU ID',
    `spu_id` BIGINT UNSIGNED NOT NULL COMMENT 'SPU ID',
    `sku_code` VARCHAR(64) DEFAULT NULL COMMENT 'SKU编码',
    `sku_name` VARCHAR(256) NOT NULL COMMENT 'SKU名称(规格组合)',
    `sku_specs` JSON NOT NULL COMMENT '规格组合 [{specId:1,specName:"颜色",specValueId:1,specValueName:"红色"}]',
    `main_image` VARCHAR(512) DEFAULT NULL COMMENT 'SKU图片',
    `price` DECIMAL(12, 2) NOT NULL COMMENT '销售价',
    `market_price` DECIMAL(12, 2) DEFAULT NULL COMMENT '市场价',
    `cost_price` DECIMAL(12, 2) DEFAULT NULL COMMENT '成本价(加密)',
    `stock_num` INT UNSIGNED DEFAULT 0 COMMENT '库存数量',
    `stock_warning` INT UNSIGNED DEFAULT 10 COMMENT '库存预警值',
    `sale_num` INT UNSIGNED DEFAULT 0 COMMENT '销量',
    `barcode` VARCHAR(64) DEFAULT NULL COMMENT '条形码',
    `weight` DECIMAL(10, 3) DEFAULT 0 COMMENT '重量kg',
    `volume` DECIMAL(10, 3) DEFAULT 0 COMMENT '体积m³',
    `status` TINYINT DEFAULT 1 COMMENT '状态:0禁用 1启用',
    `is_default` TINYINT DEFAULT 0 COMMENT '是否默认SKU',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_sku_id` (`sku_id`),
    UNIQUE KEY `uk_spu_specs` (`spu_id`, `sku_specs`), -- 规格组合唯一
    KEY `idx_spu_id` (`spu_id`),
    KEY `idx_price` (`price`),
    KEY `idx_status_stock` (`status`, `stock_num`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'SKU表';

-- 规格属性表
CREATE TABLE `t_spec` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `spec_id` BIGINT UNSIGNED NOT NULL,
    `spec_name` VARCHAR(64) NOT NULL COMMENT '规格名称',
    `category_id` BIGINT UNSIGNED NOT NULL COMMENT '所属分类',
    `spec_type` TINYINT DEFAULT 1 COMMENT '类型:1文本 2颜色 3图片',
    `is_search` TINYINT DEFAULT 0 COMMENT '是否参与搜索',
    `is_must` TINYINT DEFAULT 0 COMMENT '是否必选',
    `sort_order` INT DEFAULT 0,
    `status` TINYINT DEFAULT 1,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_spec_id` (`spec_id`),
    KEY `idx_category_id` (`category_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '规格属性';

-- 规格值表
CREATE TABLE `t_spec_value` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `spec_value_id` BIGINT UNSIGNED NOT NULL,
    `spec_id` BIGINT UNSIGNED NOT NULL,
    `spec_value_name` VARCHAR(64) NOT NULL,
    `spec_value_img` VARCHAR(512) DEFAULT NULL COMMENT '规格图片(颜色/图片类型)',
    `sort_order` INT DEFAULT 0,
    `status` TINYINT DEFAULT 1,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_spec_value_id` (`spec_value_id`),
    KEY `idx_spec_id` (`spec_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '规格值';

-- 库存表 (独立服务, 高并发场景使用Redis+消息队列, 此处为对账)
CREATE TABLE `t_inventory` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `sku_id` BIGINT UNSIGNED NOT NULL,
    `available_stock` INT UNSIGNED DEFAULT 0 COMMENT '可用库存',
    `frozen_stock` INT UNSIGNED DEFAULT 0 COMMENT '冻结库存(已下单未支付)',
    `locked_stock` INT UNSIGNED DEFAULT 0 COMMENT '锁定库存(活动预留)',
    `version` INT UNSIGNED DEFAULT 0 COMMENT '乐观锁版本号',
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_sku_id` (`sku_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '库存表';

-- 订单主表 (按user_id分库, 按创建时间月份分表: t_order_202401)
CREATE TABLE `t_order` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `order_id` BIGINT UNSIGNED NOT NULL COMMENT '订单ID',
    `order_no` VARCHAR(32) NOT NULL COMMENT '订单编号 O20240115120001',
    `user_id` BIGINT UNSIGNED NOT NULL,
    `shop_id` BIGINT UNSIGNED NOT NULL COMMENT '店铺ID(平台模式)',
    `order_type` TINYINT DEFAULT 1 COMMENT '订单类型:1普通 2秒杀 3团购 4预售',
    `order_status` TINYINT NOT NULL DEFAULT 10 COMMENT '状态:10待付款 20待发货 30待收货 40已完成 50已取消 60售后中',
    `pay_status` TINYINT DEFAULT 0 COMMENT '支付状态:0未支付 1部分支付 2已支付 3已退款',
    `delivery_status` TINYINT DEFAULT 0 COMMENT '物流状态:0未发货 1部分发货 2已发货 3已签收',
    `settlement_status` TINYINT DEFAULT 0 COMMENT '结算状态:0未结算 1结算中 2已结算',
    -- 金额信息
    `total_amount` DECIMAL(12, 2) NOT NULL COMMENT '商品总金额',
    `discount_amount` DECIMAL(12, 2) DEFAULT 0 COMMENT '优惠金额',
    `freight_amount` DECIMAL(12, 2) DEFAULT 0 COMMENT '运费',
    `tax_amount` DECIMAL(12, 2) DEFAULT 0 COMMENT '税费',
    `pay_amount` DECIMAL(12, 2) NOT NULL COMMENT '应付金额',
    `paid_amount` DECIMAL(12, 2) DEFAULT 0 COMMENT '实付金额',
    -- 收货信息(快照)
    `receiver_name` VARCHAR(64) NOT NULL,
    `receiver_mobile` VARCHAR(20) NOT NULL,
    `receiver_address` VARCHAR(512) NOT NULL,
    `receiver_zip` VARCHAR(10) DEFAULT NULL,
    `receiver_longitude` DECIMAL(10, 7) DEFAULT NULL,
    `receiver_latitude` DECIMAL(10, 7) DEFAULT NULL,
    -- 物流信息
    `delivery_type` TINYINT DEFAULT 1 COMMENT '配送方式',
    `delivery_time_type` TINYINT DEFAULT 1 COMMENT '配送时间:1 anytime 2 workday 3 weekend',
    `delivery_remark` VARCHAR(256) DEFAULT NULL,
    `expect_delivery_time` DATETIME DEFAULT NULL COMMENT '期望送达时间',
    -- 支付信息
    `pay_time` DATETIME DEFAULT NULL,
    `pay_channel` TINYINT DEFAULT NULL COMMENT '支付渠道',
    `pay_trade_no` VARCHAR(128) DEFAULT NULL COMMENT '第三方支付流水',
    `pay_timeout` DATETIME NOT NULL COMMENT '支付超时时间',
    -- 时间戳
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `pay_time` DATETIME DEFAULT NULL,
    `delivery_time` DATETIME DEFAULT NULL,
    `receive_time` DATETIME DEFAULT NULL,
    `finish_time` DATETIME DEFAULT NULL,
    `cancel_time` DATETIME DEFAULT NULL,
    `cancel_reason` VARCHAR(256) DEFAULT NULL,
    `user_remark` VARCHAR(512) DEFAULT NULL COMMENT '用户备注',
    `merchant_remark` VARCHAR(512) DEFAULT NULL COMMENT '商家备注',
    `source` TINYINT DEFAULT 1 COMMENT '来源:1APP 2小程序 3H5 4PC',
    `device_id` VARCHAR(64) DEFAULT NULL,
    `ip_address` VARCHAR(64) DEFAULT NULL,
    `deleted` TINYINT DEFAULT 0,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_order_id` (`order_id`),
    UNIQUE KEY `uk_order_no` (`order_no`),
    KEY `idx_user_id` (`user_id`, `order_status`),
    KEY `idx_shop_status` (`shop_id`, `order_status`),
    KEY `idx_create_time` (`create_time`),
    KEY `idx_pay_time` (`pay_time`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '订单主表';

-- 订单商品明细表 (与订单表同库同表后缀)
CREATE TABLE `t_order_item` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `item_id` BIGINT UNSIGNED NOT NULL,
    `order_id` BIGINT UNSIGNED NOT NULL,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `shop_id` BIGINT UNSIGNED NOT NULL,
    -- 商品信息快照
    `spu_id` BIGINT UNSIGNED NOT NULL,
    `sku_id` BIGINT UNSIGNED NOT NULL,
    `spu_name` VARCHAR(256) NOT NULL,
    `sku_name` VARCHAR(256) NOT NULL,
    `sku_image` VARCHAR(512) DEFAULT NULL,
    `sku_specs` JSON COMMENT '规格快照',
    `sku_code` VARCHAR(64) DEFAULT NULL,
    -- 价格信息
    `original_price` DECIMAL(12, 2) NOT NULL COMMENT '原价',
    `sale_price` DECIMAL(12, 2) NOT NULL COMMENT '销售价',
    `cost_price` DECIMAL(12, 2) DEFAULT NULL COMMENT '成本价',
    `quantity` INT UNSIGNED NOT NULL COMMENT '数量',
    `subtotal_amount` DECIMAL(12, 2) NOT NULL COMMENT '小计金额',
    `discount_amount` DECIMAL(12, 2) DEFAULT 0 COMMENT '分摊优惠',
    `pay_amount` DECIMAL(12, 2) NOT NULL COMMENT '实付金额',
    -- 售后信息
    `aftersale_status` TINYINT DEFAULT 0 COMMENT '售后状态:0无 1申请中 2退款中 3已退款 4退货中 5已退货',
    `aftersale_id` BIGINT UNSIGNED DEFAULT NULL,
    -- 物流
    `delivery_id` BIGINT UNSIGNED DEFAULT NULL COMMENT '发货单ID',
    `is_commented` TINYINT DEFAULT 0,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_item_id` (`item_id`),
    KEY `idx_order_id` (`order_id`),
    KEY `idx_sku_id` (`sku_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '订单商品明细';

-- 订单状态流水 (与订单表同库)
CREATE TABLE `t_order_status_log` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `order_id` BIGINT UNSIGNED NOT NULL,
    `pre_status` TINYINT DEFAULT NULL COMMENT '原状态',
    `current_status` TINYINT NOT NULL COMMENT '当前状态',
    `operate_type` TINYINT NOT NULL COMMENT '操作类型:1系统自动 2用户 3商家 4平台',
    `operator_id` BIGINT UNSIGNED DEFAULT NULL,
    `operator_name` VARCHAR(64) DEFAULT NULL,
    `remark` VARCHAR(256) DEFAULT NULL,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_order_id` (`order_id`),
    KEY `idx_created_at` (`created_at`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '订单状态流水';

-- 支付流水表 (独立库, 高一致性和审计要求)
CREATE TABLE `t_payment` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `payment_id` BIGINT UNSIGNED NOT NULL,
    `payment_no` VARCHAR(32) NOT NULL COMMENT '支付流水号',
    `order_id` BIGINT UNSIGNED NOT NULL,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `payment_type` TINYINT NOT NULL COMMENT '类型:1支付 2退款',
    -- 金额
    `amount` DECIMAL(12, 2) NOT NULL,
    `currency` VARCHAR(8) DEFAULT 'CNY',
    -- 渠道信息
    `channel_code` VARCHAR(32) NOT NULL COMMENT '渠道:wechat alipay unionpay',
    `channel_name` VARCHAR(64) DEFAULT NULL,
    `channel_trade_no` VARCHAR(128) DEFAULT NULL COMMENT '渠道流水号',
    `channel_prepay_id` VARCHAR(128) DEFAULT NULL COMMENT '预支付ID',
    `channel_response` JSON COMMENT '渠道响应原始数据',
    -- 状态
    `status` TINYINT DEFAULT 0 COMMENT '0待支付 1支付中 2成功 3失败 4关闭',
    `paid_time` DATETIME DEFAULT NULL,
    `notify_time` DATETIME DEFAULT NULL,
    `notify_data` JSON COMMENT '异步通知数据',
    -- 退款关联
    `refund_id` BIGINT UNSIGNED DEFAULT NULL,
    `original_payment_id` BIGINT UNSIGNED DEFAULT NULL COMMENT '原支付ID(退款用)',
    `client_ip` VARCHAR(64) DEFAULT NULL,
    `expire_time` DATETIME DEFAULT NULL,
    `remark` VARCHAR(256) DEFAULT NULL,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_payment_id` (`payment_id`),
    UNIQUE KEY `uk_payment_no` (`payment_no`),
    UNIQUE KEY `uk_channel_trade` (`channel_code`, `channel_trade_no`),
    KEY `idx_order_id` (`order_id`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_status_time` (`status`, `created_at`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '支付流水';

-- 购物车表 (Redis为主, 数据库为辅助持久化)
CREATE TABLE `t_cart` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `sku_id` BIGINT UNSIGNED NOT NULL,
    `spu_id` BIGINT UNSIGNED NOT NULL,
    `shop_id` BIGINT UNSIGNED NOT NULL,
    `quantity` INT UNSIGNED NOT NULL DEFAULT 1,
    `sku_specs` JSON COMMENT '规格快照',
    `is_selected` TINYINT DEFAULT 1 COMMENT '是否选中',
    `is_valid` TINYINT DEFAULT 1 COMMENT '是否有效(商品下架等)',
    `invalid_reason` VARCHAR(128) DEFAULT NULL,
    `source` TINYINT DEFAULT 1,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_sku` (`user_id`, `sku_id`),
    KEY `idx_user_selected` (`user_id`, `is_selected`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '购物车';

-- 优惠券表
CREATE TABLE `t_coupon` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `coupon_id` BIGINT UNSIGNED NOT NULL,
    `coupon_name` VARCHAR(128) NOT NULL,
    `coupon_type` TINYINT NOT NULL COMMENT '类型:1满减 2折扣 3随机减 4无门槛',
    `coupon_value` DECIMAL(12, 2) NOT NULL COMMENT '面值/折扣率',
    `min_amount` DECIMAL(12, 2) DEFAULT 0 COMMENT '最低消费金额',
    `max_discount` DECIMAL(12, 2) DEFAULT NULL COMMENT '最大优惠金额(折扣券)',
    -- 发放规则
    `total_count` INT UNSIGNED DEFAULT 0 COMMENT '总发行量,0无限',
    `user_limit` INT UNSIGNED DEFAULT 1 COMMENT '每人限领',
    `claim_count` INT UNSIGNED DEFAULT 0 COMMENT '已领取数量',
    `use_count` INT UNSIGNED DEFAULT 0 COMMENT '已使用数量',
    -- 有效期
    `valid_type` TINYINT DEFAULT 1 COMMENT '1固定时间 2领取后X天',
    `valid_start_time` DATETIME DEFAULT NULL,
    `valid_end_time` DATETIME DEFAULT NULL,
    `valid_days` INT UNSIGNED DEFAULT NULL COMMENT '领取后有效天数',
    -- 使用范围
    `use_scope_type` TINYINT DEFAULT 1 COMMENT '1全平台 2指定分类 3指定商品 4指定店铺',
    `use_scope_ids` JSON COMMENT '适用范围ID列表',
    `use_scope_rules` JSON COMMENT '额外规则',
    -- 互斥规则
    `is_stack` TINYINT DEFAULT 0 COMMENT '是否可叠加',
    `mutex_coupon_ids` JSON COMMENT '互斥优惠券ID',
    `status` TINYINT DEFAULT 1 COMMENT '0待发放 1发放中 2已结束 3已作废',
    `create_time` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `claim_start_time` DATETIME DEFAULT NULL,
    `claim_end_time` DATETIME DEFAULT NULL,
    `created_by` BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_coupon_id` (`coupon_id`),
    KEY `idx_status_time` (`status`, `claim_start_time`, `claim_end_time`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '优惠券';

-- 用户优惠券表 (按user_id分表)
CREATE TABLE `t_user_coupon` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `user_coupon_id` BIGINT UNSIGNED NOT NULL,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `coupon_id` BIGINT UNSIGNED NOT NULL,
    `coupon_name` VARCHAR(128) NOT NULL,
    `coupon_type` TINYINT NOT NULL,
    `coupon_value` DECIMAL(12, 2) NOT NULL,
    `min_amount` DECIMAL(12, 2) DEFAULT 0,
    -- 有效期
    `valid_start_time` DATETIME NOT NULL,
    `valid_end_time` DATETIME NOT NULL,
    -- 使用状态
    `status` TINYINT DEFAULT 1 COMMENT '1未使用 2已使用 3已过期 4已作废',
    `use_time` DATETIME DEFAULT NULL,
    `use_order_id` BIGINT UNSIGNED DEFAULT NULL,
    `use_order_no` VARCHAR(32) DEFAULT NULL,
    `claim_time` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `source` TINYINT DEFAULT 1 COMMENT '领取来源',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_coupon_id` (`user_coupon_id`),
    KEY `idx_user_status` (`user_id`, `status`),
    KEY `idx_valid_time` (`valid_end_time`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户优惠券';

-- 活动表 (秒杀/拼团/预售等)
CREATE TABLE `t_activity` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `activity_id` BIGINT UNSIGNED NOT NULL,
    `activity_name` VARCHAR(128) NOT NULL,
    `activity_type` TINYINT NOT NULL COMMENT '1秒杀 2拼团 3预售 4满减 5折扣',
    `activity_status` TINYINT DEFAULT 0 COMMENT '0未开始 1进行中 2已结束 3已取消',
    -- 时间规则
    `start_time` DATETIME NOT NULL,
    `end_time` DATETIME NOT NULL,
    `warmup_time` DATETIME DEFAULT NULL COMMENT '预热时间',
    -- 参与规则
    `user_limit` INT UNSIGNED DEFAULT 0 COMMENT '单人限购,0不限',
    `total_limit` INT UNSIGNED DEFAULT 0 COMMENT '活动总限购',
    -- 营销规则(JSON灵活配置)
    `activity_rules` JSON NOT NULL COMMENT '活动规则',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_activity_id` (`activity_id`),
    KEY `idx_type_status_time` (
        `activity_type`, `activity_status`, `start_time`
    )
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '营销活动';

-- 活动商品关联表
CREATE TABLE `t_activity_sku` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `activity_id` BIGINT UNSIGNED NOT NULL,
    `sku_id` BIGINT UNSIGNED NOT NULL,
    `spu_id` BIGINT UNSIGNED NOT NULL,
    `shop_id` BIGINT UNSIGNED NOT NULL,
    -- 活动价格
    `activity_price` DECIMAL(12, 2) NOT NULL,
    `original_price` DECIMAL(12, 2) NOT NULL,
    -- 库存
    `activity_stock` INT UNSIGNED NOT NULL COMMENT '活动库存',
    `sold_stock` INT UNSIGNED DEFAULT 0,
    `freeze_stock` INT UNSIGNED DEFAULT 0,
    -- 限购
    `user_buy_limit` INT UNSIGNED DEFAULT 0,
    -- 排序权重
    `sort_order` INT DEFAULT 0,
    `status` TINYINT DEFAULT 1,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_activity_sku` (`activity_id`, `sku_id`),
    KEY `idx_activity_id` (`activity_id`),
    KEY `idx_sku_id` (`sku_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '活动商品';

-- 发货单表
CREATE TABLE `t_delivery` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `delivery_id` BIGINT UNSIGNED NOT NULL,
    `delivery_no` VARCHAR(32) NOT NULL COMMENT '发货单号',
    `order_id` BIGINT UNSIGNED NOT NULL,
    `order_no` VARCHAR(32) NOT NULL,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `shop_id` BIGINT UNSIGNED NOT NULL,

    -- 物流信息
    `logistics_company` VARCHAR(64) NOT NULL COMMENT '物流公司',
    `logistics_code` VARCHAR(32) NOT NULL COMMENT '物流编码',
    `tracking_no` VARCHAR(64) NOT NULL COMMENT '物流单号',
    `tracking_url` VARCHAR(512) DEFAULT NULL,

    -- 发货信息
    `sender_name` VARCHAR(64) NOT NULL,
    `sender_mobile` VARCHAR(20) NOT NULL,
    `sender_address` VARCHAR(256) NOT NULL,
    `receiver_name` VARCHAR(64) NOT NULL,
    `receiver_mobile` VARCHAR(20) NOT NULL,
    `receiver_address` VARCHAR(256) NOT NULL,

    -- 状态
    `status` TINYINT DEFAULT 1 COMMENT '1已发货 2运输中 3已签收 4异常',
    `ship_time` DATETIME NOT NULL,
    `receive_time` DATETIME DEFAULT NULL,

    -- 物流轨迹(冗余最新一条)
    `latest_trace` VARCHAR(512) DEFAULT NULL,
    `latest_trace_time` DATETIME DEFAULT NULL,

    `remark` VARCHAR(256) DEFAULT NULL,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_delivery_id` (`delivery_id`),
    UNIQUE KEY `uk_tracking_no` (`logistics_code`, `tracking_no`),
    KEY `idx_order_id` (`order_id`),
    KEY `idx_user_id` (`user_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '发货单';

-- 物流轨迹表 (可存HBase或ES, 此处为备份)
CREATE TABLE `t_delivery_trace` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `delivery_id` BIGINT UNSIGNED NOT NULL,
    `tracking_no` VARCHAR(64) NOT NULL,
    `trace_time` DATETIME NOT NULL COMMENT '轨迹时间',
    `trace_desc` VARCHAR(512) NOT NULL,
    `trace_location` VARCHAR(128) DEFAULT NULL,
    `trace_status` VARCHAR(32) DEFAULT NULL COMMENT '物流状态码',
    `operator_code` VARCHAR(64) DEFAULT NULL,
    `operator_name` VARCHAR(64) DEFAULT NULL,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_delivery_id` (`delivery_id`),
    KEY `idx_tracking_time` (`tracking_no`, `trace_time`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '物流轨迹';

-- 仓库表
CREATE TABLE `t_warehouse` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `warehouse_id` BIGINT UNSIGNED NOT NULL,
    `warehouse_name` VARCHAR(64) NOT NULL,
    `warehouse_code` VARCHAR(32) NOT NULL,
    `warehouse_type` TINYINT DEFAULT 1 COMMENT '1自营 2第三方',
    `province_code` VARCHAR(20) NOT NULL,
    `city_code` VARCHAR(20) NOT NULL,
    `district_code` VARCHAR(20) NOT NULL,
    `detail_address` VARCHAR(256) NOT NULL,
    `contact_name` VARCHAR(64) DEFAULT NULL,
    `contact_mobile` VARCHAR(20) DEFAULT NULL,
    `status` TINYINT DEFAULT 1,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_warehouse_id` (`warehouse_id`),
    UNIQUE KEY `uk_warehouse_code` (`warehouse_code`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '仓库';

-- 仓库库存表
CREATE TABLE `t_warehouse_stock` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `warehouse_id` BIGINT UNSIGNED NOT NULL,
    `sku_id` BIGINT UNSIGNED NOT NULL,
    `stock_num` INT UNSIGNED DEFAULT 0,
    `available_stock` INT UNSIGNED DEFAULT 0,
    `locked_stock` INT UNSIGNED DEFAULT 0,
    `warning_stock` INT UNSIGNED DEFAULT 10,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_warehouse_sku` (`warehouse_id`, `sku_id`),
    KEY `idx_sku_id` (`sku_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '仓库库存';

-- 售后单表
CREATE TABLE `t_aftersale` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `aftersale_id` BIGINT UNSIGNED NOT NULL,
    `aftersale_no` VARCHAR(32) NOT NULL COMMENT '售后单号',
    `order_id` BIGINT UNSIGNED NOT NULL,
    `order_no` VARCHAR(32) NOT NULL,
    `order_item_id` BIGINT UNSIGNED NOT NULL,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `shop_id` BIGINT UNSIGNED NOT NULL,
    -- 售后类型
    `aftersale_type` TINYINT NOT NULL COMMENT '1退款 2退货退款 3换货 4维修',
    `aftersale_reason` VARCHAR(256) NOT NULL COMMENT '售后原因',
    `reason_code` VARCHAR(32) NOT NULL COMMENT '原因编码',
    `user_desc` TEXT COMMENT '用户描述',
    `user_images` JSON COMMENT '用户上传图片',
    -- 金额
    `refund_amount` DECIMAL(12, 2) NOT NULL COMMENT '申请退款金额',
    `actual_refund_amount` DECIMAL(12, 2) DEFAULT NULL COMMENT '实际退款金额',
    -- 状态流转
    `status` TINYINT DEFAULT 10 COMMENT '10待审核 20商家同意 30买家退货 40商家收货 50退款中 60已完成 70已拒绝 80已取消',
    `audit_remark` VARCHAR(256) DEFAULT NULL COMMENT '审核备注',
    `audit_time` DATETIME DEFAULT NULL,
    `audit_by` BIGINT UNSIGNED DEFAULT NULL,
    -- 物流(退货用)
    `return_logistics_company` VARCHAR(64) DEFAULT NULL,
    `return_tracking_no` VARCHAR(64) DEFAULT NULL,
    `return_ship_time` DATETIME DEFAULT NULL,
    `return_receive_time` DATETIME DEFAULT NULL,
    -- 退款信息
    `refund_time` DATETIME DEFAULT NULL,
    `refund_payment_id` BIGINT UNSIGNED DEFAULT NULL,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_aftersale_id` (`aftersale_id`),
    UNIQUE KEY `uk_aftersale_no` (`aftersale_no`),
    KEY `idx_order_id` (`order_id`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_status` (`status`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '售后单';

-- 售后协商记录
CREATE TABLE `t_aftersale_negotiation` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `aftersale_id` BIGINT UNSIGNED NOT NULL,
    `negotiation_type` TINYINT NOT NULL COMMENT '1用户留言 2商家留言 3系统通知',
    `content` TEXT NOT NULL,
    `images` JSON,
    `operator_id` BIGINT UNSIGNED DEFAULT NULL,
    `operator_name` VARCHAR(64) DEFAULT NULL,
    `operator_role` TINYINT DEFAULT NULL COMMENT '1用户 2商家 3平台',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_aftersale_id` (`aftersale_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '售后协商记录';

-- 评价表
CREATE TABLE `t_review` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `review_id` BIGINT UNSIGNED NOT NULL,
    `order_id` BIGINT UNSIGNED NOT NULL,
    `order_item_id` BIGINT UNSIGNED NOT NULL,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `shop_id` BIGINT UNSIGNED NOT NULL,
    `spu_id` BIGINT UNSIGNED NOT NULL,
    `sku_id` BIGINT UNSIGNED NOT NULL,

    -- 评分
    `overall_score` TINYINT NOT NULL COMMENT '综合评分 1-5',
    `desc_score` TINYINT DEFAULT NULL COMMENT '描述相符',
    `logistics_score` TINYINT DEFAULT NULL COMMENT '物流服务',
    `service_score` TINYINT DEFAULT NULL COMMENT '服务态度',

    -- 内容
    `content` TEXT COMMENT '评价内容',
    `images` JSON COMMENT '图片数组',
    `video_url` VARCHAR(512) DEFAULT NULL,
    `is_anonymous` TINYINT DEFAULT 0 COMMENT '是否匿名',

    -- 标签
    `tags` JSON COMMENT '评价标签',
    -- 商家回复
    `shop_reply` VARCHAR(512) DEFAULT NULL,
    `shop_reply_time` DATETIME DEFAULT NULL,

    -- 点赞数
    `like_count` INT UNSIGNED DEFAULT 0,
    `is_top` TINYINT DEFAULT 0 COMMENT '是否置顶',
    `is_quality` TINYINT DEFAULT 0 COMMENT '是否优质评价',

    -- 状态
    `status` TINYINT DEFAULT 1 COMMENT '1显示 2隐藏 3审核中',
    `audit_reason` VARCHAR(256) DEFAULT NULL,

    `create_time` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_review_id` (`review_id`),
    UNIQUE KEY `uk_order_item` (`order_item_id`), -- 一个订单商品只能评价一次
    KEY `idx_sku_id` (`sku_id`),
    KEY `idx_spu_id` (`spu_id`),
    KEY `idx_shop_id` (`shop_id`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_score` (`overall_score`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '商品评价';

-- 店铺表
CREATE TABLE `t_shop` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `shop_id` BIGINT UNSIGNED NOT NULL,
    `shop_name` VARCHAR(128) NOT NULL COMMENT '店铺名称',
    `shop_code` VARCHAR(32) DEFAULT NULL COMMENT '店铺编码',
    `shop_logo` VARCHAR(512) DEFAULT NULL,
    `shop_banner` VARCHAR(512) DEFAULT NULL,
    `shop_desc` TEXT COMMENT '店铺简介',
    -- 经营信息
    `shop_type` TINYINT DEFAULT 1 COMMENT '1旗舰店 2专卖店 3专营店 4普通店',
    `business_category` JSON COMMENT '经营类目 [{categoryId:1,level:3}]',
    `main_category` BIGINT UNSIGNED DEFAULT NULL COMMENT '主营类目',
    -- 资质信息
    `company_name` VARCHAR(128) NOT NULL COMMENT '企业名称',
    `business_license` VARCHAR(64) NOT NULL COMMENT '营业执照号',
    `legal_person` VARCHAR(64) NOT NULL COMMENT '法人',
    `company_address` VARCHAR(256) DEFAULT NULL,
    -- 联系人
    `contact_name` VARCHAR(64) NOT NULL,
    `contact_mobile` VARCHAR(20) NOT NULL,
    `contact_email` VARCHAR(128) DEFAULT NULL,
    -- 财务信息
    `settlement_bank` VARCHAR(128) DEFAULT NULL,
    `settlement_account` VARCHAR(64) DEFAULT NULL,
    `settlement_rate` DECIMAL(3, 2) DEFAULT 0.05 COMMENT '平台扣点率',
    -- 店铺状态
    `shop_status` TINYINT DEFAULT 0 COMMENT '0入驻中 1审核中 2已开业 3已歇业 4已关闭',
    `verify_status` TINYINT DEFAULT 0 COMMENT '资质审核状态',
    -- 评分统计
    `desc_score` DECIMAL(2, 1) DEFAULT 5.0,
    `service_score` DECIMAL(2, 1) DEFAULT 5.0,
    `logistics_score` DECIMAL(2, 1) DEFAULT 5.0,
    `create_time` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `open_time` DATETIME DEFAULT NULL COMMENT '开业时间',
    `close_time` DATETIME DEFAULT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_shop_id` (`shop_id`),
    UNIQUE KEY `uk_shop_name` (`shop_name`),
    KEY `idx_shop_status` (`shop_status`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '店铺';

-- 商家结算表
CREATE TABLE `t_settlement` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `settlement_id` BIGINT UNSIGNED NOT NULL,
    `settlement_no` VARCHAR(32) NOT NULL COMMENT '结算单号',
    `shop_id` BIGINT UNSIGNED NOT NULL,
    `settlement_period` VARCHAR(16) NOT NULL COMMENT '结算周期 202401',
    -- 订单金额
    `order_amount` DECIMAL(14, 2) NOT NULL COMMENT '订单金额',
    `goods_amount` DECIMAL(14, 2) NOT NULL COMMENT '商品金额',
    `freight_amount` DECIMAL(14, 2) NOT NULL COMMENT '运费金额',
    -- 优惠扣减
    `platform_discount` DECIMAL(14, 2) DEFAULT 0 COMMENT '平台优惠',
    `shop_discount` DECIMAL(14, 2) DEFAULT 0 COMMENT '店铺优惠',
    -- 费用
    `platform_commission` DECIMAL(14, 2) DEFAULT 0 COMMENT '平台佣金',
    `payment_fee` DECIMAL(14, 2) DEFAULT 0 COMMENT '支付手续费',
    `settlement_amount` DECIMAL(14, 2) NOT NULL COMMENT '应结算金额',
    -- 状态
    `status` TINYINT DEFAULT 0 COMMENT '0待结算 1结算中 2已结算 3已打款',
    `bill_time` DATETIME DEFAULT NULL COMMENT '出账时间',
    `pay_time` DATETIME DEFAULT NULL COMMENT '打款时间',
    `pay_voucher` VARCHAR(256) DEFAULT NULL COMMENT '打款凭证',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_settlement_id` (`settlement_id`),
    UNIQUE KEY `uk_shop_period` (`shop_id`, `settlement_period`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '商家结算';

-- 结算明细表
CREATE TABLE `t_settlement_detail` (
    `id` BIGINT UNSIGNED AUTO_INCREMENT,
    `settlement_id` BIGINT UNSIGNED NOT NULL,
    `order_id` BIGINT UNSIGNED NOT NULL,
    `order_no` VARCHAR(32) NOT NULL,
    `order_amount` DECIMAL(12, 2) NOT NULL,
    `settlement_amount` DECIMAL(12, 2) NOT NULL,
    `commission_amount` DECIMAL(12, 2) DEFAULT 0,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_settlement_id` (`settlement_id`),
    KEY `idx_order_id` (`order_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '结算明细';
