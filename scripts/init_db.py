"""初始化 PostgreSQL 数据库。"""

import logging
from pathlib import Path

import dotenv
import psycopg
from psycopg import sql
from psycopg.errors import DuplicateDatabase

ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / "conf" / ".env"

POSTGRES_HOST = "127.0.0.1"
POSTGRES_PORT = 5432
POSTGRES_USER = "atguigu"
MAINTENANCE_DATABASE = "postgres"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PostgresInitializer:
    """PostgreSQL 数据库初始化器。"""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
    ) -> None:
        """初始化 PostgreSQL 连接配置。"""
        self._host = host
        self._port = port
        self._user = user
        self._password = password

    def delete_db(self, db_name: str) -> None:
        """数据库存在时删除数据库。"""
        try:
            with (
                psycopg.connect(
                    host=self._host,
                    port=self._port,
                    user=self._user,
                    password=self._password,
                    dbname=MAINTENANCE_DATABASE,
                    autocommit=True,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s",
                    (db_name,),
                )
                if cursor.fetchone() is None:
                    logger.info("数据库 %s 不存在，无需删除", db_name)
                    return

                cursor.execute(
                    sql.SQL("DROP DATABASE {}").format(sql.Identifier(db_name))
                )
                logger.info("数据库 %s 删除成功", db_name)
        except psycopg.Error:
            logger.exception("数据库 %s 删除失败", db_name)
            raise

    def create_db(self, db_name: str) -> None:
        """数据库不存在时创建数据库。"""
        try:
            with (
                psycopg.connect(
                    host=self._host,
                    port=self._port,
                    user=self._user,
                    password=self._password,
                    dbname=MAINTENANCE_DATABASE,
                    autocommit=True,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s",
                    (db_name,),
                )
                if cursor.fetchone() is not None:
                    logger.info("数据库 %s 已存在，无需创建", db_name)
                    return

                try:
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {} OWNER {}").format(
                            sql.Identifier(db_name),
                            sql.Identifier(self._user),
                        )
                    )
                except DuplicateDatabase:
                    logger.info("数据库 %s 已由其他进程创建", db_name)
                    return

                logger.info("数据库 %s 创建成功", db_name)
        except psycopg.Error:
            logger.exception("数据库 %s 创建失败", db_name)
            raise


if __name__ == "__main__":
    password = dotenv.dotenv_values(ENV_FILE).get("POSTGRES_PASSWORD")
    if password is None:
        raise ValueError(f"{ENV_FILE} 中缺少 POSTGRES_PASSWORD")

    initializer = PostgresInitializer(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=password,
    )

    initializer.delete_db("auth")
    initializer.create_db("auth")
    initializer.delete_db("meta")
    initializer.create_db("meta")
    initializer.delete_db("langgraph")
    initializer.create_db("langgraph")
