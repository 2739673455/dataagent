from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, AsyncIterator

from app.core.settings import MySQLCfg, SQLiteCfg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DBSessionContextFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

ENGINE_KWARGS_MAP = {
    "mysql": {
        "echo": False,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_timeout": 30,
    },
    "sqlite": {
        "echo": False,
    },
}


class DatabaseManager:
    """数据库管理器"""

    def __init__(self) -> None:
        self.engines: dict[str, Any] = {}
        self.session_makers: dict[str, async_sessionmaker[AsyncSession]] = {}

    def get_engine(self, db_url: str, db_driver: str) -> Any:
        """获取或创建数据库引擎"""
        if db_url not in self.engines:
            self.engines[db_url] = create_async_engine(
                db_url,
                **ENGINE_KWARGS_MAP[db_driver],
            )
        return self.engines[db_url]

    def get_session_maker(
        self,
        db_url: str,
        db_driver: str,
    ) -> async_sessionmaker[AsyncSession]:
        """获取或创建会话工厂"""
        if db_url not in self.session_makers:
            engine = self.get_engine(db_url, db_driver)
            self.session_makers[db_url] = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        return self.session_makers[db_url]

    @asynccontextmanager
    async def session(
        self,
        db_url: str,
        db_driver: str,
    ) -> AsyncIterator[AsyncSession]:
        """创建数据库会话"""
        session_maker = self.get_session_maker(db_url, db_driver)
        async with session_maker() as db_session:
            yield db_session

    async def close_all(self) -> None:
        """关闭所有数据库引擎"""
        for engine in self.engines.values():
            await engine.dispose()
        self.engines.clear()
        self.session_makers.clear()


def get_db_url(
    db_cfg: SQLiteCfg | MySQLCfg, db_driver: str, async_mode: bool = True
) -> str:
    """获取数据库连接 url"""
    if db_driver == "mysql":
        if not isinstance(db_cfg, MySQLCfg):
            raise TypeError("MySQL 配置错误")
        driver = "mysql+asyncmy" if async_mode else "mysql+pymysql"
        return (
            f"{driver}://{db_cfg.user}:{db_cfg.password}@"
            f"{db_cfg.host}:{db_cfg.port}/{db_cfg.database}"
        )

    if db_driver == "sqlite":
        if not isinstance(db_cfg, SQLiteCfg):
            raise TypeError("SQLite 配置错误")
        driver = "sqlite+aiosqlite" if async_mode else "sqlite"
        return f"{driver}:///{db_cfg.file_path}"

    raise ValueError(f"不支持的数据库驱动: {db_driver}")


db_manager = DatabaseManager()


def get_db_session_context(
    db_cfg: SQLiteCfg | MySQLCfg,
    db_driver: str,
    async_mode: bool = True,
) -> AbstractAsyncContextManager[AsyncSession]:
    """获取数据库会话上下文"""
    return db_manager.session(
        get_db_url(db_cfg, db_driver, async_mode),
        db_driver,
    )


async def close_db() -> None:
    """关闭所有数据库引擎"""
    await db_manager.close_all()
