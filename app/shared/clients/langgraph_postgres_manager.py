"""LangGraph PostgreSQL 持久化客户端管理"""

import asyncio
import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.shared.config.app_config import DBConfig, cfg

_ADVISORY_POOL_MAX_SIZE = 12


def _advisory_lock_key(name: str) -> int:
    """把业务锁名称稳定映射为 PostgreSQL bigint"""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class LangGraphPostgresManager:
    """LangGraph PostgreSQL Checkpointer 和咨询锁生命周期管理器"""

    def __init__(self, db_config: DBConfig) -> None:
        """初始化 PostgreSQL 持久化配置"""
        self._db_config = db_config
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._advisory_pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._checkpointer: AsyncPostgresSaver | None = None
        self._advisory_locks: dict[str, asyncio.Lock] = {}

    @property
    def _conninfo(self) -> str:
        """构造 PostgreSQL 连接信息"""
        return make_conninfo(
            host=self._db_config.host,
            port=self._db_config.port,
            user=self._db_config.user,
            password=self._db_config.password.get_secret_value(),
            dbname=self._db_config.database,
        )

    async def init(self) -> None:
        """初始化连接池和 LangGraph Checkpointer"""
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
        try:
            await checkpointer.setup()
        except Exception:
            await advisory_pool.close()
            await pool.close()
            raise

        self._pool = pool
        self._advisory_pool = advisory_pool
        self._checkpointer = checkpointer

    def get_checkpointer(self) -> AsyncPostgresSaver:
        """获取已初始化的 Checkpointer"""
        if self._checkpointer is None:
            raise RuntimeError("LangGraph PostgreSQL 管理器尚未初始化")
        return self._checkpointer

    @asynccontextmanager
    async def advisory_lock(
        self,
        name: str,
    ) -> AsyncGenerator[None, None]:
        """非阻塞获取连接级 PostgreSQL advisory lock"""
        if not name:
            raise ValueError("咨询锁名称不能为空")
        if self._advisory_pool is None:
            raise RuntimeError("LangGraph PostgreSQL 管理器尚未初始化")

        lock_key = _advisory_lock_key(name)
        advisory_pool = self._advisory_pool
        local_lock = self._advisory_locks.setdefault(name, asyncio.Lock())
        if local_lock.locked():
            raise RuntimeError(f"咨询锁正在使用: {name}")
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
                    raise RuntimeError(f"咨询锁正在使用: {name}")
                try:
                    yield
                finally:
                    await connection.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        (lock_key,),
                    )
        finally:
            local_lock.release()

    async def delete_thread(self, thread_id: str) -> None:
        """删除会话线程的全部 Checkpoint"""
        await self.get_checkpointer().adelete_thread(thread_id)

    async def list_checkpoint_namespaces(
        self,
        thread_id: str,
        *,
        prefix: str,
    ) -> list[str]:
        """列出线程内具有指定前缀的唯一 Checkpoint namespace"""
        if not thread_id or not prefix:
            raise ValueError("thread_id 和 prefix 均不能为空")
        if self._pool is None:
            raise RuntimeError("LangGraph PostgreSQL 管理器尚未初始化")
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT DISTINCT checkpoint_ns
                FROM checkpoints
                WHERE thread_id = %s
                  AND left(checkpoint_ns, length(%s)) = %s
                ORDER BY checkpoint_ns
                """,
                (thread_id, prefix, prefix),
            )
            rows = await cursor.fetchall()
        return [str(row["checkpoint_ns"]) for row in rows]

    async def delete_checkpoint_namespace(
        self,
        thread_id: str,
        checkpoint_ns: str,
    ) -> bool:
        """原子删除线程内单个 namespace 的全部 Checkpoint 数据"""
        if not thread_id or not checkpoint_ns:
            raise ValueError("thread_id 和 checkpoint_ns 均不能为空")
        if self._pool is None:
            raise RuntimeError("LangGraph PostgreSQL 管理器尚未初始化")
        deleted = 0
        statements = (
            (
                "DELETE FROM checkpoint_writes "
                "WHERE thread_id = %s AND checkpoint_ns = %s"
            ),
            (
                "DELETE FROM checkpoint_blobs "
                "WHERE thread_id = %s AND checkpoint_ns = %s"
            ),
            ("DELETE FROM checkpoints WHERE thread_id = %s AND checkpoint_ns = %s"),
        )
        async with (
            self._pool.connection() as connection,
            connection.transaction(),
        ):
            for statement in statements:
                cursor = await connection.execute(
                    statement,
                    (thread_id, checkpoint_ns),
                )
                deleted += max(cursor.rowcount, 0)
        return deleted > 0

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
        self._advisory_locks.clear()


langgraph_postgres_manager = LangGraphPostgresManager(cfg.langgraph_postgresql)
