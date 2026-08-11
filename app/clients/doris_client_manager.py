"""Doris 客户端管理"""

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.conf.app_config import DBConfig, cfg


class DorisClientManager:
    """Doris 客户端管理器"""

    def __init__(self, db_config: DBConfig) -> None:
        """初始化 Doris 客户端管理器"""
        self._db_config = db_config
        self._engine: AsyncEngine | None = None

    @property
    def _url(self) -> URL:
        """获取 Doris 异步连接 URL"""
        return URL.create(
            drivername="mysql+asyncmy",
            username=self._db_config.user,
            password=self._db_config.password,
            host=self._db_config.host,
            port=self._db_config.port,
            database=self._db_config.database,
        )

    def init(self) -> None:
        """初始化 Doris 连接池"""
        self._engine = create_async_engine(
            self._url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=30,
        )

    def _get_engine(self) -> AsyncEngine:
        """获取 Doris 数据库引擎"""
        if self._engine is None:
            raise RuntimeError("Doris client manager is not initialized")
        return self._engine

    def connection(self) -> AsyncConnection:
        """创建 Doris 数据库连接"""
        return self._get_engine().connect()

    async def close(self) -> None:
        """关闭 Doris 连接池并释放资源"""
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None


source_doris_client_manager = DorisClientManager(cfg.doris)
