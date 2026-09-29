"""Doris 客户端管理。"""

from contextlib import AsyncExitStack

from pydantic import SecretStr
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.shared.config.app_config import DBConfig


class DorisClientManager:
    """Doris 客户端管理器。"""

    def __init__(self, db_config: DBConfig) -> None:
        """初始化 Doris 客户端管理器。"""
        self._db_config = db_config
        self._engine: AsyncEngine | None = None

    @property
    def _url(self) -> URL:
        """获取 Doris 异步连接 URL。"""
        return URL.create(
            drivername="mysql+asyncmy",
            username=self._db_config.user,
            password=self._db_config.password.get_secret_value(),
            host=self._db_config.host,
            port=self._db_config.port,
            database=self._db_config.database,
        )

    def init(self) -> None:
        """初始化 Doris 连接池。"""
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
        """获取 Doris 数据库引擎。"""
        if self._engine is None:
            raise RuntimeError("Doris 客户端管理器尚未初始化")
        return self._engine

    def connection(self) -> AsyncConnection:
        """创建 Doris 数据库连接。"""
        return self._get_engine().connect()

    async def close(self) -> None:
        """关闭 Doris 连接池并释放资源。"""
        resource = self._engine
        self._engine = None
        if resource is not None:
            await resource.dispose()


class DorisQueryClientRegistry:
    """按预定义角色缓存查询连接池，凭据变更后需重启应用。"""

    def __init__(self, endpoint: DBConfig) -> None:
        self._endpoint = endpoint
        self._clients: dict[str, DorisClientManager] = {}

    def get_or_create(
        self,
        role_name: str,
        query_user: str,
        password: str,
    ) -> DorisClientManager:
        """在当前事件循环中读取或同步创建角色专属连接池。"""
        if role_name not in self._clients:
            manager = DorisClientManager(
                self._endpoint.model_copy(
                    update={"user": query_user, "password": SecretStr(password)}
                )
            )
            manager.init()
            self._clients[role_name] = manager
        return self._clients[role_name]

    async def close(self) -> None:
        """关闭全部查询身份连接池。"""
        clients = tuple(self._clients.values())
        self._clients.clear()
        async with AsyncExitStack() as stack:
            for client in clients:
                stack.push_async_callback(client.close)
