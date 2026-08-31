"""Doris 客户端管理"""

import asyncio
import hashlib
from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.shared.config.app_config import DBConfig, cfg


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
            password=self._db_config.password.get_secret_value(),
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
            raise RuntimeError("Doris 客户端管理器尚未初始化")
        return self._engine

    def connection(self) -> AsyncConnection:
        """创建 Doris 数据库连接"""
        return self._get_engine().connect()

    async def close(self) -> None:
        """关闭 Doris 连接池并释放资源"""
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None


class DorisQueryClientRegistry:
    """按数据库中的稳定查询身份动态管理 Doris 连接池"""

    def __init__(self, endpoint: DBConfig) -> None:
        """初始化查询端点和按角色隔离的连接池注册表"""
        self._endpoint = endpoint
        self._entries: dict[str, _QueryClientEntry] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        role_name: str,
        query_user: str,
        password: str,
    ) -> DorisClientManager:
        """读取或创建与当前查询凭据一致的连接池"""
        fingerprint = hashlib.sha256(f"{query_user}\0{password}".encode()).hexdigest()
        stale: DorisClientManager | None = None
        async with self._lock:
            current = self._entries.get(role_name)
            if current is not None and current.fingerprint == fingerprint:
                return current.manager
            if current is not None:
                stale = current.manager
            manager = DorisClientManager(
                DBConfig(
                    host=self._endpoint.host,
                    port=self._endpoint.port,
                    user=query_user,
                    password=SecretStr(password),
                    database=self._endpoint.database,
                )
            )
            manager.init()
            self._entries[role_name] = _QueryClientEntry(fingerprint, manager)
        if stale is not None:
            await stale.close()
        return manager

    async def invalidate(self, role_name: str) -> None:
        """关闭并移除指定角色的查询连接池"""
        async with self._lock:
            entry = self._entries.pop(role_name, None)
        if entry is not None:
            await entry.manager.close()

    async def close(self) -> None:
        """关闭全部查询身份连接池"""
        async with self._lock:
            entries = tuple(self._entries.values())
            self._entries.clear()
        for entry in entries:
            await entry.manager.close()


@dataclass(frozen=True, slots=True)
class _QueryClientEntry:
    """记录查询连接池的凭据指纹和客户端实例"""

    fingerprint: str
    manager: DorisClientManager


admin_doris_client_manager = DorisClientManager(cfg.doris)
query_doris_client_registry = DorisQueryClientRegistry(cfg.doris)
