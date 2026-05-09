"""初始化数据库"""

import asyncio
import os
from pathlib import Path

import asyncmy
import dotenv
from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn
from sqlacodegen.generators import DeclarativeGenerator
from sqlalchemy import MetaData, create_engine

CURRENT_DIR = Path(__file__).parent
ROOT_DIR = CURRENT_DIR.parent  # 项目根目录
dotenv.load_dotenv(ROOT_DIR / "configs" / ".env")


def _write_code(output_path: Path, code: str) -> None:
    """同步写入生成的模型代码到文件"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(code)


class DBInit:
    async def delete_db(self, db_name: str):
        """删除数据库"""
        raise NotImplementedError

    async def create_db(self, db_name: str):
        """创建数据库"""
        raise NotImplementedError

    async def exec_sql_file(self, db_name: str, sql_file_path: Path):
        """执行 SQL 文件"""
        raise NotImplementedError

    def get_sync_db_url(self, db_name: str):
        """获取同步数据库连接 url"""
        raise NotImplementedError

    def get_async_db_url(self, db_name: str):
        """获取异步数据库连接 url"""
        raise NotImplementedError

    async def check_db_exists(self, db_name: str) -> bool:
        """检查数据库是否存在"""
        raise NotImplementedError

    async def gen_tb_model(self, output_path: Path, db_url: str):
        """通过反射数据库结构自动生成 SQLAlchemy ORM 模型代码"""
        engine = create_engine(db_url)
        try:
            metadata = MetaData()
            metadata.reflect(engine)
            generator = DeclarativeGenerator(metadata, engine, [])
            code = generator.generate()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(_write_code, output_path, code)
        finally:
            engine.dispose()

    async def init_db(self, db_sql_orm: list[tuple], max_workers: int = 5):
        """并发执行建库、导表、生成模型全流程，通过进度条展示进度"""
        logger.info(f"开始初始化数据库 {[db_name for db_name, _, _ in db_sql_orm]}")
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[cyan]{task.completed}/{task.total}"),
            console=Console(),
        ) as progress:
            task_id = progress.add_task("Start", total=len(db_sql_orm))
            semaphore = asyncio.Semaphore(max_workers)  # 信号量控制并发

            async def process_database(db_name: str, sql_file_path: Path, output_path: Path):
                """处理单个数据库的异步任务"""
                async with semaphore:
                    try:
                        if await self.check_db_exists(db_name):
                            await self.delete_db(db_name)
                        await self.create_db(db_name)
                        await self.exec_sql_file(db_name, sql_file_path)
                        db_url = self.get_sync_db_url(db_name)
                        await self.gen_tb_model(output_path, db_url)
                    finally:
                        progress.update(task_id, advance=1, description=f"{db_name[:8]:<8}")

            # 并发执行任务
            await asyncio.gather(
                *[
                    process_database(db_name, sql_file_path, output_path)
                    for db_name, sql_file_path, output_path in db_sql_orm
                ]
            )
            progress.update(task_id, description="Complete")
        logger.info("数据库初始化完成")


class MyInitializer(DBInit):
    """MySQL 数据库初始化器，负责建库、导表、模型生成"""

    def __init__(self, host: str, port: int, user: str, password: str):
        """初始化 MySQL 连接配置"""

        self._auth = f"{user}:{password}@{host}:{port}"
        self.conn_conf = {
            "host": host,
            "port": int(port),
            "user": user,
            "password": password,
        }

    async def check_db_exists(self, db_name: str) -> bool:
        """检查 MySQL 数据库是否存在"""
        try:
            conn = await asyncmy.connect(**self.conn_conf)
            try:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s",
                        (db_name,),
                    )
                    result = await cur.fetchone()
                    return result is not None
            finally:
                conn.close()
        except Exception as e:
            logger.exception(f"检查数据库存在性失败: {e}")
            raise

    async def delete_db(self, db_name: str):
        """删除数据库"""
        conn = await asyncmy.connect(**self.conn_conf, autocommit=True)
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE `{db_name}`")
        except Exception as e:
            logger.exception(f"数据库 {db_name} 删除失败: {e}")
            raise
        finally:
            conn.close()

    async def create_db(self, db_name: str):
        """创建数据库"""
        conn = await asyncmy.connect(**self.conn_conf, autocommit=True)
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4")
        except Exception as e:
            logger.exception(f"数据库 {db_name} 创建失败: {e}")
            raise
        finally:
            conn.close()

    async def exec_sql_file(self, db_name: str, sql_file_path: Path):
        """读取 SQL 文件并在目标库中执行"""
        sql = sql_file_path.read_text(encoding="utf-8")
        conn = await asyncmy.connect(**self.conn_conf, db=db_name)
        try:
            await conn.begin()
            async with conn.cursor() as cur:
                await cur.execute(sql)
            await conn.commit()
        except Exception as e:
            await conn.rollback()
            logger.exception(f"{sql_file_path.stem} 执行sql失败: {e}")
            raise
        finally:
            conn.close()

    def get_sync_db_url(self, db_name: str):
        """获取同步数据库连接 URL（用于 sqlacodegen 反射）"""
        return f"mysql+pymysql://{self._auth}/{db_name}"

    def get_async_db_url(self, db_name: str):
        """获取异步数据库连接 URL"""
        return f"mysql+asyncmy://{self._auth}/{db_name}"


def prepare():
    """根据环境变量创建数据库初始化器并收集 SQL 文件与模型输出路径"""
    db_driver = os.environ["DB_DRIVER"]
    if db_driver == "mysql":
        db_initializer = MyInitializer(
            host=os.environ["DB_HOST"],
            port=int(os.environ["DB_PORT"]),
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
        )
    else:
        logger.error(f"不支持的数据库驱动: {db_driver}")
        raise ValueError(f"不支持的数据库驱动: {db_driver}")

    sql_dir = ROOT_DIR / "sql" / db_driver
    orm_dir = CURRENT_DIR / "entities"
    sql_files = list(sql_dir.glob("*.sql"))
    db_sql_orm = [(f.stem, f, orm_dir / f"{f.stem}.py") for f in sql_files]
    return db_initializer, db_sql_orm


if __name__ == "__main__":
    db_initializer, db_sql_orm = prepare()
    asyncio.run(db_initializer.init_db(db_sql_orm))
