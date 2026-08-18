"""PostgreSQL 元数据客户端管理"""

from collections.abc import AsyncIterator

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.conf.app_config import DBConfig, cfg
from app.entities import Base


class PostgresClientManager:
    """PostgreSQL 元数据客户端管理器"""

    def __init__(self, db_config: DBConfig) -> None:
        """初始化 PostgreSQL 元数据客户端管理器"""
        self._db_config = db_config
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
            raise RuntimeError("PostgreSQL client manager is not initialized")
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
        """初始化认证、授权与元数据表"""
        if self._engine is None:
            raise RuntimeError("PostgreSQL client manager is not initialized")
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)


meta_postgres_client_manager = PostgresClientManager(cfg.meta_postgresql)

if __name__ == "__main__":
    import asyncio

    from sqlalchemy import text

    meta_postgres_client_manager.init()

    async def test() -> None:
        await meta_postgres_client_manager.init_tables()

        try:
            async with meta_postgres_client_manager.session() as session:
                result = await session.execute(
                    text(
                        "select tablename from pg_tables "
                        "where schemaname = 'public' order by tablename"
                    )
                )
                rows = result.fetchall()
                print(rows)
        finally:
            await meta_postgres_client_manager.close()

    asyncio.run(test())
