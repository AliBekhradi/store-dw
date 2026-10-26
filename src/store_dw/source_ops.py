"""Apply numbered source migrations and explicitly reset the development source."""

from pathlib import Path

import psycopg

from store_dw.config import load_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "source"
DEV_SOURCE_DATABASE = "undead_schema_test"
RESET_CONFIRMATION = f"RESET {DEV_SOURCE_DATABASE}"


class SourceMigrationError(RuntimeError):
    """Source setup cannot safely proceed."""


def _migration_files() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise SourceMigrationError("No numbered source migrations found in migrations/source.")
    versions = [path.name[:3] for path in files]
    if len(versions) != len(set(versions)):
        raise SourceMigrationError("Source migration numbers must be unique.")
    return files


def _apply_migrations(conn: psycopg.Connection, files: list[Path]) -> None:
    # The caller owns the transaction: schema changes and their ledger commit together.
    history_exists = conn.execute(
        "SELECT to_regclass('store_source.schema_migrations') IS NOT NULL"
    ).fetchone()[0]
    if not history_exists:
        existing_tables = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'store_source')"
        ).fetchone()[0]
        if existing_tables:
            raise SourceMigrationError(
                "Source tables already exist without migration history. "
                "No changes applied. See README: existing source databases."
            )

    conn.execute("CREATE SCHEMA IF NOT EXISTS store_source")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS store_source.schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    applied = {row[0] for row in conn.execute("SELECT version FROM store_source.schema_migrations")}
    available = {path.name for path in files}
    if applied - available:
        raise SourceMigrationError("Applied source migration files are missing or renamed.")
    for path in files:
        if path.name in applied:
            continue
        if applied and path.name < max(applied):
            raise SourceMigrationError("New source migrations must follow applied versions.")
        conn.execute(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO store_source.schema_migrations (version) VALUES (%s)", (path.name,)
        )


def run_source_migrations() -> None:
    """Apply pending NNN_description.sql files once, in one transaction."""
    files = _migration_files()
    settings = load_settings()
    with psycopg.connect(
        settings.source_dsn, connect_timeout=settings.connect_timeout_seconds
    ) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('store_dw.source_migrations'))")
        _apply_migrations(conn, files)


def reset_source(*, confirmation: str) -> None:
    """Reset only the explicitly confirmed development database's source schema."""
    if confirmation != RESET_CONFIRMATION:
        raise SourceMigrationError("Reset cancelled: confirmation did not match.")
    files = _migration_files()
    settings = load_settings()
    with psycopg.connect(
        settings.source_dsn, connect_timeout=settings.connect_timeout_seconds
    ) as conn:
        database = conn.execute("SELECT current_database()").fetchone()[0]
        if database != DEV_SOURCE_DATABASE:
            raise SourceMigrationError(f"Reset only supports {DEV_SOURCE_DATABASE}.")
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('store_dw.source_migrations'))")
        conn.execute("DROP SCHEMA IF EXISTS store_source CASCADE")
        _apply_migrations(conn, files)
