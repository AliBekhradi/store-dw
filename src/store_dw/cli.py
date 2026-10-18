"""Command-line entry point."""

import argparse
import sys

import psycopg

from store_dw.config import ConfigError, load_settings
from store_dw.db import check_connection


def doctor() -> int:
    try:
        settings = load_settings()
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 1

    print("Configuration: OK")
    failed = False

    for name, dsn in [
        ("Source database", settings.source_dsn),
        ("Warehouse database", settings.warehouse_dsn),
    ]:
        try:
            check_connection(
                dsn,
                timeout=settings.connect_timeout_seconds,
            )
        except psycopg.Error:
            # Connection errors can contain sensitive connection details.
            print(
                f"{name}: FAILED — check server, database, and credentials.",
                file=sys.stderr,
            )
            failed = True
        else:
            print(f"{name}: OK")

    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="store-dw",
        description="Project Undead: store warehouse application.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="Check configuration and databases.")
    commands.add_parser("migrate", help="Apply migrations (not implemented yet).")
    commands.add_parser("run", help="Run the pipeline (not implemented yet).")

    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor()

    print(
        f"{args.command}: not implemented yet.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())