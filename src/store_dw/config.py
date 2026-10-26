"""Load and validate application configuration from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict


class ConfigError(ValueError):
    """Application configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    source_dsn: str = field(repr=False)
    warehouse_dsn: str = field(repr=False)
    connect_timeout_seconds: int = 5


def _read_dsn(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()

    if not value:
        raise ConfigError(f"{name} is required.")

    try:
        parameters = conninfo_to_dict(value)
    except ProgrammingError:
        # Do not expose a DSN or a parser message that could contain credentials.
        raise ConfigError(f"{name} must be a valid PostgreSQL connection string.") from None

    # Require an explicit target instead of silently using libpq defaults.
    for parameter in ("host", "dbname"):
        if not parameters.get(parameter, "").strip():
            raise ConfigError(f"{name} must explicitly specify {parameter}.")

    return value


def load_source_dsn(env: Mapping[str, str] | None = None) -> str:
    """Validate source configuration for tools that do not need the warehouse."""
    return _read_dsn(os.environ if env is None else env, "STORE_DW_SOURCE_DSN")


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Read settings, optionally from an injected mapping for tests."""
    if env is None:
        env = os.environ

    source_dsn = _read_dsn(env, "STORE_DW_SOURCE_DSN")
    warehouse_dsn = _read_dsn(env, "STORE_DW_WAREHOUSE_DSN")

    raw_timeout = env.get("STORE_DW_CONNECT_TIMEOUT_SECONDS", "5")

    try:
        timeout = int(raw_timeout)
    except ValueError:
        raise ConfigError("STORE_DW_CONNECT_TIMEOUT_SECONDS must be a positive integer.") from None

    if timeout <= 0:
        raise ConfigError("STORE_DW_CONNECT_TIMEOUT_SECONDS must be a positive integer.")

    return Settings(
        source_dsn=source_dsn,
        warehouse_dsn=warehouse_dsn,
        connect_timeout_seconds=timeout,
    )
