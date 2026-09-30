"""Doris 客户端管理。"""

import asyncio
import hashlib
from contextlib import AsyncExitStack
from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import create_async_engine

from app.shared.config.app_config import DBConfig


class DorisClientManager:
    """Doris 客户端管理器。"""

    def __init__(self, db_config: DBConfig) -> None:
        """创建 Doris 连接池；调用方通过 engine.connect() 获取连接。"""
        self.engine = create_async_engine(
            URL.create(
                drivername="mysql+asyncmy",
                username=db_config.user,
                password=db_config.password.get_secret_value(),
                host=db_config.host,
                port=db_config.port,
                database=db_config.database,
            ),
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=30,
        )

    async def close(self) -> None:
        """释放 Doris 连接池。"""
        await self.engine.dispose()


class DorisQueryClientRegistry:
    """按数据库中的稳定查询身份动态管理 Doris 连接池。"""

    def __init__(self, endpoint: DBConfig) -> None:
        """初始化查询端点和按角色隔离的连接池注册表。"""
        self._endpoint = endpoint
        self._entries: dict[str, _QueryClientEntry] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        role_name: str,
        query_user: str,
        password: str,
    ) -> DorisClientManager:
        """读取或创建与当前查询凭据一致的连接池。"""
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
            self._entries[role_name] = _QueryClientEntry(fingerprint, manager)
        if stale is not None:
            await stale.close()
        return manager

    async def invalidate(self, role_name: str) -> None:
        """关闭并移除指定角色的查询连接池。"""
        async with self._lock:
            entry = self._entries.pop(role_name, None)
        if entry is not None:
            await entry.manager.close()

    async def close(self) -> None:
        """关闭全部查询身份连接池。"""
        async with self._lock:
            entries = tuple(self._entries.values())
            self._entries.clear()
        async with AsyncExitStack() as stack:
            for entry in entries:
                stack.push_async_callback(entry.manager.close)


@dataclass(frozen=True, slots=True)
class _QueryClientEntry:
    """记录查询连接池的凭据指纹和客户端实例。"""

    fingerprint: str
    manager: DorisClientManager
