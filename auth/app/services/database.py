from typing import AsyncGenerator

from app.config import CFG
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 存储数据库引擎的字典，键为数据库名称
ENGINES = {}
# 存储会话工厂的字典，键为数据库名称
SESSION_MAKERS = {}


def get_engine(name: str):
    """获取或创建数据库引擎"""
    if name not in ENGINES:
        # 从配置中获取指定数据库的配置
        cfg = getattr(CFG.db, name)
        # 构建数据库连接URL
        db_url = f"mysql+asyncmy://{cfg.user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.database}"
        # 创建异步数据库引擎
        ENGINES[name] = create_async_engine(
            db_url,
            echo=False,  # 不打印SQL语句
            pool_size=10,  # 连接池大小
            max_overflow=20,  # 连接池最大溢出连接数
            pool_pre_ping=True,  # 连接前检查连接是否有效
            pool_recycle=1800,  # 连接回收时间（秒）
            pool_timeout=30,  # 获取连接超时时间（秒）
        )
    return ENGINES[name]


def get_session_maker(name: str):
    """获取或创建会话工厂"""
    if name not in SESSION_MAKERS:
        engine = get_engine(name)
        # 创建异步会话工厂
        SESSION_MAKERS[name] = async_sessionmaker(
            engine,
            class_=AsyncSession,  # 使用异步会话类
            expire_on_commit=False,  # 提交后不立即过期对象
        )
    return SESSION_MAKERS[name]


def get_db(name: str):
    """获取数据库会话依赖函数"""

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        session_maker = get_session_maker(name)
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


# 创建认证数据库的依赖函数
get_auth_db = get_db("auth")
