"""PostgreSQL 客户端管理"""

from collections.abc import AsyncIterator

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.shared.config.app_config import DBConfig, cfg
from app.shared.database.base import AuthBase, MetaBase


class PostgresClientManager:
    """PostgreSQL 客户端管理器"""

    def __init__(
        self,
        db_config: DBConfig,
        base: type[DeclarativeBase],
    ) -> None:
        """初始化 PostgreSQL 客户端管理器"""
        self._db_config = db_config
        self._base = base
        self._engine: AsyncEngine | None = None
        self._session_maker: async_sessionmaker[AsyncSession] | None = None

    @property
    def _url(self) -> URL:
        """获取异步数据库连接 URL"""
        return URL.create(
            drivername="postgresql+psycopg",
            username=self._db_config.user,
            password=self._db_config.password,
            host=self._db_config.host,
            port=self._db_config.port,
            database=self._db_config.database,
        )

    def init(self) -> None:
        """初始化数据库引擎和会话工厂"""
        self._engine = create_async_engine(
            self._url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=30,
        )
        self._session_maker = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    def _get_session_maker(self) -> async_sessionmaker[AsyncSession]:
        """获取数据库会话工厂"""
        if self._session_maker is None:
            raise RuntimeError("PostgreSQL 客户端管理器尚未初始化")
        return self._session_maker

    def session(self) -> AsyncSession:
        """创建数据库会话"""
        return self._get_session_maker()()

    async def get_session(self) -> AsyncIterator[AsyncSession]:
        """获取 FastAPI 请求级数据库会话"""
        async with self.session() as db_session:
            yield db_session

    async def close(self) -> None:
        """关闭数据库引擎并释放资源"""
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._session_maker = None

    async def init_tables(self) -> None:
        """初始化数据表"""
        if self._engine is None:
            raise RuntimeError("PostgreSQL 客户端管理器尚未初始化")
        async with self._engine.begin() as connection:
            await connection.run_sync(self._base.metadata.create_all)
            if self._base is AuthBase:
                await connection.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                        "auth_version INTEGER NOT NULL DEFAULT 0"
                    )
                )
            if self._base is MetaBase:
                await connection.execute(
                    text(
                        "ALTER TABLE table_info ADD COLUMN IF NOT EXISTS "
                        "value_index_sync JSONB NOT NULL DEFAULT "
                        "'{\"cursor_column\":null}'::jsonb"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE table_info ALTER COLUMN value_index_sync "
                        "SET DEFAULT "
                        "'{\"cursor_column\":null}'"
                    )
                )
                await connection.execute(
                    text(
                        "UPDATE table_info SET value_index_sync = "
                        "value_index_sync - 'mode' "
                        "- 'full_reconcile_interval_hours' "
                        "- 'lookback_seconds' "
                        "WHERE jsonb_exists(value_index_sync, 'mode') OR "
                        "jsonb_exists(value_index_sync, "
                        "'full_reconcile_interval_hours') OR "
                        "jsonb_exists(value_index_sync, 'lookback_seconds')"
                    )
                )
                await connection.execute(
                    text(
                        """
                        DO $migration$
                        BEGIN
                            IF EXISTS (
                                SELECT 1
                                FROM information_schema.columns
                                WHERE table_schema = current_schema()
                                  AND table_name = 'column_info'
                                  AND column_name = 'value_index_sync_status'
                            ) THEN
                                EXECUTE $sql$
                                    INSERT INTO value_index_sync_state (
                                        t_name,
                                        c_name,
                                        status,
                                        last_full_synced_at,
                                        updated_at
                                    )
                                    SELECT
                                        t_name,
                                        name,
                                        COALESCE(
                                            value_index_sync_status,
                                            'succeeded'
                                        ),
                                        value_index_synced_at,
                                        COALESCE(
                                            value_index_synced_at,
                                            CURRENT_TIMESTAMP
                                        )
                                    FROM column_info
                                    WHERE value_index_sync_status IS NOT NULL
                                       OR value_index_synced_at IS NOT NULL
                                    ON CONFLICT (t_name, c_name) DO NOTHING
                                $sql$;
                            END IF;
                        END
                        $migration$
                        """
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE column_info "
                        "DROP COLUMN IF EXISTS value_index_synced_at"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE column_info "
                        "DROP COLUMN IF EXISTS value_index_sync_status"
                    )
                )


auth_postgres_client_manager = PostgresClientManager(
    cfg.auth_postgresql,
    AuthBase,
)
meta_postgres_client_manager = PostgresClientManager(
    cfg.meta_postgresql,
    MetaBase,
)
