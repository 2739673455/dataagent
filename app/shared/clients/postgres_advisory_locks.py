"""使用专用连接池持有跨进程业务咨询锁。"""

import asyncio
import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.shared.config.app_config import DBConfig
from app.shared.errors.infrastructure import AdvisoryLockBusyError


def _advisory_lock_key(name: str) -> int:
    """把业务锁名称稳定映射为 PostgreSQL bigint。"""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class PostgresAdvisoryLocks:
    """业务锁使用独立连接池，不占用 Checkpoint 连接。"""

    def __init__(self, db_config: DBConfig) -> None:
        """构造专用连接池，并保留同进程非重入检查。"""
        self._pool = AsyncConnectionPool[AsyncConnection[DictRow]](
            conninfo=make_conninfo(
                host=db_config.host,
                port=db_config.port,
                user=db_config.user,
                password=db_config.password.get_secret_value(),
                dbname=db_config.database,
            ),
            min_size=1,
            max_size=12,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        self._advisory_locks: dict[str, asyncio.Lock] = {}

    async def init(self) -> None:
        """打开业务锁连接池；所有者负责在失败或退出时关闭。"""
        await self._pool.open(wait=True)

    async def close(self) -> None:
        """在业务任务停止后释放连接池。"""
        await self._pool.close()
        self._advisory_locks.clear()

    @asynccontextmanager
    async def advisory_lock(
        self,
        name: str,
    ) -> AsyncGenerator[None]:
        """非阻塞获取连接级 PostgreSQL advisory lock。"""
        if not name:
            raise ValueError("咨询锁名称不能为空")

        lock_key = _advisory_lock_key(name)
        advisory_pool = self._pool
        local_lock = self._advisory_locks.setdefault(name, asyncio.Lock())
        if local_lock.locked():
            raise AdvisoryLockBusyError(f"咨询锁正在使用: {name}")
        await local_lock.acquire()
        try:
            async with advisory_pool.connection() as connection:
                # PostgreSQL advisory lock 绑定数据库连接；必须在同一专用连接上持锁
                # 到调用方退出，并在归还连接池前显式解锁。
                cursor = await connection.execute(
                    "SELECT pg_try_advisory_lock(%s) AS acquired",
                    (lock_key,),
                )
                row = await cursor.fetchone()
                if row is None or not bool(row["acquired"]):
                    raise AdvisoryLockBusyError(f"咨询锁正在使用: {name}")
                try:
                    yield
                finally:
                    await connection.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        (lock_key,),
                    )
        finally:
            local_lock.release()
