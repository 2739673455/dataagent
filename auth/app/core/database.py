from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import SQLiteCfg

DBSessionContextFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

ENGINE_KWARGS_MAP: dict[str, dict[str, object]] = {
    "sqlite": {
        "echo": False,
    },
}


class DatabaseManager:
    """数据库管理器"""

    def __init__(self) -> None:
        self._engines: dict[str, AsyncEngine] = {}
        self._session_makers: dict[str, async_sessionmaker[AsyncSession]] = {}

    def _get_engine(self, db_url: str, db_driver: str) -> AsyncEngine:
        """获取或创建数据库引擎"""
        if db_url not in self._engines:
            self._engines[db_url] = create_async_engine(
                db_url,
                **ENGINE_KWARGS_MAP[db_driver],
            )
        return self._engines[db_url]

    def _get_session_maker(self, db_url: str, db_driver: str) -> async_sessionmaker[AsyncSession]:
        """获取或创建会话工厂"""
        if db_url not in self._session_makers:
            engine = self._get_engine(db_url, db_driver)
            self._session_makers[db_url] = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        return self._session_makers[db_url]

    @asynccontextmanager
    async def session(self, db_url: str, db_driver: str) -> AsyncIterator[AsyncSession]:
        """创建数据库会话上下文"""
        session_maker = self._get_session_maker(db_url, db_driver)
        async with session_maker() as db_session:
            yield db_session

    async def close_all(self) -> None:
        """关闭所有数据库引擎"""
        for engine in self._engines.values():
            await engine.dispose()
        self._engines.clear()
        self._session_makers.clear()


def _get_db_url(db_cfg: SQLiteCfg, db_driver: str, async_mode: bool = True) -> str:
    if db_driver == "sqlite":
        if not isinstance(db_cfg, SQLiteCfg):
            raise TypeError("SQLite 配置错误")
        driver = "sqlite+aiosqlite" if async_mode else "sqlite"
        return f"{driver}:///{db_cfg.file_path}"

    raise ValueError(f"不支持的数据库驱动: {db_driver}")


_db_manager = DatabaseManager()


def get_db_session_context(
    db_cfg: SQLiteCfg,
    db_driver: str,
    async_mode: bool = True,
) -> AbstractAsyncContextManager[AsyncSession]:
    """获取数据库会话上下文"""
    return _db_manager.session(_get_db_url(db_cfg, db_driver, async_mode), db_driver)


async def close_db() -> None:
    """关闭所有数据库引擎"""
    await _db_manager.close_all()
