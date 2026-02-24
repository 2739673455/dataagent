from typing import AsyncGenerator

from app.config import CFG, MySQLCfg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ENGINES = {}  # 存储数据库引擎的字典，键为数据库名称
SESSION_MAKERS = {}  # 存储会话工厂的字典，键为数据库名称
ENGINE_KWARGS_MAP = {
    "mysql": {
        "echo": False,  # 不打印SQL语句
        "pool_size": 10,  # 连接池大小
        "max_overflow": 20,  # 连接池最大溢出连接数
        "pool_pre_ping": True,  # 连接前检查连接是否有效
        "pool_recycle": 1800,  # 连接回收时间（秒）
        "pool_timeout": 30,  # 获取连接超时时间（秒）
    }
}  # 数据库引擎配置


def _get_db_url(cfg, db_driver: str):
    """获取数据库连接 url"""
    match db_driver:
        case "mysql":
            assert isinstance(cfg, MySQLCfg), "MySQL 配置错误"
            return f"mysql+asyncmy://{cfg.user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.database}"
        case _:
            raise ValueError(f"不支持的数据库驱动: {db_driver}")


def _get_engine(name: str, db_url: str, db_driver: str):
    """获取或创建数据库引擎"""
    if name not in ENGINES:
        ENGINES[name] = create_async_engine(db_url, **ENGINE_KWARGS_MAP[db_driver])
    return ENGINES[name]


def _get_session_maker(name: str, db_url: str, db_driver: str):
    """获取或创建会话工厂"""
    if name not in SESSION_MAKERS:
        engine = _get_engine(name, db_url, db_driver)
        # 创建异步会话工厂
        SESSION_MAKERS[name] = async_sessionmaker(
            engine,
            class_=AsyncSession,  # 使用异步会话类
            expire_on_commit=False,  # 提交后不立即过期对象
        )
    return SESSION_MAKERS[name]


def get_db(name: str, db_url: str, db_driver: str):
    """获取数据库会话依赖函数"""

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        session_maker = _get_session_maker(name, db_url, db_driver)
        # 创建数据库会话上下文管理器
        async with session_maker() as db_session:
            try:
                # 向调用方yield会话
                yield db_session
            finally:
                # 确保会话被正确关闭
                await db_session.close()

    return _get_db


async def close_all():
    """关闭所有数据库引擎"""
    for engine in ENGINES.values():
        await engine.dispose()
    ENGINES.clear()
    SESSION_MAKERS.clear()


# 创建认证数据库的依赖函数
get_auth_db = get_db(
    "auth",
    _get_db_url(CFG.db.configs[CFG.db.driver], CFG.db.driver),
    CFG.db.driver,
)
