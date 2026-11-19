"""验证应用数据库初始化是增量创建，而非重置。"""

import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from scripts.init_db import APPLICATION_DATABASES, ensure_application_databases


def test_init_preserves_existing_databases_and_rows():
    # 只在测试脚本新建的空集群上运行，不在共享开发实例上建库。
    if os.getenv("DATAAGENT_DISPOSABLE_INIT_TEST") != "1":
        pytest.skip("run scripts/development/test_business_pg.sh")
    params = {
        k: str(v)
        for k, v in conninfo_to_dict(os.environ["DATAAGENT_TEST_PG_DSN"]).items()
        if v is not None
    }

    def ensure():
        ensure_application_databases(
            host=params["host"],
            port=int(params["port"]),
            user=params["user"],
            password=params["password"],
        )

    def connect(name):
        return psycopg.connect(**{**params, "dbname": name}, autocommit=True)

    # 模拟部分初始化：auth 已有数据，meta/langgraph 尚不存在。业务库完全独立。
    with connect("postgres") as db:
        db.execute("CREATE DATABASE auth")
    for name in ("auth", "ecommerce"):
        with connect(name) as db:
            db.execute(
                "CREATE TABLE init_preservation_probe (value integer PRIMARY KEY)"
            )
            db.execute("INSERT INTO init_preservation_probe VALUES (101)")

    ensure()
    for name in ("meta", "langgraph"):
        with connect(name) as db:
            db.execute(
                "CREATE TABLE init_preservation_probe (value integer PRIMARY KEY)"
            )
            db.execute("INSERT INTO init_preservation_probe VALUES (101)")
    ensure()
    for name in (*APPLICATION_DATABASES, "ecommerce"):
        with connect(name) as db:
            assert db.execute(
                "SELECT value FROM init_preservation_probe"
            ).fetchall() == [(101,)]
