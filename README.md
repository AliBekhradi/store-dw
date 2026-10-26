# Project Undead

A reproducible fictional store and the foundation for a batch data warehouse.
The current implementation covers phases 0 and 1: contracts, Python tooling,
the operational PostgreSQL schema, and deterministic store generators.

## Current scope

```text
seed_store.py / generate_store.py
                 |
                 v
source database: store_source schema
customers -> orders -> order_items <- products
```

Only completed orders count as sales. Money is integer rial; sales use the
quantity, historical price, and whole-line discount on each order line.
Business dates use Asia/Tehran. Payments, refunds, warehouse extraction and
loading, reports, Airflow, and Metabase remain future phases.

Read [business rules](docs/business_rules.md) for meaning and
[source contracts](docs/source_contracts.md) for fields and source behavior.
The [Phase 1 finishing notes](docs/phase_1_finishing_notes.md) explain the setup
repairs, naming choices, tests, and remaining limits.

## Install and configure (PowerShell)

Prerequisites: Python 3.13+ and PostgreSQL. Put PostgreSQL's `bin` directory on
PATH for `psql`, `createdb`, `initdb`, and `pg_ctl`. The isolated integration
tests require the server binaries, not a running preconfigured server.

From this repository:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Copy `.env.example` only when you do not already have `.env`. Edit the example
values for your local setup. `.env` is ignored by Git; `.env.example` contains
no credentials. Do not put real passwords in committed files.

The application reads the **process environment**. It does not automatically
parse `.env`. The existing VS Code Python-terminal setting can supply `.env`
values; reopen the Python terminal after editing them. For ordinary PowerShell,
set the same variables explicitly:

```powershell
$env:STORE_DW_SOURCE_DSN = 'host=localhost port=5432 dbname=undead_schema_test user=postgres'
$env:STORE_DW_WAREHOUSE_DSN = 'host=localhost port=5432 dbname=store_warehouse user=postgres'
$env:STORE_DW_CONNECT_TIMEOUT_SECONDS = '5'
```

Supply authentication through your PostgreSQL configuration, a connection-string
password, or `PGPASSWORD`. To supply a password without typing it visibly:

```powershell
$credential = Get-Credential -UserName postgres -Message 'Local PostgreSQL credentials'
$env:PGPASSWORD = $credential.GetNetworkCredential().Password
```

This example uses one local PostgreSQL user/password for both databases. DSNs
can specify different users/passwords when needed. The simulators prompt for a
password only if neither their DSN nor `PGPASSWORD` supplies one.

If the two databases do not yet exist, create them using your local admin role:

```powershell
createdb -h localhost -U postgres undead_schema_test
createdb -h localhost -U postgres store_warehouse
```

These are databases, not table schemas. Source tables live in the `store_source`
schema inside the source database. `undead_schema_test` is the existing
development-reset database name; it is not a required production database name.
The warehouse database can be empty at this phase.

## Commands

Use the installed executable without activating the virtual environment:

```powershell
.\.venv\Scripts\store-dw.exe doctor
.\.venv\Scripts\store-dw.exe migrate-source
.\.venv\Scripts\python.exe simulators/seed_store.py
.\.venv\Scripts\python.exe simulators/generate_store.py --seed 42
```

`doctor` checks configuration and both database connections. `migrate-source`
applies pending numbered migrations. The tiny fixture inserts data and checks
its database totals before committing. The larger generator previews by default;
add `--load` to insert it and verify database results.

The application and simulators use `STORE_DW_SOURCE_DSN`. Simulators still accept
the old `STORE_DATABASE_DSN` when the standard name is absent, with a notice.
The standard name takes precedence. Source generators do not need warehouse
configuration. Existing CLI source commands still load the application settings.

The tiny fixture has 2 customers, 3 products, 4 orders, and 5 order lines.
Expected **completed sales: 9,900,000 rial**. Pending/cancelled basket values do
not contribute. Fixed business dates are January 15, 2025.

Default bulk generation produces 100 customers, 50 products, 1,000 orders,
3,117 lines, and 63,985,710,500 rial in completed sales. Its SHA-256 fingerprint is:

```text
1f05d0f63df5c127930d00927c184560c6377936a8f2159f9035b92a3899ceeb
```

Identical generator inputs produce identical business payloads. Database identity
values and operational timestamps are not part of that guarantee. The generators
insert records; repeated loading of the same fixture/batch is not an upsert.
Use a clean development source or a new bulk `--batch` name as appropriate.

`store-dw migrate` and `store-dw run` remain explicit placeholders returning exit
code 2. They do not migrate or run a warehouse yet.

## Migrations and development reset

Numbered files follow the repository's `NNN_description.sql` pattern, starting
with `001_initial_source_schema.sql`. The runner ignores unnumbered helper files
such as `reset_dev_source.sql`. It fails if it finds no numbered migrations.

`store_source.schema_migrations` records each applied filename and its timestamp.
Repeated migration commands skip recorded files. New files must follow applied
numbers; duplicate numbers and missing/renamed applied files are rejected.
Do not rewrite an applied migration: add the next numbered file instead.
There is no content-checksum or downgrade mechanism in this small runner.

The runner owns the transaction. Migration SQL must not contain top-level
BEGIN/COMMIT commands. Schema changes and ledger entries commit together;
failures roll back the pending migration batch. A transaction-scoped advisory
lock serializes source migration/reset commands. It is not the future pipeline
concurrency mechanism.

Initialization requires the database owner or an appropriately privileged role,
because it also configures the database's default timezone. Application roles
should not own tables or bypass their guards, as documented in the schema.

For an intentional development reset:

```powershell
.\.venv\Scripts\store-dw.exe reset-source
```

It accepts only the actual connected database named `undead_schema_test` and
requires typing `RESET undead_schema_test`. It drops `store_source` with CASCADE
and recreates it with its migration history. Objects that depend on that schema
can also be removed by CASCADE. Unrelated schemas are not targeted.
If rebuilding fails, the transaction restores the previous state.

The existing standalone psql helper remains available:

```powershell
psql -X -h localhost -U postgres -d undead_schema_test -f migrations/source/reset_dev_source.sql
```

It retains the same database/typed-confirmation checks and rebuilds the current
initial schema with a matching ledger entry. Prefer the CLI when more numbered
migrations are added: the helper explicitly includes only the initial migration.
Do not pass the psql helper to Psycopg; its backslash commands belong to psql.

### Existing source databases

A database initialized before migration tracking may already contain source
tables without `schema_migrations`. The runner deliberately leaves it unchanged
and reports this situation. It cannot infer whether those tables match today's
schema. Do not insert a history entry merely to silence the error.

Keep valuable existing data. To try the reproducible setup, point the source DSN
at a separate empty database. If the existing `undead_schema_test` contents are
disposable, an explicitly confirmed development reset recreates it with history.
Preserving and adopting an older populated schema requires a separately reviewed
baseline/upgrade; this patch does not silently perform one.

## Tests

Fast tests require no database server:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pgdata/pytest-tmp
.\.venv\Scripts\python.exe -m ruff check .
```

Include real PostgreSQL tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --run-postgres --basetemp=.pgdata/pytest-tmp
```

The opt-in suite starts its own cluster under ignored `.pgdata/`, chooses a
loopback port, uses a random password, and creates its own `undead_schema_test`.
It overrides connection settings for tests and never uses your configured
source/warehouse databases. It stops and removes its cluster after the session.
Do not run two test sessions with the same `--basetemp` directory concurrently.

Coverage includes source totals and reporting dates, immutability/lifecycle
guards, migration reruns, reset restrictions, preservation of untracked schemas,
and rollback after injected migration/reset failures. Integration tests are
reported as skipped unless `--run-postgres` is supplied.

## Repository map

- `src/store_dw/`: configuration, connection checking, CLI, source operations.
- `migrations/source/`: initial source schema and the explicit psql reset helper.
- `simulators/`: tiny fixture, larger generator, shared insert/verification logic.
- `tests/`: configuration, CLI, simulator, migration and PostgreSQL tests.
- `docs/`: business definitions, source contracts, and change explanations.

Runtime dependencies remain the existing Psycopg and HTTPX; development tools
remain pytest and Ruff. Their declarations are minimum versions, not a locked
environment. No warehouse-phase functionality is introduced by these repairs.
