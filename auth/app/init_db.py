"""初始化数据库"""

import os
import sqlite3
from pathlib import Path

import dotenv
from loguru import logger

# 路径常量
CURRENT_DIR = Path(__file__).parent  # app
ROOT_DIR = CURRENT_DIR.parent  # 项目根目录

# 加载环境变量
dotenv.load_dotenv(ROOT_DIR / "configs" / ".env")


class DBInitializer:
    def check_db_exists(self, db_name: str) -> bool:
        """检查数据库是否存在"""
        raise NotImplementedError

    def delete_db(self, db_name: str):
        """删除数据库"""
        raise NotImplementedError

    def create_db(self, db_name: str):
        """创建数据库"""
        raise NotImplementedError

    def exec_sql_file(self, db_name: str, sql_file_path: Path):
        """执行 SQL 文件"""
        raise NotImplementedError

    def init_db(self, dbs: list[dict], max_workers: int = 5):
        """初始化数据库并导入数据"""

        for i in dbs:
            db_name = i["db_name"]
            sql_file = i["sql_file"]

            logger.info(f"开始初始化数据库 {db_name}")

            # 检查数据库是否存在
            if self.check_db_exists(db_name):
                # 删除数据库
                self.delete_db(db_name)
            # 创建数据库
            self.create_db(db_name)
            # 执行 SQL 文件
            self.exec_sql_file(db_name, sql_file)

            logger.info("数据库初始化完成")


class SQLiteInitializer(DBInitializer):
    """SQLite 数据库初始化"""

    def __init__(self, file_path: str):
        self.file_path = (ROOT_DIR / file_path).resolve()

    def check_db_exists(self, db_name: str) -> bool:
        """检查数据库是否存在"""
        return self.file_path.exists()

    def delete_db(self, db_name: str):
        """删除数据库"""
        self.file_path.unlink(missing_ok=True)
        # 同时删除 WAL/SHM 文件，否则下次连接会恢复旧数据
        for suffix in ("-wal", "-shm"):
            p = self.file_path.with_suffix(self.file_path.suffix + suffix)
            p.unlink(missing_ok=True)

    def create_db(self, db_name: str):
        """创建数据库"""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.file_path)
        conn.close()

    def exec_sql_file(self, db_name: str, sql_file_path: Path):
        """执行 SQL 文件"""
        sql = sql_file_path.read_text(encoding="utf-8")
        conn = sqlite3.connect(self.file_path)
        try:
            conn.executescript(sql)
            conn.commit()
        finally:
            conn.close()


def prepare():
    db_driver = os.environ["DB_DRIVER"]

    if db_driver == "sqlite":
        db_sqlite_file = os.environ["DB_SQLITE_FILE"]
        db_initializer = SQLiteInitializer(db_sqlite_file)

    else:
        logger.error(f"不支持的数据库驱动: {db_driver}")
        raise ValueError(f"不支持的数据库驱动: {db_driver}")

    sql_dir = ROOT_DIR / "sql" / db_driver  # SQL 文件目录
    sql_files = list(sql_dir.glob("*.sql"))  # 获取所有 SQL 文件
    dbs = [{"db_name": f.stem, "sql_file": f} for f in sql_files]
    return db_initializer, dbs


if __name__ == "__main__":
    db_initializer, dbs = prepare()
    db_initializer.init_db(dbs)
