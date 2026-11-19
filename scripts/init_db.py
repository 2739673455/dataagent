"""补建应用内部库；仅显式本机开发模式提供交互式重置。"""

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import dotenv
import psycopg
from psycopg import Connection, sql
from psycopg.errors import DuplicateDatabase

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "conf" / ".env"
APPLICATION_DATABASES = ("auth", "meta", "langgraph")
DEV_CLUSTER = "dataagent-internal-dev"
logger = logging.getLogger(__name__)


class ResetRefused(RuntimeError):
    """不能充分确认目标或授权。"""


def _ensure(connection: Connection) -> None:
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
            logger.info("保留并发创建的数据库 %s", name)
        else:
            logger.info("已创建数据库 %s", name)


def ensure_application_databases(
    *, host: str, port: int, user: str, password: str
) -> None:
    """默认命令：只创建缺失内部库，不依赖 Docker，不修改现有内容。"""
    with psycopg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname="postgres",
        autocommit=True,
    ) as conn:
        _ensure(conn)


def _docker(*args: str) -> str:
    """只读核对本机 Docker Unix socket；不接受远程 Docker context。"""
    try:
        return subprocess.run(
            ["docker", "--host", "unix:///var/run/docker.sock", *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ResetRefused(
            "无法通过本机 Docker 验证开发容器；请检查是否已启动当前项目的 postgres 服务。"
        ) from exc


def _local_cluster_id(port: int) -> str:
    """从本检出的 Compose 内部库容器验证环回端口及物理数据卷标识。"""
    container = _docker(
        "ps",
        "--filter",
        "label=com.docker.compose.project=dataagent",
        "--filter",
        "label=com.docker.compose.service=postgres",
        "--filter",
        f"label=com.docker.compose.project.config_files={ROOT / 'docker/compose.yml'}",
        "--format",
        "{{.ID}}",
    )
    if not re.fullmatch(r"[0-9a-f]{12,64}", container):
        raise ResetRefused("未找到唯一匹配本项目的内部 PostgreSQL 开发容器。")
    if _docker("port", container, "5432/tcp") != f"127.0.0.1:{port}":
        raise ResetRefused("开发容器必须仅映射至配置的 IPv4 环回端口。")
    info = _docker(
        "exec",
        "--user",
        "postgres",
        "--env",
        "LC_ALL=C",
        container,
        "pg_controldata",
        "/var/lib/postgresql/data",
    )
    ids = re.findall(r"^Database system identifier:\s*(\d+)\s*$", info, re.MULTILINE)
    if len(ids) != 1:
        raise ResetRefused("无法验证开发容器的数据卷标识。")
    return ids[0]


def _preflight(conn: Connection, expected_id: str) -> None:
    identity = conn.execute(
        "SELECT system_identifier::text, current_setting('cluster_name'), pg_is_in_recovery() "
        "FROM pg_catalog.pg_control_system()"
    ).fetchone()
    if identity != (expected_id, DEV_CLUSTER, False):
        raise ResetRefused("数据库物理标识或专用开发集群标记不匹配。")
    rows = conn.execute(
        "SELECT datname, pg_get_userbyid(datdba) = current_user, datallowconn FROM pg_database "
        "WHERE datname NOT IN ('template0','template1','postgres')"
    ).fetchall()
    if {n for n, owned, enabled in rows if owned and enabled} != set(
        APPLICATION_DATABASES
    ) or len(rows) != 3:
        raise ResetRefused(
            "集群必须专用于三个内部库，库名、所有者与可连接状态均须匹配；不重置共享实例。"
        )
    _require_idle(conn)


def _require_idle(conn: Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM pg_stat_activity WHERE datname::text = ANY(%s) LIMIT 1",
        (list(APPLICATION_DATABASES),),
    ).fetchone():
        raise ResetRefused(
            "目标库仍有连接；请先停止应用与 Worker 并关闭客户端，不会强制断开。"
        )
    if conn.execute(
        "SELECT 1 FROM pg_prepared_xacts WHERE database::text = ANY(%s) LIMIT 1",
        (list(APPLICATION_DATABASES),),
    ).fetchone():
        raise ResetRefused("目标库存在预备事务。")
    if (
        conn.execute(
            "SELECT 1 FROM pg_replication_slots WHERE database::text = ANY(%s) LIMIT 1",
            (list(APPLICATION_DATABASES),),
        ).fetchone()
        or conn.execute(
            "SELECT 1 FROM pg_subscription s JOIN pg_database d ON s.subdbid=d.oid "
            "WHERE d.datname::text = ANY(%s) LIMIT 1",
            (list(APPLICATION_DATABASES),),
        ).fetchone()
    ):
        raise ResetRefused("目标库正在使用逻辑复制。")


def reset_local_databases(*, port: int, user: str, password: str) -> None:
    """只针对本机明确标记的 Compose 开发集群；未通过全部校验前不修改数据库。"""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ResetRefused("reset-db 必须在交互终端中运行，不支持管道确认。")
    if os.getenv("BUSINESS_PG_DATABASE", "ecommerce") in APPLICATION_DATABASES:
        raise ResetRefused("业务库名称与内部库冲突。")
    expected_id = _local_cluster_id(port)
    with psycopg.connect(
        host="127.0.0.1",
        port=port,
        user=user,
        password=password,
        dbname="postgres",
        autocommit=True,
        connect_timeout=5,
    ) as conn:
        if conn.execute("SELECT pg_try_advisory_lock(84510, 17001)").fetchone() != (
            True,
        ):
            raise ResetRefused("已有重置操作正在进行。")
        _preflight(conn, expected_id)
        print(f"本机开发 PostgreSQL 127.0.0.1:{port}（集群 {expected_id}）")
        print("将永久清空并重建 auth、meta、langgraph。此操作不可撤销，请先备份。")
        if input("请输入 RESET 继续，其他输入取消：") != "RESET":
            raise ResetRefused("已取消，未修改数据库。")
        _preflight(conn, expected_id)
        # 在任何 DROP 前同时禁止三个库接受新连接，再检查确认期间的连接竞争。
        with conn.transaction():
            for name in APPLICATION_DATABASES:
                conn.execute(
                    sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(
                        sql.Identifier(name)
                    )
                )
        try:
            _require_idle(conn)
            for name in APPLICATION_DATABASES:
                conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            _ensure(conn)
        finally:
            # DROP/CREATE DATABASE 不支持原子提交；恢复未被删除库的可连接性。
            for (name,) in conn.execute(
                "SELECT datname FROM pg_database WHERE datname::text = ANY(%s) AND NOT datallowconn",
                (list(APPLICATION_DATABASES),),
            ).fetchall():
                conn.execute(
                    sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS true").format(
                        sql.Identifier(name)
                    )
                )
        print("三个内部数据库已重新创建；业务数据库未连接或修改。")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true", help="交互式重置本机专用开发内部库"
    )
    args = parser.parse_args()
    dotenv.load_dotenv(ENV_FILE)
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("请设置 POSTGRES_PASSWORD，再运行初始化命令")
    port = int(os.getenv("APP_PG_PORT", "5432"))
    if args.reset:
        try:
            reset_local_databases(port=port, user="atguigu", password=password)
        except (ResetRefused, EOFError, KeyboardInterrupt) as exc:
            raise SystemExit(str(exc) or "操作已取消") from None
    else:
        ensure_application_databases(
            host="127.0.0.1", port=port, user="atguigu", password=password
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
