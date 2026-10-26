"""Optional PostgreSQL tests use a private cluster, never the configured source."""

import os
import secrets
import shutil
import socket
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from store_dw import source_ops


def pytest_addoption(parser):
    parser.addoption("--run-postgres", action="store_true", help="Start isolated PostgreSQL tests")


@pytest.fixture(scope="session")
def postgres_cluster(request):
    if not request.config.getoption("--run-postgres"):
        pytest.skip("Use --run-postgres to start a private PostgreSQL test cluster")
    executables = {name: shutil.which(name) for name in ("initdb", "pg_ctl", "psql")}
    if not all(executables.values()):
        pytest.fail("PostgreSQL initdb, pg_ctl and psql must be on PATH")

    root = Path(__file__).resolve().parents[1] / ".pgdata"
    work = root / f"pytest-{uuid.uuid4().hex}"
    work.mkdir(parents=True)
    data = work / "cluster"
    password_file = work / "password"
    password = secrets.token_urlsafe(24)
    password_file.write_text(password, encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    # Do not inherit connection/cluster settings from the developer's environment.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

    def command(args):
        # A server started by pg_ctl must not inherit Python's capture pipes on Windows.
        log = work / "commands.log"
        with log.open("a", encoding="utf-8") as output:
            result = subprocess.run(
                args, stdout=output, stderr=output, env=env, timeout=45, **flags
            )
        if result.returncode:
            pytest.fail(f"PostgreSQL test setup failed: {log.read_text(encoding='utf-8')}")

    started = False
    try:
        command(
            [
                executables["initdb"],
                "-D",
                str(data),
                "-U",
                "postgres",
                "-A",
                "scram-sha-256",
                "--pwfile",
                str(password_file),
                "--encoding=UTF8",
                "--no-locale",
            ]
        )
        password_file.unlink()
        started = True
        command(
            [
                executables["pg_ctl"],
                "-D",
                str(data),
                "-l",
                str(work / "postgres.log"),
                "-o",
                f"-h 127.0.0.1 -p {port}",
                "-w",
                "start",
            ]
        )
        dsn = make_conninfo(
            host="127.0.0.1",
            port=port,
            dbname="postgres",
            user="postgres",
            password=password,
            connect_timeout=5,
            options="-c statement_timeout=10000 -c lock_timeout=5000",
        )
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("CREATE DATABASE undead_schema_test")
        yield {
            "dsn": make_conninfo(dsn, dbname="undead_schema_test"),
            "other_dsn": dsn,
            "psql": executables["psql"],
            "env": {**env, "PGPASSWORD": password},
            "flags": flags,
        }
    finally:
        if started:
            # A stop failure deliberately keeps the directory for diagnosis.
            command([executables["pg_ctl"], "-D", str(data), "-m", "fast", "-w", "stop"])
        if work.resolve().parent != root.resolve():
            raise RuntimeError("Refusing cleanup outside the project's .pgdata directory")
        shutil.rmtree(work)


@pytest.fixture
def empty_source(postgres_cluster, monkeypatch):
    dsn = postgres_cluster["dsn"]
    monkeypatch.setenv("STORE_DW_SOURCE_DSN", dsn)
    monkeypatch.setenv("STORE_DW_WAREHOUSE_DSN", postgres_cluster["other_dsn"])
    monkeypatch.setenv("STORE_DW_CONNECT_TIMEOUT_SECONDS", "5")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS store_source CASCADE")
        yield conn


@pytest.fixture
def source_db(empty_source):
    source_ops.run_source_migrations()
    return empty_source
