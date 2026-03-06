"""品牌商品词库。"""

BRAND_PRODUCT_CATALOG = {
    "Apple": {
        "allowed_categories": ["手机通讯", "数码电子", "电脑办公"],
        "series_words": ["iPhone", "iPad", "MacBook", "Apple Watch", "AirPods"],
        "product_words": ["手机", "平板电脑", "笔记本电脑", "智能手表", "无线耳机"],
    },
    "华为": {
        "allowed_categories": ["手机通讯", "数码电子", "电脑办公"],
        "series_words": ["Mate", "Pura", "nova", "MateBook", "FreeBuds", "Watch"],
        "product_words": ["手机", "笔记本电脑", "平板电脑", "智能手表", "蓝牙耳机"],
    },
    "小米": {
        "allowed_categories": ["手机通讯", "数码电子", "家用电器"],
        "series_words": ["数字系列", "Redmi", "小米平板", "小米手环", "米家"],
        "product_words": ["手机", "平板电脑", "智能手环", "空气净化器", "扫地机器人"],
    },
    "荣耀": {
        "allowed_categories": ["手机通讯", "数码电子", "电脑办公"],
        "series_words": ["Magic", "数字系列", "X系列", "平板", "笔记本"],
        "product_words": ["手机", "平板电脑", "笔记本电脑", "智能手表", "耳机"],
    },
    "OPPO": {
        "allowed_categories": ["手机通讯", "数码电子"],
        "series_words": ["Find", "Reno", "A系列", "Pad", "Enco"],
        "product_words": ["手机", "平板电脑", "蓝牙耳机", "智能手表", "充电套装"],
    },
    "vivo": {
        "allowed_categories": ["手机通讯", "数码电子"],
        "series_words": ["X系列", "S系列", "Y系列", "Pad", "TWS"],
        "product_words": ["手机", "平板电脑", "蓝牙耳机", "智能手表", "充电套装"],
    },
    "三星": {
        "allowed_categories": ["手机通讯", "数码电子", "家用电器"],
        "series_words": ["Galaxy S", "Galaxy Z", "Galaxy Tab", "玄龙骑士", "Neo QLED"],
        "product_words": ["手机", "平板电脑", "显示器", "电视", "存储卡"],
    },
    "索尼": {
        "allowed_categories": ["数码电子", "家用电器"],
        "series_words": ["Alpha", "LinkBuds", "WH", "BRAVIA", "PlayStation"],
        "product_words": ["相机", "耳机", "电视", "游戏主机", "镜头"],
    },
    "佳能": {
        "allowed_categories": ["数码电子", "电脑办公"],
        "series_words": ["EOS R", "EOS", "PowerShot", "imageCLASS", "SELPHY"],
        "product_words": ["相机", "镜头", "打印机", "照片打印机", "摄影套装"],
    },
    "尼康": {
        "allowed_categories": ["数码电子"],
        "series_words": ["Z系列", "D系列", "COOLPIX", "尼克尔"],
        "product_words": ["相机", "镜头", "摄影套装", "望远镜"],
    },
    "大疆": {
        "allowed_categories": ["数码电子"],
        "series_words": ["Mini", "Air", "Mavic", "Osmo", "Mic"],
        "product_words": ["无人机", "手持云台", "运动相机", "麦克风", "航拍套装"],
    },
    "海康威视": {
        "allowed_categories": ["数码电子", "电脑办公"],
        "series_words": ["萤石", "威视", "智联", "守护者"],
        "product_words": ["监控摄像头", "录像机", "门锁", "存储卡", "安防套装"],
    },
    "联想": {
        "allowed_categories": ["电脑办公", "数码电子"],
        "series_words": ["小新", "拯救者", "ThinkCentre", "YOGA", "天逸"],
        "product_words": ["笔记本电脑", "台式机", "一体机", "显示器", "平板电脑"],
    },
    "ThinkPad": {
        "allowed_categories": ["电脑办公"],
        "series_words": ["X1", "T系列", "E系列", "P系列", "L系列"],
        "product_words": ["笔记本电脑", "商务本", "扩展坞", "电源适配器"],
    },
    "戴尔": {
        "allowed_categories": ["电脑办公"],
        "series_words": ["XPS", "灵越", "游匣", "成就", "外星人"],
        "product_words": ["笔记本电脑", "台式机", "显示器", "工作站", "键鼠套装"],
    },
    "惠普": {
        "allowed_categories": ["电脑办公"],
        "series_words": ["战", "暗影精灵", "星", "光影精灵", "LaserJet"],
        "product_words": ["笔记本电脑", "打印机", "显示器", "台式机", "墨盒"],
    },
    "华硕": {
        "allowed_categories": ["电脑办公", "数码电子"],
        "series_words": ["天选", "灵耀", "无畏", "ROG", "ProArt"],
        "product_words": ["笔记本电脑", "主板", "显卡", "显示器", "路由器"],
    },
    "机械革命": {
        "allowed_categories": ["电脑办公"],
        "series_words": ["极光", "旷世", "蛟龙", "翼龙", "耀世"],
        "product_words": ["游戏本", "轻薄本", "台式机", "显示器"],
    },
    "美的": {
        "allowed_categories": ["家用电器", "家居家装"],
        "series_words": ["风尊", "领鲜者", "净矿", "极光", "小白鲸"],
        "product_words": ["空调", "冰箱", "洗衣机", "电饭煲", "空气炸锅"],
    },
    "海尔": {
        "allowed_categories": ["家用电器"],
        "series_words": ["云溪", "麦浪", "山茶花", "Leader", "卡萨帝"],
        "product_words": ["冰箱", "洗衣机", "空调", "热水器", "冷柜"],
    },
    "格力": {
        "allowed_categories": ["家用电器"],
        "series_words": ["云佳", "云逸", "风无界", "京逸", "臻新风"],
        "product_words": ["空调", "电风扇", "净化器", "取暖器", "除湿机"],
    },
    "西门子家电": {
        "allowed_categories": ["家用电器"],
        "series_words": ["智感", "零度", "iQ300", "iQ500", "晶蕾"],
        "product_words": ["冰箱", "洗衣机", "洗碗机", "烤箱", "烟灶套装"],
    },
    "戴森": {
        "allowed_categories": ["家用电器", "美妆个护"],
        "series_words": ["V系列", "Gen5", "Supersonic", "Airwrap", "Purifier"],
        "product_words": ["吸尘器", "吹风机", "卷发器", "空气净化器", "洗地机"],
    },
    "科沃斯": {
        "allowed_categories": ["家用电器", "家居家装"],
        "series_words": ["地宝", "窗宝", "沁宝", "机器人"],
        "product_words": ["扫地机器人", "擦窗机器人", "空气净化器", "清洁套装"],
    },
    "追觅": {
        "allowed_categories": ["家用电器", "家居家装"],
        "series_words": ["H系列", "V系列", "L系列", "Pocket"],
        "product_words": ["吸尘器", "洗地机", "扫地机器人", "吹风机", "除螨仪"],
    },
    "耐克": {
        "allowed_categories": ["运动户外", "鞋靴箱包", "服饰内衣"],
        "series_words": ["Air Max", "Pegasus", "Dri-FIT", "Jordan", "Court Vision"],
        "product_words": ["跑步鞋", "篮球鞋", "运动T恤", "运动外套", "双肩包"],
    },
    "阿迪达斯": {
        "allowed_categories": ["运动户外", "鞋靴箱包", "服饰内衣"],
        "series_words": ["UltraBoost", "Superstar", "Climacool", "Adizero", "Originals"],
        "product_words": ["跑步鞋", "板鞋", "运动裤", "卫衣", "运动背包"],
    },
    "安踏": {
        "allowed_categories": ["运动户外", "鞋靴箱包", "服饰内衣"],
        "series_words": ["冠军", "KT", "星岳", "毒刺", "氢跑"],
        "product_words": ["跑步鞋", "篮球鞋", "运动服", "运动裤", "羽绒服"],
    },
    "李宁": {
        "allowed_categories": ["运动户外", "鞋靴箱包", "服饰内衣"],
        "series_words": ["赤兔", "超轻", "韦德", "中国李宁", "烈骏"],
        "product_words": ["跑步鞋", "篮球鞋", "运动卫衣", "运动短裤", "训练套装"],
    },
    "斯凯奇": {
        "allowed_categories": ["运动户外", "鞋靴箱包"],
        "series_words": ["GO WALK", "DLites", "Arch Fit", "Max Cushioning"],
        "product_words": ["休闲鞋", "跑步鞋", "老爹鞋", "拖鞋"],
    },
    "优衣库": {
        "allowed_categories": ["服饰内衣"],
        "series_words": ["HEATTECH", "AIRism", "UT", "U系列", "轻羽绒"],
        "product_words": ["T恤", "衬衫", "羽绒服", "休闲裤", "家居服"],
    },
    "ZARA": {
        "allowed_categories": ["服饰内衣", "鞋靴箱包"],
        "series_words": ["基础款", "都市系列", "TRF", "秋冬新款"],
        "product_words": ["连衣裙", "外套", "衬衫", "牛仔裤", "手提包"],
    },
    "波司登": {
        "allowed_categories": ["服饰内衣"],
        "series_words": ["极寒", "轻薄", "城市", "高端户外"],
        "product_words": ["羽绒服", "轻羽绒马甲", "冲锋衣", "保暖内衣"],
    },
    "海澜之家": {
        "allowed_categories": ["服饰内衣"],
        "series_words": ["商务休闲", "轻商务", "国民精选", "简约基础"],
        "product_words": ["衬衫", "夹克", "休闲裤", "西服", "针织衫"],
    },
    "太平鸟": {
        "allowed_categories": ["服饰内衣"],
        "series_words": ["乐町", "都市休闲", "时髦系列", "轻潮"],
        "product_words": ["连衣裙", "风衣", "卫衣", "牛仔裤", "半身裙"],
    },
    "欧莱雅": {
        "allowed_categories": ["美妆个护"],
        "series_words": ["复颜", "葡萄籽", "金致", "男士控油", "小蜜罐"],
        "product_words": ["面霜", "精华", "乳液", "洗面奶", "护发精油"],
    },
    "雅诗兰黛": {
        "allowed_categories": ["美妆个护"],
        "series_words": ["小棕瓶", "白金", "智妍", "沁水", "DW"],
        "product_words": ["精华", "面霜", "粉底液", "眼霜", "护肤套装"],
    },
    "兰蔻": {
        "allowed_categories": ["美妆个护"],
        "series_words": ["小黑瓶", "菁纯", "极光", "持妆", "塑颜"],
        "product_words": ["精华", "面霜", "粉底液", "口红", "护肤礼盒"],
    },
    "香奈儿": {
        "allowed_categories": ["美妆个护"],
        "series_words": ["可可小姐", "山茶花", "奢华精萃", "炫亮", "蔚蓝"],
        "product_words": ["香水", "口红", "粉底液", "面霜", "彩妆礼盒"],
    },
    "SK-II": {
        "allowed_categories": ["美妆个护"],
        "series_words": ["神仙水", "大红瓶", "小灯泡", "前男友面膜"],
        "product_words": ["精华水", "面霜", "精华液", "面膜"],
    },
    "资生堂": {
        "allowed_categories": ["美妆个护"],
        "series_words": ["红腰子", "悦薇", "时光琉璃", "安热沙"],
        "product_words": ["精华", "面霜", "防晒", "眼霜", "洁面"],
    },
    "三只松鼠": {
        "allowed_categories": ["食品饮料"],
        "series_words": ["每日坚果", "坚果礼盒", "零食大礼包", "肉食铺子"],
        "product_words": ["坚果礼盒", "零食组合", "肉脯", "饼干糕点", "果干"],
    },
    "良品铺子": {
        "allowed_categories": ["食品饮料"],
        "series_words": ["高端零食", "坚果礼", "休闲食刻", "卤味大师"],
        "product_words": ["坚果", "肉脯", "糕点", "零食礼盒", "果干"],
    },
    "百草味": {
        "allowed_categories": ["食品饮料"],
        "series_words": ["抱抱果", "坚果工坊", "零食盛宴", "风味肉食"],
        "product_words": ["坚果", "果干", "零食礼包", "山楂制品", "卤味"],
    },
    "农夫山泉": {
        "allowed_categories": ["食品饮料"],
        "series_words": ["东方树叶", "茶π", "水溶C100", "尖叫", "长白雪"],
        "product_words": ["矿泉水", "无糖茶", "果汁饮料", "功能饮料", "饮用水"],
    },
    "元气森林": {
        "allowed_categories": ["食品饮料"],
        "series_words": ["气泡水", "外星人", "乳茶", "燃茶"],
        "product_words": ["苏打气泡水", "功能饮料", "奶茶饮料", "无糖茶"],
    },
    "星巴克": {
        "allowed_categories": ["食品饮料"],
        "series_words": ["星选", "馥芮白", "美式", "拿铁", "咖啡豆"],
        "product_words": ["即饮咖啡", "咖啡豆", "咖啡胶囊", "杯具周边"],
    },
    "雀巢": {
        "allowed_categories": ["食品饮料", "母婴玩具"],
        "series_words": ["咖啡馆藏", "脆脆鲨", "能恩", "怡养"],
        "product_words": ["咖啡", "巧克力", "奶粉", "营养粉", "冲调饮品"],
    },
    "伊利": {
        "allowed_categories": ["食品饮料"],
        "series_words": ["金典", "安慕希", "舒化", "QQ星"],
        "product_words": ["纯牛奶", "酸奶", "儿童牛奶", "奶酪", "礼盒装"],
    },
    "蒙牛": {
        "allowed_categories": ["食品饮料"],
        "series_words": ["特仑苏", "纯甄", "真果粒", "未来星"],
        "product_words": ["纯牛奶", "酸奶", "儿童奶", "早餐奶", "礼盒装"],
    },
    "惠氏": {
        "allowed_categories": ["母婴玩具"],
        "series_words": ["启赋", "铂臻", "蓝钻", "S-26"],
        "product_words": ["婴儿奶粉", "儿童奶粉", "孕产营养品"],
    },
    "飞鹤": {
        "allowed_categories": ["母婴玩具"],
        "series_words": ["星飞帆", "臻稚", "卓睿", "茁然"],
        "product_words": ["婴儿奶粉", "儿童奶粉", "孕产营养品"],
    },
    "a2": {
        "allowed_categories": ["母婴玩具"],
        "series_words": ["至初", "紫白金", "成长营养"],
        "product_words": ["婴儿奶粉", "儿童奶粉", "成人奶粉"],
    },
    "乐高": {
        "allowed_categories": ["母婴玩具"],
        "series_words": ["城市组", "机械组", "得宝", "星球大战", "幻影忍者"],
        "product_words": ["积木玩具", "拼搭套装", "儿童玩具", "收藏模型"],
    },
    "费雪": {
        "allowed_categories": ["母婴玩具"],
        "series_words": ["声光安抚", "益智启蒙", "成长伙伴", "感统训练"],
        "product_words": ["早教玩具", "安抚玩具", "学步玩具", "益智玩具"],
    },
    "好孩子": {
        "allowed_categories": ["母婴玩具"],
        "series_words": ["口袋车", "云感", "成长家", "护脊"],
        "product_words": ["婴儿推车", "安全座椅", "婴儿床", "餐椅"],
    },
    "十月结晶": {
        "allowed_categories": ["母婴玩具"],
        "series_words": ["待产安心", "棉柔呵护", "产后护理"],
        "product_words": ["待产包", "产褥垫", "吸奶器", "防溢乳垫"],
    },
    "全棉时代": {
        "allowed_categories": ["母婴玩具", "美妆个护"],
        "series_words": ["奈丝公主", "纯棉柔巾", "婴童呵护", "居家棉品"],
        "product_words": ["棉柔巾", "湿巾", "婴儿服饰", "卫生巾", "家居服"],
    },
    "宜家": {
        "allowed_categories": ["家居家装"],
        "series_words": ["毕利", "卡莱克", "汉尼斯", "玛尔姆", "瓦瑞拉"],
        "product_words": ["书柜", "置物架", "餐桌", "收纳盒", "灯具"],
    },
    "九阳": {
        "allowed_categories": ["家用电器", "家居家装"],
        "series_words": ["不用手洗", "太空科技", "轻养", "破壁王"],
        "product_words": ["豆浆机", "破壁机", "空气炸锅", "电饭煲", "电热水壶"],
    },
    "苏泊尔": {
        "allowed_categories": ["家用电器", "家居家装"],
        "series_words": ["球釜", "火红点", "远红外", "轻养"],
        "product_words": ["电饭煲", "炒锅", "电压力锅", "保温杯", "刀具套装"],
    },
    "膳魔师": {
        "allowed_categories": ["家居家装", "母婴玩具"],
        "series_words": ["保温随行", "儿童吸管", "焖烧", "真空保温"],
        "product_words": ["保温杯", "焖烧杯", "儿童水杯", "饭盒"],
    },
    "特步": {
        "allowed_categories": ["运动户外", "鞋靴箱包", "服饰内衣"],
        "series_words": ["260X", "动力巢", "柔立方", "风火"],
        "product_words": ["跑步鞋", "运动服", "训练裤", "运动外套"],
    },
    "New Balance": {
        "allowed_categories": ["运动户外", "鞋靴箱包"],
        "series_words": ["574", "327", "9060", "Fresh Foam"],
        "product_words": ["休闲鞋", "跑步鞋", "老爹鞋", "运动短袖"],
    },
}

