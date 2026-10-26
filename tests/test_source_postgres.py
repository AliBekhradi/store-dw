import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
import seed_store
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from store_dw import cli, source_ops


def load_fixture(conn):
    with conn.transaction():
        order_ids, item_ids = seed_store.insert_store_records(
            conn,
            seed_store.build_customers(),
            seed_store.build_products(),
            seed_store.build_orders(),
        )
        seed_store.verify_store_fixture(conn, order_ids)
    return order_ids, item_ids


def test_migration_rerun_preserves_data_and_single_history_entry(source_db):
    load_fixture(source_db)
    before = source_db.execute("SELECT * FROM store_source.schema_migrations").fetchall()
    source_ops.run_source_migrations()
    assert source_db.execute("SELECT * FROM store_source.schema_migrations").fetchall() == before
    assert len(before) == 1
    assert source_db.execute("SELECT count(*) FROM store_source.orders").fetchone()[0] == 4


def test_daily_sales_match_hand_calculated_fixture(source_db):
    load_fixture(source_db)
    rows = source_db.execute("""
        SELECT (o.completed_at AT TIME ZONE 'Asia/Tehran')::date, SUM(i.line_sales_rial)
        FROM store_source.orders o
        JOIN store_source.order_items i USING (order_id)
        WHERE o.order_status = 'completed'
        GROUP BY 1
    """).fetchall()
    assert [(day.isoformat(), total) for day, total in rows] == [("2025-01-15", 9_900_000)]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE store_source.order_items SET quantity = quantity + 1",
        "DELETE FROM store_source.order_items",
        "TRUNCATE store_source.orders CASCADE",
        "UPDATE store_source.orders SET order_status = 'cancelled' "
        "WHERE order_status = 'completed'",
        "UPDATE store_source.products SET sku = 'CHANGED'",
        "UPDATE store_source.customers SET customer_id = DEFAULT",
        "UPDATE store_source.orders SET completed_at = completed_at + interval '1 day' "
        "WHERE order_status = 'completed'",
    ],
)
def test_source_mutation_guards(source_db, statement):
    load_fixture(source_db)
    with pytest.raises(psycopg.IntegrityError):
        source_db.execute(statement)


def test_empty_order_cannot_complete_and_completed_order_cannot_gain_lines(source_db):
    orders, _ = load_fixture(source_db)
    customer = source_db.execute(
        "SELECT customer_id FROM store_source.customers LIMIT 1"
    ).fetchone()[0]
    empty_order = source_db.execute(
        "INSERT INTO store_source.orders(customer_id, order_status) VALUES (%s, 'pending') "
        "RETURNING order_id",
        (customer,),
    ).fetchone()[0]
    with pytest.raises(psycopg.IntegrityError):
        source_db.execute(
            "UPDATE store_source.orders SET order_status='completed', "
            "completed_at=clock_timestamp() "
            "WHERE order_id=%s",
            (empty_order,),
        )
    with pytest.raises(psycopg.IntegrityError):
        source_db.execute(
            "INSERT INTO store_source.order_items(order_id, product_id, quantity, unit_price_rial) "
            "SELECT %s, product_id, 1, price_rial FROM store_source.products LIMIT 1",
            (orders["completed_discount"],),
        )


def test_catalog_price_changes_do_not_change_sales_and_advance_watermark(source_db):
    load_fixture(source_db)
    before = source_db.execute(
        "SELECT sum(line_sales_rial) FROM store_source.order_items"
    ).fetchone()
    product, old_time = source_db.execute(
        "SELECT product_id, updated_at FROM store_source.products LIMIT 1"
    ).fetchone()
    new_time = source_db.execute(
        "UPDATE store_source.products SET price_rial = price_rial + 1000 "
        "WHERE product_id = %s RETURNING updated_at",
        (product,),
    ).fetchone()[0]
    assert new_time > old_time
    assert (
        source_db.execute("SELECT sum(line_sales_rial) FROM store_source.order_items").fetchone()
        == before
    )


def test_untracked_existing_source_is_left_intact(empty_source):
    empty_source.execute("CREATE SCHEMA store_source")
    empty_source.execute("CREATE TABLE store_source.keep_me(value integer)")
    empty_source.execute("INSERT INTO store_source.keep_me VALUES (42)")
    with pytest.raises(source_ops.SourceMigrationError, match="without migration history"):
        source_ops.run_source_migrations()
    assert empty_source.execute("SELECT * FROM store_source.keep_me").fetchall() == [(42,)]
    assert (
        empty_source.execute("SELECT to_regclass('store_source.schema_migrations')").fetchone()[0]
        is None
    )


def failing_migrations(tmp_path, monkeypatch):
    initial = source_ops.MIGRATIONS_DIR / "001_initial_source_schema.sql"
    (tmp_path / initial.name).write_text(initial.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "002_failure.sql").write_text(
        "CREATE TABLE store_source.should_rollback(id int); SELECT 1 / 0;", encoding="utf-8"
    )
    monkeypatch.setattr(source_ops, "MIGRATIONS_DIR", tmp_path)


def test_failed_migration_rolls_back_schema_and_ledger(empty_source, tmp_path, monkeypatch):
    failing_migrations(tmp_path, monkeypatch)
    with pytest.raises(psycopg.errors.DivisionByZero):
        source_ops.run_source_migrations()
    assert empty_source.execute("SELECT to_regnamespace('store_source')").fetchone()[0] is None


def test_failed_reset_restores_previous_source(source_db, tmp_path, monkeypatch):
    load_fixture(source_db)
    failing_migrations(tmp_path, monkeypatch)
    with pytest.raises(psycopg.errors.DivisionByZero):
        source_ops.reset_source(confirmation=source_ops.RESET_CONFIRMATION)
    assert source_db.execute("SELECT count(*) FROM store_source.orders").fetchone()[0] == 4
    assert (
        source_db.execute("SELECT count(*) FROM store_source.schema_migrations").fetchone()[0] == 1
    )


def test_reset_removes_only_source_schema(source_db):
    load_fixture(source_db)
    source_db.execute("CREATE TABLE public.reset_sentinel(id integer)")
    try:
        source_ops.reset_source(confirmation=source_ops.RESET_CONFIRMATION)
        assert source_db.execute("SELECT count(*) FROM store_source.orders").fetchone()[0] == 0
        assert source_db.execute("SELECT to_regclass('public.reset_sentinel')").fetchone()[0]
        assert (
            source_db.execute("SELECT count(*) FROM store_source.schema_migrations").fetchone()[0]
            == 1
        )
    finally:
        source_db.execute("DROP TABLE public.reset_sentinel")


def test_reset_refuses_other_database(empty_source, postgres_cluster, monkeypatch):
    monkeypatch.setenv("STORE_DW_SOURCE_DSN", postgres_cluster["other_dsn"])
    with pytest.raises(source_ops.SourceMigrationError, match="only supports"):
        source_ops.reset_source(confirmation=source_ops.RESET_CONFIRMATION)


def test_doctor_against_real_databases(source_db, capsys):
    assert cli.main(["doctor"]) == 0
    assert "Warehouse database: OK" in capsys.readouterr().out


def test_psql_reset_and_cli_migration_share_history(source_db, postgres_cluster):
    load_fixture(source_db)
    params = conninfo_to_dict(postgres_cluster["dsn"])
    params.pop("password")
    script = Path(__file__).resolve().parents[1] / "migrations/source/reset_dev_source.sql"
    result = subprocess.run(
        [postgres_cluster["psql"], "-X", "-w", "-d", make_conninfo(**params), "-f", str(script)],
        input=source_ops.RESET_CONFIRMATION + "\n",
        capture_output=True,
        text=True,
        env=postgres_cluster["env"],
        timeout=15,
        **postgres_cluster["flags"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    source_ops.run_source_migrations()
    assert (
        source_db.execute("SELECT count(*) FROM store_source.schema_migrations").fetchone()[0] == 1
    )
    assert source_db.execute("SELECT count(*) FROM store_source.orders").fetchone()[0] == 0


def test_terminal_workflow_from_outside_repository(empty_source, postgres_cluster, tmp_path):
    """Exercise installed commands and script entry points, not only imported functions."""
    project = Path(__file__).resolve().parents[1]
    executable = Path(sys.executable).parent / ("store-dw.exe" if os.name == "nt" else "store-dw")
    env = {
        **postgres_cluster["env"],
        "STORE_DW_SOURCE_DSN": postgres_cluster["dsn"],
        "STORE_DW_WAREHOUSE_DSN": postgres_cluster["other_dsn"],
        "STORE_DW_CONNECT_TIMEOUT_SECONDS": "5",
        "PYTHONUTF8": "1",
    }

    def run(args, *, input_text="", expected_code=0):
        result = subprocess.run(
            args, input=input_text, capture_output=True, text=True, encoding="utf-8",
            cwd=tmp_path, env=env, timeout=20, **postgres_cluster["flags"],
        )
        assert result.returncode == expected_code, result.stdout + result.stderr
        return result.stdout + result.stderr

    assert "Configuration: OK" in run([str(executable), "doctor"])
    run([str(executable), "migrate-source"])
    output = run([sys.executable, str(project / "simulators/seed_store.py")])
    assert "Database checks passed: 9,900,000 IRR sales" in output
    run([str(executable), "migrate-source"])
    assert empty_source.execute("SELECT count(*) FROM store_source.orders").fetchone()[0] == 4

    # A cancelled reset must keep the just-loaded fixture.
    run([str(executable), "reset-source"], input_text="cancel\n", expected_code=1)
    assert empty_source.execute("SELECT count(*) FROM store_source.orders").fetchone()[0] == 4
    run([str(executable), "reset-source"], input_text=source_ops.RESET_CONFIRMATION + "\n")
    assert empty_source.execute("SELECT count(*) FROM store_source.orders").fetchone()[0] == 0

    # The documented default batch checks both its fingerprint and committed totals.
    output = run([
        sys.executable, str(project / "simulators/generate_store.py"), "--load",
    ])
    assert "1f05d0f63df5c127930d00927c184560c6377936a8f2159f9035b92a3899ceeb" in output
    assert "Database checks passed: 1000 orders, 63,985,710,500 IRR sales" in output
    assert empty_source.execute("SELECT count(*) FROM store_source.orders").fetchone()[0] == 1000

    for placeholder in ("migrate", "run"):
        assert "not implemented yet" in run([str(executable), placeholder], expected_code=2)
