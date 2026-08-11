"""LangGraph PostgreSQL 持久化客户端管理"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.conf.app_config import DBConfig, cfg


class LangGraphPostgresManager:
    """LangGraph PostgreSQL Checkpointer 和 Store 生命周期管理器"""

    def __init__(self, db_config: DBConfig) -> None:
        """初始化 PostgreSQL 持久化配置"""
        self._db_config = db_config
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._checkpointer: AsyncPostgresSaver | None = None
        self._store: AsyncPostgresStore | None = None

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
        await pool.open(wait=True)

        checkpointer = AsyncPostgresSaver(pool)
        store = AsyncPostgresStore(pool)
        try:
            await checkpointer.setup()
            await store.setup()
        except Exception:
            await pool.close()
            raise

        self._pool = pool
        self._checkpointer = checkpointer
        self._store = store

    def get_checkpointer(self) -> AsyncPostgresSaver:
        """获取已初始化的 Checkpointer"""
        if self._checkpointer is None:
            raise RuntimeError("LangGraph PostgreSQL manager is not initialized")
        return self._checkpointer

    def get_store(self) -> AsyncPostgresStore:
        """获取已初始化的 Store"""
        if self._store is None:
            raise RuntimeError("LangGraph PostgreSQL manager is not initialized")
        return self._store

    async def delete_thread(self, thread_id: str) -> None:
        """删除会话线程的全部 Checkpoint"""
        await self.get_checkpointer().adelete_thread(thread_id)

    async def close(self) -> None:
        """关闭连接池并释放持久化组件"""
        if self._pool is not None:
            await self._pool.close()
        self._pool = None
        self._checkpointer = None
        self._store = None


langgraph_postgres_manager = LangGraphPostgresManager(cfg.langgraph_postgresql)
