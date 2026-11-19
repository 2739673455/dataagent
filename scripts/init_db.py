"""仅创建缺失的应用内部数据库。重复运行不会修改已有库的数据、权限或配置。"""

import logging
import os
from pathlib import Path

import dotenv
import psycopg
from psycopg import sql
from psycopg.errors import DuplicateDatabase

ENV_FILE = Path(__file__).resolve().parents[1] / "conf" / ".env"
APPLICATION_DATABASES = ("auth", "meta", "langgraph")
logger = logging.getLogger(__name__)


def ensure_application_databases(
    *, host: str, port: int, user: str, password: str
) -> None:
    """连接维护库；只补建三个固定名称的内部数据库，不连接业务库。"""
    with psycopg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname="postgres",
        autocommit=True,
    ) as connection:
        for name in APPLICATION_DATABASES:
            if connection.execute(
                "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (name,)
            ).fetchone():
                logger.info("保留已存在的数据库 %s", name)
                continue
            try:
                connection.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
                )
            except DuplicateDatabase:
                # 另一个初始化进程刚创建了它；无需处理其内容。
                logger.info("保留并发创建的数据库 %s", name)
            else:
                logger.info("已创建数据库 %s", name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    dotenv.load_dotenv(ENV_FILE)
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("请设置 POSTGRES_PASSWORD，再运行初始化命令")
    ensure_application_databases(
        host="127.0.0.1",
        port=int(os.getenv("APP_PG_PORT", "5432")),
        user="atguigu",
        password=password,
    )
