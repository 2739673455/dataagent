"""初始化数据库"""

import asyncio
import logging
import sys
from pathlib import Path

import asyncmy
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn
from sqlacodegen.generators import DeclarativeGenerator
from sqlalchemy import MetaData, create_engine


class DBInit:
    def __init__(self, conn_conf: dict):
        self.conn_conf = conn_conf

    async def create_db(self, db_name: str):
        """创建数据库"""
        raise NotImplementedError

    async def exec_sql_file(self, db_name: str, sql_file_path: Path):
        """执行 SQL 文件"""
        raise NotImplementedError

    async def gen_tb_model(self, db_name: str, output_path: Path):
        """生成表模型"""
        raise NotImplementedError

    async def init_db(self, db_sql_orm: list[tuple], max_workers: int = 5):
        """初始化数据库并导入数据"""

        logger.info(f"开始初始化数据库 {[db_name for db_name, _, _ in db_sql_orm]}")
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[cyan]{task.completed}/{task.total}"),
            console=Console(),
        ) as progress:
            task_id = progress.add_task("Start", total=len(db_sql_orm))
            semaphore = asyncio.Semaphore(max_workers)  # 信号量控制并发

            async def process_database(
                db_name: str, sql_file_path: Path, output_path: Path
            ):
                """处理单个数据库的异步任务"""
                async with semaphore:
                    try:
                        await self.create_db(db_name)
                        await self.exec_sql_file(db_name, sql_file_path)
                        await self.gen_tb_model(db_name, output_path)
                    finally:
                        progress.update(
                            task_id, advance=1, description=f"{db_name[:8]:<8}"
                        )

            # 并发执行任务
            await asyncio.gather(
                *[
                    process_database(db_name, sql_file_path, output_path)
                    for db_name, sql_file_path, output_path in db_sql_orm
                ]
            )
            progress.update(task_id, description="Complete")
        logger.info("数据库初始化完成")


class MyInit(DBInit):
    ERROR_PATTERNS = []

    async def create_db(self, db_name: str):
        conn = await asyncmy.connect(**self.conn_conf, autocommit=True)
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE {db_name} CHARACTER SET utf8mb4")
        except Exception as e:
            if e.args[0] != 1007:
                logger.exception(f"数据库 {db_name} 创建失败: {e}")
        finally:
            conn.close()

    async def exec_sql_file(self, db_name: str, sql_file_path: Path):
        conn = await asyncmy.connect(**self.conn_conf, db=db_name)
        try:
            with open(sql_file_path, "r", encoding="utf-8") as f:
                sql = f.read()
            await conn.begin()
            async with conn.cursor() as cur:
                await cur.execute(sql)
            await conn.commit()
        except Exception as e:
            await conn.rollback()
            logger.error(f"{sql_file_path.stem} 执行sql失败: {e}")
        finally:
            conn.close()

    async def gen_tb_model(self, db_name: str, output_path: Path):
        # 创建 SQLAlchemy 数据库引擎
        db_url = f"mysql+pymysql://{self.conn_conf['user']}:{self.conn_conf['password']}@{self.conn_conf['host']}:{self.conn_conf['port']}/{db_name}"
        engine = create_engine(db_url)
        # 创建元数据对象并反射数据库结构
        metadata = MetaData()
        metadata.reflect(engine)
        # 使用 DeclarativeGenerator 生成模型代码
        generator = DeclarativeGenerator(metadata, engine, [])
        code = generator.generate()
        # 将生成的代码写入文件
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(code)


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# 数据库连接配置
my_init = MyInit(
    {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "root",
        "password": "123321",
    },
)
sql_dir = Path(__file__).parent.parent / "sql"  # 数据库文件目录
sql_files = list(sql_dir.glob("*.sql"))  # 获取所有SQL文件
orm_dir = Path(__file__).parent.parent / "entities"  # 表模型输出目录
db_sql_orm = [(f.stem, f, orm_dir / f"{f.stem}.py") for f in sql_files]
asyncio.run(my_init.init_db(db_sql_orm))
