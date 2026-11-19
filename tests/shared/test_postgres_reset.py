"""只在本次测试创建的专用开发集群验证重置；已有实例绝不作为重置目标。"""

import os
import secrets
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg
import pytest

from scripts import init_db


def test_no_terminal_or_business_name_collision_never_inspects_or_connects(monkeypatch):
    monkeypatch.setattr(init_db, "_local_cluster_id", Mock(side_effect=AssertionError))
    for tty, business_db, reason in (
        (False, "ecommerce", "交互终端"),
        (True, "auth", "业务库名称"),
    ):
        monkeypatch.setattr(sys.stdin, "isatty", Mock(return_value=tty))
        monkeypatch.setattr(sys.stdout, "isatty", Mock(return_value=tty))
        monkeypatch.setenv("BUSINESS_PG_DATABASE", business_db)
        with pytest.raises(init_db.ResetRefused, match=reason):
            init_db.reset_local_databases(port=5432, user="unused", password="")


def test_local_docker_proof_requires_exact_container_and_loopback_binding(monkeypatch):
    for outputs, message in (
        ([""], "唯一"),
        (["a" * 12 + "\n" + "b" * 12], "唯一"),
        (["a" * 12, "0.0.0.0:5432"], "环回"),
        (["a" * 12, "127.0.0.1:5432", ""], "数据卷"),
    ):
        monkeypatch.setattr(init_db, "_docker", Mock(side_effect=outputs))
        with pytest.raises(init_db.ResetRefused, match=message):
            init_db._local_cluster_id(5432)
    docker = Mock(
        side_effect=["a" * 12, "127.0.0.1:5432", "Database system identifier:  12345"]
    )
    monkeypatch.setattr(init_db, "_docker", docker)
    assert init_db._local_cluster_id(5432) == "12345"
    ps_args = docker.call_args_list[0].args
    assert "label=com.docker.compose.service=postgres" in ps_args
    assert (
        f"label=com.docker.compose.project.config_files={init_db.ROOT / 'docker/compose.yml'}"
        in ps_args
    )


@contextmanager
def _disposable_dev(tmp_path: Path):
    if os.getenv("DATAAGENT_DISPOSABLE_INIT_TEST") != "1":
        pytest.skip("run scripts/development/test_business_pg.sh")
    password = secrets.token_urlsafe(32)
    password_file = tmp_path / "password"
    password_file.write_text(password)
    password_file.chmod(0o600)
    data = str(tmp_path / "db")
    args = [
        "initdb",
        "-D",
        data,
        "-U",
        "atguigu",
        "--auth=scram-sha-256",
        f"--pwfile={password_file}",
        "--no-locale",
        "-E",
        "UTF8",
    ]
    if share := os.getenv("PGSHARE"):
        args += ["-L", share]
    subprocess.run(args, capture_output=True, check=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    subprocess.run(
        [
            "pg_ctl",
            "-D",
            data,
            "-l",
            str(tmp_path / "server.log"),
            "-o",
            f"-h 127.0.0.1 -k '' -p {port} -c cluster_name={init_db.DEV_CLUSTER}",
            "-w",
            "start",
        ],
        capture_output=True,
        check=True,
    )
    try:
        info = subprocess.run(
            ["pg_controldata", data],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "LC_ALL": "C"},
        ).stdout
        yield port, password, info
    finally:
        subprocess.run(
            ["pg_ctl", "-D", data, "-m", "fast", "-w", "stop"],
            capture_output=True,
            check=True,
        )


def test_disposable_guarded_reset_and_separate_business_db(tmp_path, monkeypatch):
    with _disposable_dev(tmp_path) as (port, password, control):
        monkeypatch.setenv("BUSINESS_PG_DATABASE", "ecommerce")
        kwargs = {
            "host": "127.0.0.1",
            "port": port,
            "user": "atguigu",
            "password": password,
        }
        reset_kwargs = {"port": port, "user": "atguigu", "password": password}

        def db(name):
            return psycopg.connect(**kwargs, dbname=name, autocommit=True)

        def existing():
            with db("postgres") as c:
                return c.execute(
                    "SELECT datname, oid, datallowconn FROM pg_database WHERE datname::text = ANY(%s) ORDER BY 1",
                    (list(init_db.APPLICATION_DATABASES),),
                ).fetchall()

        # Docker is not required in this suite. Return the real cluster's control data
        # at that boundary; password checks, identity verification and all SQL remain real.
        def docker(*args):
            if args[0] == "ps":
                return "a" * 12
            if args[0] == "port":
                return f"127.0.0.1:{port}"
            assert args[0] == "exec"
            return control

        monkeypatch.setattr(init_db, "_docker", docker)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        init_db.ensure_application_databases(**kwargs)
        for name in init_db.APPLICATION_DATABASES:
            with db(name) as c:
                c.execute("CREATE TABLE marker (value integer)")
                c.execute("INSERT INTO marker VALUES (71)")
        old = existing()
        with psycopg.connect(
            os.environ["DATAAGENT_TEST_PG_DSN"], autocommit=True
        ) as business:
            business.execute(
                "CREATE TEMP TABLE reset_business_probe AS SELECT 83 AS value"
            )
            for response in ("", "reset", "RESET ", "NO"):
                with (
                    patch("builtins.input", return_value=response),
                    pytest.raises(init_db.ResetRefused, match="已取消"),
                ):
                    init_db.reset_local_databases(**reset_kwargs)
                assert existing() == old
            with patch("builtins.input", side_effect=EOFError), pytest.raises(EOFError):
                init_db.reset_local_databases(**reset_kwargs)
            with (
                patch.object(init_db, "_local_cluster_id", return_value="0"),
                patch("builtins.input", side_effect=AssertionError),
                pytest.raises(init_db.ResetRefused, match="物理标识"),
            ):
                init_db.reset_local_databases(**reset_kwargs)
            with (
                db("meta"),
                patch("builtins.input", side_effect=AssertionError),
                pytest.raises(init_db.ResetRefused, match="仍有连接"),
            ):
                init_db.reset_local_databases(**reset_kwargs)
            late = []

            def arrive(_):
                late.append(db("auth"))
                return "RESET"

            try:
                with (
                    patch("builtins.input", side_effect=arrive),
                    pytest.raises(init_db.ResetRefused, match="仍有连接"),
                ):
                    init_db.reset_local_databases(**reset_kwargs)
            finally:
                for c in late:
                    c.close()
            assert existing() == old
            for name in init_db.APPLICATION_DATABASES:
                with db(name) as c:
                    assert c.execute("SELECT value FROM marker").fetchall() == [(71,)]
            with patch("builtins.input", return_value="RESET"):
                init_db.reset_local_databases(**reset_kwargs)
            new = existing()
            assert [n for n, _, _ in new] == [n for n, _, _ in old]
            assert {oid for _, oid, enabled in new if enabled}.isdisjoint(
                oid for _, oid, _ in old
            )
            for name in init_db.APPLICATION_DATABASES:
                with db(name) as c:
                    assert c.execute(
                        "SELECT to_regclass('public.marker')"
                    ).fetchone() == (None,)
            assert business.execute(
                "SELECT value FROM reset_business_probe"
            ).fetchall() == [(83,)]
            # A business database colocated on the target makes the instance ineligible.
            with db("postgres") as c:
                c.execute("CREATE DATABASE ecommerce")
            with db("ecommerce") as c:
                c.execute("CREATE TABLE untouched AS SELECT 97 AS value")
            with (
                patch("builtins.input", side_effect=AssertionError),
                pytest.raises(init_db.ResetRefused, match="专用"),
            ):
                init_db.reset_local_databases(**reset_kwargs)
            assert existing() == new
            with db("ecommerce") as c:
                assert c.execute("SELECT value FROM untouched").fetchall() == [(97,)]
