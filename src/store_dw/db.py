"""Database connectivity helpers."""

import psycopg


def check_connection(dsn: str, *, timeout: int) -> None:
    """Raise psycopg.Error if the database cannot be queried."""
    with psycopg.connect(
        dsn,
        connect_timeout=timeout,
        autocommit=True,
    ) as connection:
        connection.execute("SELECT 1").fetchone()
