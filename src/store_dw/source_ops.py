from pathlib import Path
import psycopg
from store_dw.config import load_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "source"

def run_source_migrations() -> None:
    """Executes all .sql files in migrations/source in alphabetical order."""
    settings = load_settings()
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    with psycopg.connect(settings.source_dsn) as conn:
        with conn.cursor() as cur:
            for sql_file in sql_files:
                sql_content = sql_file.read_text(encoding="utf-8")
                cur.execute(sql_content)
        conn.commit()

def reset_source() -> None:
    """Wipes all public tables in the source DB and applies migrations fresh."""
    settings = load_settings()
    with psycopg.connect(settings.source_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DROP TABLE IF EXISTS order_items CASCADE;
                DROP TABLE IF EXISTS orders CASCADE;
                DROP TABLE IF EXISTS products CASCADE;
                DROP TABLE IF EXISTS customers CASCADE;
            """)
        conn.commit()
    run_source_migrations()