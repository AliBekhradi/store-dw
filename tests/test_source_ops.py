from unittest.mock import Mock

import pytest

from store_dw import source_ops
from store_dw.source_ops import SourceMigrationError


def test_migration_discovery_finds_initial_schema_not_reset():
    assert [path.name for path in source_ops._migration_files()] == [
        "001_initial_source_schema.sql"
    ]


def test_missing_migrations_fail_before_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(source_ops, "MIGRATIONS_DIR", tmp_path)
    connect = Mock()
    monkeypatch.setattr(source_ops.psycopg, "connect", connect)
    with pytest.raises(SourceMigrationError, match="No numbered"):
        source_ops.run_source_migrations()
    connect.assert_not_called()


def test_duplicate_migration_numbers_fail(tmp_path, monkeypatch):
    for name in ("001_first.sql", "001_second.sql"):
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(source_ops, "MIGRATIONS_DIR", tmp_path)
    with pytest.raises(SourceMigrationError, match="unique"):
        source_ops._migration_files()


def test_reset_requires_exact_confirmation_before_connection(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(source_ops.psycopg, "connect", connect)
    with pytest.raises(SourceMigrationError, match="confirmation"):
        source_ops.reset_source(confirmation="yes")
    connect.assert_not_called()
