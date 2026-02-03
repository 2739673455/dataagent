from typing import AsyncGenerator

from app.config import CFG
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class DatabaseManager:
    """数据库管理器"""

    def __init__(self):
        self.engines = {}
        self.session_makers = {}

    def get_engine(self, name: str):
        """获取或创建数据库引擎"""
        if name not in self.engines:
            cfg = getattr(CFG.db, name)
            db_url = f"mysql+asyncmy://{cfg.user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.database}"
            self.engines[name] = create_async_engine(
                db_url,
                echo=False,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=1800,
                pool_timeout=30,
            )
        return self.engines[name]

    def get_session_maker(self, name: str):
        """获取或创建会话工厂"""
        if name not in self.session_makers:
            engine = self.get_engine(name)
            self.session_makers[name] = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
        return self.session_makers[name]

    def get_db(self, name: str):
        """获取数据库会话依赖"""

        async def _get_db() -> AsyncGenerator[AsyncSession, None]:
            session_maker = self.get_session_maker(name)
            async with session_maker() as db_session:
                try:
                    yield db_session
                finally:
                    await db_session.close()

        return _get_db

    async def close_all(self):
        """关闭所有数据库引擎"""
        for engine in self.engines.values():
            await engine.dispose()


db_manager = DatabaseManager()

get_auth_db = db_manager.get_db("auth")
get_app_db = db_manager.get_db("app")
