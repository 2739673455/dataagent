"""原生 Checkpointer 及专业 Session 所需的 namespace 数据操作。"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.shared.config.app_config import DBConfig


class PostgresCheckpointStore:
    """持有 Checkpoint 连接池，并提供原生 Saver 未覆盖的目录操作。"""

    def __init__(self, db_config: DBConfig) -> None:
        """构造尚未打开的连接池和原生 Saver。"""
        self._pool = AsyncConnectionPool[AsyncConnection[DictRow]](
            conninfo=make_conninfo(
                host=db_config.host,
                port=db_config.port,
                user=db_config.user,
                password=db_config.password.get_secret_value(),
                dbname=db_config.database,
            ),
            min_size=1,
            max_size=20,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        self.checkpointer = AsyncPostgresSaver(self._pool)

    async def init(self) -> None:
        """打开连接池并准备 Checkpoint 表；所有者负责失败及退出清理。"""
        await self._pool.open(wait=True)
        await self.checkpointer.setup()

    async def close(self) -> None:
        """在 Agent 与执行任务停止后关闭 Checkpoint 连接池。"""
        await self._pool.close()

    async def list_checkpoint_namespaces(
        self,
        thread_id: str,
        *,
        prefix: str,
    ) -> list[str]:
        """列出线程内具有指定前缀的唯一 Checkpoint namespace。"""
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
        """原子删除线程内单个 namespace 的全部 Checkpoint 数据。"""
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
        """删除用户全部 LangGraph Checkpoint 线程。"""
        prefix = f"user_{user_id}:conversation_"
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT DISTINCT thread_id
                FROM checkpoints
                WHERE left(thread_id, length(%s)) = %s
                ORDER BY thread_id
                """,
                (prefix, prefix),
            )
            rows = await cursor.fetchall()
        for row in rows:
            await self.checkpointer.adelete_thread(str(row["thread_id"]))
