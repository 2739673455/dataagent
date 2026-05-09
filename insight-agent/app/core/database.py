"""数据库管理 — 引擎、会话工厂、FastAPI 依赖、上下文管理器"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import MySQLCfg, cfg

ENGINE_KWARGS_MAP: dict[str, dict[str, object]] = {
    "mysql": {
        "echo": False,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_timeout": 30,
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


def _get_db_url(db_cfg: MySQLCfg, db_driver: str, async_mode: bool = True) -> str:
    """获取数据库连接 URL"""
    if db_driver == "mysql":
        if not isinstance(db_cfg, MySQLCfg):
            raise TypeError("MySQL 配置错误")
        driver = "mysql+asyncmy" if async_mode else "mysql+pymysql"
        return (
            f"{driver}://{db_cfg.user}:{db_cfg.password}@"
            f"{db_cfg.host}:{db_cfg.port}/{db_cfg.database}"
        )
    raise ValueError(f"不支持的数据库驱动: {db_driver}")


_db_manager = DatabaseManager()

_db_cfg = cfg.db.configs[cfg.db.driver]
_db_driver = cfg.db.driver
_db_url = _get_db_url(_db_cfg, _db_driver)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖 — 请求级数据库会话，自动关闭"""
    async with _db_manager.session(_db_url, _db_driver) as db_session:
        yield db_session


def get_db_session():
    """获取数据库会话上下文 — 用于后台任务等非请求场景"""
    return _db_manager.session(_db_url, _db_driver)


async def close_db() -> None:
    """关闭所有数据库引擎"""
    await _db_manager.close_all()
