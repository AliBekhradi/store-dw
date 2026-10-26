import random

import pytest

from simulators import generate_store, seed_store
from store_dw.config import ConfigError


@pytest.fixture
def simulator_env(monkeypatch):
    for name in ("STORE_DW_SOURCE_DSN", "STORE_DATABASE_DSN", "PGPASSWORD"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_simulator_prefers_application_dsn(simulator_env):
    simulator_env.setenv("STORE_DW_SOURCE_DSN", "host=localhost dbname=canonical password=test")
    simulator_env.setenv("STORE_DATABASE_DSN", "host=localhost dbname=legacy")
    assert "dbname=canonical" in seed_store.source_connection_kwargs()["conninfo"]


def test_simulator_preserves_legacy_dsn_and_password_prompt(simulator_env, capsys):
    simulator_env.setenv("STORE_DATABASE_DSN", "host=localhost dbname=legacy")
    simulator_env.setattr(seed_store, "getpass", lambda _: "prompted-password")
    kwargs = seed_store.source_connection_kwargs()
    assert "dbname=legacy" in kwargs["conninfo"]
    assert kwargs["password"] == "prompted-password"
    assert "legacy alias" in capsys.readouterr().err


def test_simulator_honors_pgpassword(simulator_env):
    simulator_env.setenv("STORE_DW_SOURCE_DSN", "host=localhost dbname=source")
    simulator_env.setenv("PGPASSWORD", "test-password")
    assert "password" not in seed_store.source_connection_kwargs()


def test_simulator_rejects_invalid_canonical_dsn_instead_of_falling_back(simulator_env):
    simulator_env.setenv("STORE_DW_SOURCE_DSN", "")
    simulator_env.setenv("STORE_DATABASE_DSN", "host=localhost dbname=legacy")
    with pytest.raises(ConfigError, match="STORE_DW_SOURCE_DSN"):
        seed_store.source_connection_kwargs()


def test_tiny_fixture_has_independently_expected_sales():
    orders = seed_store.build_orders()
    assert len(orders) == 4
    assert sum(len(order["items"]) for order in orders) == 5
    seed_store.check_order_scenarios(orders)


def test_seeded_generation_is_repeatable():
    def generate(seed):
        rng = random.Random(seed)
        customers = generate_store.generate_customers(10, "test")
        products = generate_store.generate_products(8, "test", rng)
        return products, generate_store.generate_orders(30, "test", customers, products, rng)

    assert generate(42) == generate(42)
    assert generate(42) != generate(43)
