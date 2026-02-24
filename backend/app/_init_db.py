"""初始化数据库"""

import asyncio
import logging
import sys
from pathlib import Path

import asyncmy
from app.config import CFG, MySQLCfg
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn
from sqlacodegen.generators import DeclarativeGenerator
from sqlalchemy import MetaData, create_engine

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class DBInit:
    def __init__(self, config):
        self.db_url = ""

    async def create_db(self, db_name: str):
        """创建数据库"""
        raise NotImplementedError

    async def exec_sql_file(self, db_name: str, sql_file_path: Path):
        """执行 SQL 文件"""
        raise NotImplementedError

    async def get_db_url(self, db_name: str):
        """获取数据库连接 url"""
        raise NotImplementedError

    async def gen_tb_model(self, output_path: Path):
        """生成 SQLAlchemy 表模型"""
        # 创建 SQLAlchemy 数据库引擎
        engine = create_engine(self.db_url)
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
                        await self.get_db_url(db_name)
                        await self.gen_tb_model(output_path)
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
    """MySQL 数据库初始化"""

    def __init__(self, config: MySQLCfg):
        self.config = config
        self.conn_conf = {
            "host": config.host,
            "port": config.port,
            "user": config.user,
            "password": config.password,
        }

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
        with open(sql_file_path, "r", encoding="utf-8") as f:
            sql = f.read()
        conn = await asyncmy.connect(**self.conn_conf, db=db_name)
        try:
            await conn.begin()
            async with conn.cursor() as cur:
                await cur.execute(sql)
        except Exception as e:
            logger.exception(f"{sql_file_path.stem} 执行sql失败: {e}")
        finally:
            conn.close()

    async def get_db_url(self, db_name: str):
        self.db_url = f"mysql+pymysql://{self.config.user}:{self.config.password}@{self.config.host}:{self.config.port}/{db_name}"


if __name__ == "__main__":
    DB_DRIVER = CFG.db.driver
    DB_CONFIG = CFG.db.configs[DB_DRIVER]

    if isinstance(DB_CONFIG, MySQLCfg):
        # 配置数据库连接
        db_init = MyInit(DB_CONFIG)
    else:
        logger.error(f"不支持的数据库驱动: {DB_DRIVER}")
        sys.exit(1)

    # SQL 文件目录
    sql_dir = Path(__file__).parent.parent / "sql" / DB_DRIVER
    # 获取所有 SQL 文件
    sql_files = list(sql_dir.glob("*.sql"))
    # 表模型输出目录
    orm_dir = Path(__file__).parent / "entities"
    # db_name, sql_file_path, output_path
    db_sql_orm = []
    for f in sql_files:
        db_name = f.stem
        sql_file_path = f
        output_path = orm_dir / f"{f.stem}.py"
        db_sql_orm.append((db_name, sql_file_path, output_path))
    asyncio.run(db_init.init_db(db_sql_orm))
