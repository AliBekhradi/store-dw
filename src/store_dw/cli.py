"""Command-line entry point."""

import argparse
import sys

import psycopg

from store_dw.config import ConfigError, load_settings
from store_dw.db import check_connection
from store_dw.source_ops import RESET_CONFIRMATION, SourceMigrationError, run_source_migrations
from store_dw.source_ops import reset_source as do_reset_source


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
            print(
                f"{name}: FAILED — check server, database, and credentials.",
                file=sys.stderr,
            )
            failed = True
        else:
            print(f"{name}: OK")

    return 1 if failed else 0


def migrate_source() -> int:
    try:
        run_source_migrations()
        print("Source migrations applied successfully.")
        return 0
    except (ConfigError, SourceMigrationError) as error:
        print(f"Migration error: {error}", file=sys.stderr)
        return 1
    except psycopg.Error:
        print(
            "Migration failed; changes rolled back. Check database access and SQL.", file=sys.stderr
        )
        return 1


def reset_source() -> int:
    try:
        confirmation = input(f"This destroys the development source. Type {RESET_CONFIRMATION}: ")
        do_reset_source(confirmation=confirmation)
        print("Source database reset and schema recreated.")
        return 0
    except (ConfigError, SourceMigrationError) as error:
        print(f"Reset error: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Reset cancelled.", file=sys.stderr)
        return 1
    except psycopg.Error:
        print("Reset failed; changes rolled back. Check database access and SQL.", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="store-dw",
        description="Project Undead: store warehouse application.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="Check configuration and databases.")
    commands.add_parser("migrate-source", help="Apply source operational migrations.")
    commands.add_parser("reset-source", help="Wipe and recreate source operational schema.")
    commands.add_parser("migrate", help="Apply warehouse migrations (not implemented yet).")
    commands.add_parser("run", help="Run the pipeline (not implemented yet).")

    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor()
    if args.command == "migrate-source":
        return migrate_source()
    if args.command == "reset-source":
        return reset_source()

    print(
        f"{args.command}: not implemented yet.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())