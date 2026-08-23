"""LangGraph PostgreSQL 持久化客户端管理"""

import asyncio
import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.shared.config.app_config import DBConfig, cfg

_ADVISORY_LOCK_POLL_SECONDS = 0.05
_ADVISORY_POOL_MAX_SIZE = 12


def _advisory_lock_key(name: str) -> int:
    """把业务锁名称稳定映射为 PostgreSQL bigint"""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class LangGraphPostgresManager:
    """LangGraph PostgreSQL Checkpointer 和 Store 生命周期管理器"""

    def __init__(self, db_config: DBConfig) -> None:
        """初始化 PostgreSQL 持久化配置"""
        self._db_config = db_config
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._advisory_pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._checkpointer: AsyncPostgresSaver | None = None
        self._store: AsyncPostgresStore | None = None
        self._advisory_locks: dict[str, asyncio.Lock] = {}

    @property
    def _conninfo(self) -> str:
        """构造 PostgreSQL 连接信息"""
        return make_conninfo(
            host=self._db_config.host,
            port=self._db_config.port,
            user=self._db_config.user,
            password=self._db_config.password,
            dbname=self._db_config.database,
        )

    async def init(self) -> None:
        """初始化连接池和 LangGraph 持久化组件"""
        pool = AsyncConnectionPool[AsyncConnection[DictRow]](
            conninfo=self._conninfo,
            min_size=1,
            max_size=20,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        advisory_pool = AsyncConnectionPool[AsyncConnection[DictRow]](
            conninfo=self._conninfo,
            min_size=1,
            max_size=_ADVISORY_POOL_MAX_SIZE,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        try:
            await pool.open(wait=True)
            await advisory_pool.open(wait=True)
        except Exception:
            await advisory_pool.close()
            await pool.close()
            raise

        checkpointer = AsyncPostgresSaver(pool)
        store = AsyncPostgresStore(pool)
        try:
            await checkpointer.setup()
            await store.setup()
        except Exception:
            await advisory_pool.close()
            await pool.close()
            raise

        self._pool = pool
        self._advisory_pool = advisory_pool
        self._checkpointer = checkpointer
        self._store = store

    def get_checkpointer(self) -> AsyncPostgresSaver:
        """获取已初始化的 Checkpointer"""
        if self._checkpointer is None:
            raise RuntimeError("LangGraph PostgreSQL 管理器尚未初始化")
        return self._checkpointer

    def get_store(self) -> AsyncPostgresStore:
        """获取已初始化的 Store"""
        if self._store is None:
            raise RuntimeError("LangGraph PostgreSQL 管理器尚未初始化")
        return self._store

    @asynccontextmanager
    async def advisory_lock(
        self,
        name: str,
        *,
        timeout: float,
    ) -> AsyncGenerator[None, None]:
        """在连接级 PostgreSQL advisory lock 下执行临界区"""
        if not name:
            raise ValueError("咨询锁名称不能为空")
        if timeout <= 0:
            raise ValueError("咨询锁超时时间必须为正数")
        if self._advisory_pool is None:
            raise RuntimeError("LangGraph PostgreSQL 管理器尚未初始化")

        lock_key = _advisory_lock_key(name)
        advisory_pool = self._advisory_pool
        deadline = asyncio.get_running_loop().time() + timeout
        local_lock = self._advisory_locks.setdefault(name, asyncio.Lock())
        try:
            await asyncio.wait_for(local_lock.acquire(), timeout=timeout)
        except TimeoutError as exc:
            raise TimeoutError(f"获取咨询锁超时: {name}") from exc
        try:
            while True:
                async with advisory_pool.connection() as connection:
                    cursor = await connection.execute(
                        "SELECT pg_try_advisory_lock(%s) AS acquired",
                        (lock_key,),
                    )
                    row = await cursor.fetchone()
                    if row is not None and bool(row["acquired"]):
                        try:
                            yield
                        finally:
                            await connection.execute(
                                "SELECT pg_advisory_unlock(%s)",
                                (lock_key,),
                            )
                        return
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"获取咨询锁超时: {name}")
                await asyncio.sleep(min(_ADVISORY_LOCK_POLL_SECONDS, remaining))
        finally:
            local_lock.release()

    async def delete_thread(self, thread_id: str) -> None:
        """删除会话线程的全部 Checkpoint"""
        await self.get_checkpointer().adelete_thread(thread_id)

    async def delete_user_threads(self, user_id: int) -> None:
        """删除用户全部 LangGraph Checkpoint 线程"""
        thread_prefix = f"user_{user_id}:conversation_"
        thread_ids: set[str] = set()
        async for checkpoint in self.get_checkpointer().alist(None):
            configurable = checkpoint.config.get("configurable")
            if not isinstance(configurable, dict):
                continue
            thread_id = configurable.get("thread_id")
            if isinstance(thread_id, str) and thread_id.startswith(thread_prefix):
                thread_ids.add(thread_id)
        for thread_id in thread_ids:
            await self.delete_thread(thread_id)

    async def close(self) -> None:
        """关闭连接池并释放持久化组件"""
        if self._advisory_pool is not None:
            await self._advisory_pool.close()
        if self._pool is not None:
            await self._pool.close()
        self._advisory_pool = None
        self._pool = None
        self._checkpointer = None
        self._store = None
        self._advisory_locks.clear()


langgraph_postgres_manager = LangGraphPostgresManager(cfg.langgraph_postgresql)
