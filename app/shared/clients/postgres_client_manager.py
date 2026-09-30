"""PostgreSQL 引擎、会话工厂与建表操作。"""

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.shared.config.app_config import DBConfig


class PostgresClientManager:
    """持有 PostgreSQL 引擎和可直接调用的会话工厂。"""

    def __init__(self, db_config: DBConfig, base: type[DeclarativeBase]) -> None:
        """创建连接池和会话工厂；连接按需建立。"""
        self._base = base
        self.engine = create_async_engine(
            URL.create(
                drivername="postgresql+psycopg",
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
        self.session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def close(self) -> None:
        """释放连接池。"""
        await self.engine.dispose()

    async def init_tables(self) -> None:
        """根据当前 ORM 模型创建尚未存在的数据表。"""
        async with self.engine.begin() as connection:
            await connection.run_sync(self._base.metadata.create_all)
