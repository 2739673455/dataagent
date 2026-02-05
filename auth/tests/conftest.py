import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from typing import AsyncGenerator, Generator

import asyncmy
import pytest
import pytest_asyncio
from app.config import CFG
from app.main import app
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_NAME = f"test_{CFG.db.database}"  # 测试数据库

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def create_test_database(conn_conf: dict, db_name: str):
    """创建测试数据库"""
    conn = await asyncmy.connect(**conn_conf)
    try:
        await conn.begin()
        async with conn.cursor() as cur:
            try:
                await cur.execute(f"DROP DATABASE IF EXISTS {db_name}")
            except Exception:
                pass
            await cur.execute(f"CREATE DATABASE {db_name} CHARACTER SET utf8mb4")
        await conn.commit()
    except Exception as e:
        await conn.rollback()
        logger.exception(f"Error creating database: {e}")
        raise
    finally:
        conn.close()


async def insert_data_into_test_db(conn_conf: dict, db_name: str, sql_file_path: Path):
    """向测试数据库插入测试数据"""
    conn = await asyncmy.connect(**conn_conf, db=db_name)
    try:
        with open(sql_file_path, "r", encoding="utf-8") as f:
            sql = f.read()
        await conn.begin()
        async with conn.cursor() as cur:
            await cur.execute(sql)
        await conn.commit()
    except Exception as e:
        await conn.rollback()
        logger.exception(f"{sql_file_path.stem} 执行sql失败: {e}")
    finally:
        conn.close()


async def clear_test_database(conn_conf: dict, db_name: str):
    """清理测试数据库"""
    conn = await asyncmy.connect(**conn_conf)
    try:
        await conn.begin()
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS {db_name}")
        await conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """创建事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_test_database():
    """创建测试数据库并初始化表结构"""
    conn_conf = {
        "host": CFG.db.host,
        "port": CFG.db.port,
        "user": CFG.db.user,
        "password": CFG.db.password,
    }

    # 创建测试数据库
    await create_test_database(conn_conf, TEST_DB_NAME)

    # 初始化表结构
    sql_file_path = Path(__file__).parent.parent / "app" / "sql" / "auth.sql"
    if sql_file_path.exists():
        await insert_data_into_test_db(conn_conf, TEST_DB_NAME, sql_file_path)

    yield

    # 清理测试数据库
    await clear_test_database(conn_conf, TEST_DB_NAME)


@pytest.fixture
def test_db_url() -> str:
    """获取测试数据库URL"""
    return f"mysql+asyncmy://{CFG.db.user}:{CFG.db.password}@{CFG.db.host}:{CFG.db.port}/{TEST_DB_NAME}"


@pytest_asyncio.fixture
async def test_db_session(test_db_url: str) -> AsyncGenerator[AsyncSession, None]:
    """创建测试数据库会话"""
    engine = create_async_engine(
        test_db_url,
        echo=False,
        pool_size=5,
        max_overflow=10,
    )
    session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def async_test_client() -> AsyncGenerator[AsyncClient, None]:
    """创建异步测试客户端"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def sync_test_client() -> TestClient:
    """创建同步测试客户端"""
    return TestClient(app)
