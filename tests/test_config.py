import pytest

from store_dw.config import ConfigError, load_settings


@pytest.fixture
def valid_env():
    return {
        "STORE_DW_SOURCE_DSN": (
            "host=localhost dbname=store_source user=store_app password=test-secret"
        ),
        "STORE_DW_WAREHOUSE_DSN": (
            "host=localhost dbname=store_warehouse user=warehouse_app password=test-secret"
        ),
    }


def test_loads_valid_settings(valid_env):
    settings = load_settings(valid_env)

    assert settings.source_dsn == valid_env["STORE_DW_SOURCE_DSN"]
    assert settings.warehouse_dsn == valid_env["STORE_DW_WAREHOUSE_DSN"]
    assert settings.connect_timeout_seconds == 5


def test_loads_explicit_timeout(valid_env):
    valid_env["STORE_DW_CONNECT_TIMEOUT_SECONDS"] = "10"

    assert load_settings(valid_env).connect_timeout_seconds == 10


@pytest.mark.parametrize(
    "name",
    ["STORE_DW_SOURCE_DSN", "STORE_DW_WAREHOUSE_DSN"],
)
@pytest.mark.parametrize("value", [None, "", "   "])
def test_rejects_missing_or_blank_dsn(valid_env, name, value):
    if value is None:
        valid_env.pop(name)
    else:
        valid_env[name] = value

    with pytest.raises(ConfigError, match=name):
        load_settings(valid_env)


def test_rejects_invalid_dsn_without_exposing_it(valid_env):
    valid_env["STORE_DW_SOURCE_DSN"] = "not-a-dsn-secret"

    with pytest.raises(ConfigError) as exc_info:
        load_settings(valid_env)

    assert "STORE_DW_SOURCE_DSN" in str(exc_info.value)
    assert "not-a-dsn-secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("dsn", "missing_parameter"),
    [
        ("dbname=store_source", "host"),
        ("host=localhost", "dbname"),
    ],
)
def test_requires_explicit_database_target(valid_env, dsn, missing_parameter):
    valid_env["STORE_DW_SOURCE_DSN"] = dsn

    with pytest.raises(ConfigError, match=missing_parameter):
        load_settings(valid_env)


@pytest.mark.parametrize("value", ["", "abc", "1.5", "0", "-1"])
def test_rejects_invalid_timeout(valid_env, value):
    valid_env["STORE_DW_CONNECT_TIMEOUT_SECONDS"] = value

    with pytest.raises(
        ConfigError,
        match="STORE_DW_CONNECT_TIMEOUT_SECONDS",
    ):
        load_settings(valid_env)


def test_settings_repr_hides_connection_strings(valid_env):
    settings = load_settings(valid_env)

    assert "test-secret" not in repr(settings)
    assert settings.source_dsn not in repr(settings)
    assert settings.warehouse_dsn not in repr(settings)


def test_reads_process_environment(monkeypatch, valid_env):
    for name, value in valid_env.items():
        monkeypatch.setenv(name, value)

    monkeypatch.delenv("STORE_DW_CONNECT_TIMEOUT_SECONDS", raising=False)

    settings = load_settings()

    assert settings.source_dsn == valid_env["STORE_DW_SOURCE_DSN"]
    assert settings.connect_timeout_seconds == 5
