from unittest.mock import Mock

import psycopg
import pytest

from store_dw import cli
from store_dw.config import ConfigError, Settings


@pytest.fixture
def settings():
    return Settings(
        source_dsn="host=localhost dbname=store_source",
        warehouse_dsn="host=localhost dbname=store_warehouse",
        connect_timeout_seconds=5,
    )


def test_doctor_checks_both_databases(monkeypatch, capsys, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    check = Mock()
    monkeypatch.setattr(cli, "check_connection", check)

    assert cli.main(["doctor"]) == 0

    assert check.call_count == 2
    check.assert_any_call(settings.source_dsn, timeout=5)
    check.assert_any_call(settings.warehouse_dsn, timeout=5)

    output = capsys.readouterr()
    assert "Configuration: OK" in output.out
    assert "Source database: OK" in output.out
    assert "Warehouse database: OK" in output.out
    assert output.err == ""


def test_doctor_stops_on_invalid_configuration(monkeypatch, capsys):
    load = Mock(side_effect=ConfigError("STORE_DW_SOURCE_DSN is required."))
    check = Mock()
    monkeypatch.setattr(cli, "load_settings", load)
    monkeypatch.setattr(cli, "check_connection", check)

    assert cli.main(["doctor"]) == 1

    check.assert_not_called()
    assert "Configuration error:" in capsys.readouterr().err


def test_doctor_checks_warehouse_after_source_failure(monkeypatch, capsys, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    check = Mock(
        side_effect=[
            psycopg.OperationalError("sensitive-connection-details"),
            None,
        ]
    )
    monkeypatch.setattr(cli, "check_connection", check)

    assert cli.main(["doctor"]) == 1
    assert check.call_count == 2

    output = capsys.readouterr()
    assert "Source database: FAILED" in output.err
    assert "Warehouse database: OK" in output.out
    assert "sensitive-connection-details" not in output.out + output.err


@pytest.mark.parametrize("command", ["migrate", "run"])
def test_unimplemented_commands_fail_explicitly(command, capsys):
    assert cli.main([command]) == 2
    assert "not implemented yet" in capsys.readouterr().err

def test_reset_cli_passes_typed_confirmation(monkeypatch):
    reset = Mock()
    monkeypatch.setattr("builtins.input", lambda _: cli.RESET_CONFIRMATION)
    monkeypatch.setattr(cli, "do_reset_source", reset)
    assert cli.main(["reset-source"]) == 0
    reset.assert_called_once_with(confirmation=cli.RESET_CONFIRMATION)


def test_reset_cli_cancels_on_eof(monkeypatch):
    monkeypatch.setattr("builtins.input", Mock(side_effect=EOFError))
    reset = Mock()
    monkeypatch.setattr(cli, "do_reset_source", reset)
    assert cli.main(["reset-source"]) == 1
    reset.assert_not_called()


def test_migration_database_errors_do_not_expose_credentials(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "run_source_migrations", Mock(side_effect=psycopg.OperationalError("secret-password"))
    )
    assert cli.main(["migrate-source"]) == 1
    assert "secret-password" not in capsys.readouterr().err