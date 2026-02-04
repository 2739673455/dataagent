import asyncio

# 添加项目根目录到Python路径
import sys
from pathlib import Path
from typing import AsyncGenerator, Generator

import asyncmy
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import CFG
from app.main import app

# 测试数据库配置
TEST_DB_NAME = "auth_test"


# 保存原始数据库名称
ORIGINAL_DB_NAME = CFG.db.database


@pytest.fixture(scope="session", autouse=True)
def set_test_database():
    """设置测试数据库名称"""
    # 修改配置使用测试数据库
    CFG.db.database = TEST_DB_NAME
    yield
    # 恢复原始配置
    CFG.db.database = ORIGINAL_DB_NAME


# 测试数据库配置
TEST_DB_NAME = "auth_test"


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
        "autocommit": False,
    }

    # 创建测试数据库
    conn = await asyncmy.connect(**conn_conf)
    try:
        await conn.begin()
        async with conn.cursor() as cur:
            try:
                await cur.execute(f"DROP DATABASE IF EXISTS `{TEST_DB_NAME}`")
            except Exception:
                pass  # 数据库可能不存在，忽略错误
            await cur.execute(f"CREATE DATABASE `{TEST_DB_NAME}` CHARACTER SET utf8mb4")
        await conn.commit()
    except Exception as e:
        await conn.rollback()
        print(f"Error creating database: {e}")
        raise
    finally:
        conn.close()

    # 初始化表结构
    sql_file = Path(__file__).parent.parent / "app" / "sql" / "auth.sql"
    if sql_file.exists():
        conn = await asyncmy.connect(**conn_conf, db=TEST_DB_NAME)
        try:
            with open(sql_file, "r", encoding="utf-8") as f:
                sql_content = f.read()

            # 执行SQL语句
            statements = []
            current_statement = []
            for line in sql_content.split("\n"):
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue
                current_statement.append(line)
                if stripped.endswith(";"):
                    statements.append("\n".join(current_statement))
                    current_statement = []
            if current_statement:
                statements.append("\n".join(current_statement))

            # 执行每条SQL语句
            for statement in statements:
                statement = statement.strip()
                if statement:
                    await conn.begin()
                    try:
                        async with conn.cursor() as cur:
                            await cur.execute(statement)
                        await conn.commit()
                    except Exception as e:
                        await conn.rollback()
                        # 忽略DROP TABLE错误（表可能不存在）
                        if (
                            "DROP TABLE" not in statement
                            and "DROP DATABASE" not in statement
                        ):
                            print(f"Error executing: {statement[:80]}... Error: {e}")
                            raise e
        except Exception as e:
            print(f"Error in database setup: {e}")
            raise
        finally:
            conn.close()

    yield

    # 清理测试数据库
    conn = await asyncmy.connect(**conn_conf)
    try:
        await conn.begin()
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{TEST_DB_NAME}`")
        await conn.commit()
    finally:
        conn.close()


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
async def clean_db(test_db_url: str):
    """在每个测试后清理数据库数据"""
    engine = create_async_engine(test_db_url, echo=False)
    session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_maker() as session:
        yield session
        # 清理数据
        try:
            await session.execute(text("DELETE FROM refresh_token"))
        except:
            pass
        try:
            await session.execute(text("DELETE FROM group_user_rel"))
        except:
            pass
        try:
            await session.execute(text("DELETE FROM group_scope_rel"))
        except:
            pass
        try:
            await session.execute(text("DELETE FROM user WHERE email != ''"))
        except:
            pass
        try:
            await session.execute(text("DELETE FROM `group` WHERE id > 1"))
        except:
            pass
        try:
            await session.execute(text("DELETE FROM scope WHERE id > 0"))
        except:
            pass
        await session.commit()

    await engine.dispose()


@pytest_asyncio.fixture
async def test_client() -> AsyncGenerator[AsyncClient, None]:
    """创建异步测试客户端"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def sync_test_client() -> TestClient:
    """创建同步测试客户端（仅用于健康检查等简单测试）"""
    return TestClient(app)


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """创建异步测试客户端"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# 移除自动清理，因为会导致事件循环问题
# 每个测试使用不同的邮箱来避免冲突


@pytest.fixture
def test_user_data() -> dict:
    """测试用户数据"""
    return {
        "email": "test@example.com",
        "username": "testuser",
        "password": "testpass123",
    }


@pytest.fixture
def test_user_data2() -> dict:
    """第二个测试用户数据"""
    return {
        "email": "test2@example.com",
        "username": "testuser2",
        "password": "testpass456",
    }


@pytest.fixture
def test_user_data3() -> dict:
    """第三个测试用户数据"""
    return {
        "email": "test3@example.com",
        "username": "testuser3",
        "password": "testpass789",
    }


@pytest.fixture
def test_user_data4() -> dict:
    """第四个测试用户数据"""
    return {
        "email": "test4@example.com",
        "username": "testuser4",
        "password": "testpass000",
    }


@pytest.fixture
def test_user_data5() -> dict:
    """第五个测试用户数据"""
    return {
        "email": "test5@example.com",
        "username": "testuser5",
        "password": "testpass111",
    }
