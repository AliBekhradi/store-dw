# Phase 1 finishing changes

This records the repairs following the Rewind day review of commit `fe4301d`
("Phase 1 complete"). It explains the whole patch, including work resumed after
the interrupted editing session. The patch repairs setup and verification;
the four store entities, their business rules, and generated business data remain
the same. No existing developer database is migrated, reset, or seeded by this work.

## Migration discovery and history

The old migration path went one directory above the repository. As a result,
`migrate-source` could find zero SQL files and still report success. The path now
resolves to this repository's `migrations/source`, independent of the terminal's
working directory. An empty migration set is an error.

Discovery selects `NNN_description.sql`, preserving the existing name
`001_initial_source_schema.sql`. This also excludes `reset_dev_source.sql`, which
contains psql-specific backslash commands rather than just server SQL.
Duplicate migration numbers are rejected.

The new `store_source.schema_migrations` table records the applied filename and
`applied_at`. It is setup metadata, not a fifth business entity. A rerun skips
recorded files and preserves the source data. Missing/renamed applied files and
new migrations inserted before applied versions are rejected.

There is no checksum-based drift detector or downgrade framework. After adoption,
keep applied migrations unchanged and add the next numbered migration for a
schema change. These small mechanisms are sufficient for the current source.

## Transactions and resets

Previously the initial SQL file committed its own transaction. A runner could
not atomically combine that commit with recording migration history. Its only
schema-file edits are removal of the outer BEGIN/COMMIT and explanatory comments.
Table definitions, constraints, generated expressions, indexes and triggers are
unchanged. The Python connection context now commits all pending migration SQL
and its history together, or rolls them all back.

The CLI reset previously dropped unqualified table names, committed the drops,
and then called the broken migration runner. It now drops the explicitly named
`store_source` schema and rebuilds it in one transaction. If rebuilding fails,
the old source remains intact.

Reset follows the existing psql helper's policy: verify the actual connected
database is `undead_schema_test` and require the exact phrase
`RESET undead_schema_test`. This database name comes from the project; it is not
a general PostgreSQL naming standard. CASCADE can remove objects depending on
the source schema, so reset remains an intentional development operation.

The psql reset helper retains its own checks and prompt. It now wraps drop,
rebuild, and history insertion in a transaction. It explicitly rebuilds migration
001; the CLI discovers all numbered migrations. Both commands take the same
transaction-scoped advisory lock so source setup operations serialize.

An existing populated schema without migration history is preserved and reported
as unmanaged. The runner does not guess that its structure matches migration 001
and does not create a misleading history record. The README explains using a
separate empty source, deliberately resetting disposable development data, or
reviewing a data-preserving baseline separately.

## Configuration and simulator compatibility

The project already uses `STORE_DW_SOURCE_DSN`; the simulators used the different
name `STORE_DATABASE_DSN`. Both simulators now share `source_connection_kwargs()`
in `seed_store.py`, alongside their existing shared insert function.

The standard project variable takes precedence. The original simulator name is
still accepted when the standard name is absent, and prints a migration notice.
An explicitly invalid standard variable fails validation rather than silently
connecting with the fallback. `load_source_dsn()` reuses the existing DSN validator
without requiring unrelated warehouse configuration for source generators.

A password supplied in the DSN or through `PGPASSWORD` is honored. Otherwise the
original interactive password prompt is retained. Application configuration still
comes from process environment variables; no automatic .env parser or new runtime
dependency has been added. README examples explain ordinary PowerShell and the
existing VS Code environment-file setting.

The existing application Settings loader and doctor behavior are preserved.
Source CLI commands still load the application's source and warehouse settings.
The actual `.env` file and its credentials are untouched.

## CLI behavior and lint

The CLI passes the typed reset confirmation to the reset helper, handles EOF or
Ctrl+C as cancellation, and reports anticipated configuration/migration failures.
Database exceptions receive a generic failure message rather than printing a raw
connection error that could contain credentials. Original doctor checks and the
explicitly unimplemented warehouse commands remain unchanged.

Import ordering and the `datetime.UTC` spelling address the repository's existing
Ruff rules. UTC behavior, dates, quantities, prices, discounts and random choices
are unchanged. Broader formatter changes to existing simulator bodies were
removed to keep the patch focused.

## Naming choices

| Name | Basis |
| --- | --- |
| `STORE_DW_SOURCE_DSN` | Existing application convention: uppercase environment name with project prefix |
| `NNN_description.sql` | Existing numbered migration convention; zero padding preserves lexical order |
| `schema_migrations`, `version`, `applied_at` | Descriptive lowercase SQL identifiers consistent with this schema |
| `load_source_dsn`, `source_connection_kwargs` | Python snake_case, describing the returned value |
| `.env.example` | Placeholder file explicitly requested by the roadmap |
| `test_*.py`, `conftest.py` | pytest discovery and shared fixture names |
| `--run-postgres` | Explicit opt-in flag for tests requiring server binaries |
| `phase_1_finishing_notes.md` | Project-specific descriptive name; no mandated standard |

## Verification

Verified at the end of the resumed repair:

- Full suite with `--run-postgres`: **55 passed** (including 19 real PostgreSQL tests).
- Default suite: **36 passed, 19 skipped**, with PostgreSQL tests explicitly opt-in.
- Ruff, `pip check`, and `git diff --check`: passed.
- SQL comparison against `fe4301d`: business DDL unchanged after excluding outer
  transaction statements and comments.
- Python AST comparison against `fe4301d`: fixture builders, generation,
  insertion and verification functions unchanged, allowing the equivalent UTC alias.

The original 23 tests are retained. Added checks cover:

- Canonical/legacy simulator configuration and password handling.
- Repeatable generation and the hand-calculated tiny fixture.
- Migration discovery, missing files, duplicate numbering, and confirmation.
- Actual PostgreSQL migration reruns without duplicate history or lost data.
- The tiny fixture's 9,900,000-rial daily sales result.
- Order lifecycle, immutable line items, stable identifiers and advancing watermarks.
- Historical line totals remaining unchanged after catalog price updates.
- Unmanaged schema preservation, wrong-database reset refusal, and unrelated-table preservation.
- Failure injection: failed migration rolls back its schema/history; failed reset restores old data.
- Compatibility between the standalone psql reset and Python migration runner.
- Installed CLI and simulator entry points from a working directory outside the repository.

Integration tests start a private PostgreSQL cluster under ignored `.pgdata/`,
with a random password and loopback port. They override test connection settings,
stop the cluster, and clean up their own directory. They do not connect to the
developer's existing PostgreSQL databases. Process output goes to a file during
server startup so Windows child processes do not keep capture pipes open.

The default generator's expected fingerprint remains:

```text
1f05d0f63df5c127930d00927c184560c6377936a8f2159f9035b92a3899ceeb
```

That represents 100 customers, 50 products, 1,000 orders, 3,117 lines, and
63,985,710,500 rial in completed sales. Generator database verification runs
before commit. Database-generated IDs and operational timestamps remain outside
the deterministic business-payload guarantee.

## Complete file inventory

| File | Change |
| --- | --- |
| `src/store_dw/source_ops.py` | Correct path; numbered discovery; migration history; atomic migration/reset; reset target/confirmation checks |
| `src/store_dw/cli.py` | Reset prompt/cancellation and controlled migration/reset errors; import cleanup |
| `src/store_dw/config.py` | Add source-only DSN validation helper; preserve existing Settings loader |
| `migrations/source/001_initial_source_schema.sql` | Transfer transaction ownership to caller; no business-schema changes |
| `migrations/source/reset_dev_source.sql` | Atomic reset and matching history entry; preserve original guards |
| `simulators/seed_store.py` | Shared connection arguments, variable compatibility, password handling, lint cleanup |
| `simulators/generate_store.py` | Use shared connection arguments; lint cleanup |
| `pyproject.toml` | Add simulators to pytest's import path; dependencies and package settings unchanged |
| `.env.example` | New placeholder configuration with loading instructions |
| `README.md` | Fill previously empty file with setup, commands, migration/reset behavior, limitations and tests |
| `docs/phase_1_finishing_notes.md` | This explanation and file inventory |
| `tests/test_cli.py` | Extend existing tests for reset confirmation/cancellation and sanitized errors |
| `tests/conftest.py` | Isolated opt-in PostgreSQL cluster and source fixtures |
| `tests/test_source_ops.py` | Migration discovery and reset precondition tests |
| `tests/test_simulators.py` | Configuration compatibility, fixture and determinism tests |
| `tests/test_source_postgres.py` | Real source/migration/reset behavior and terminal workflow tests |

## Remaining boundaries

No warehouse pipeline, payment API, refund ingestion, scheduler, or dashboard is
implemented here. Dependency declarations remain minimum versions rather than an
exact lockfile; the current environment can be checked with `python -m pip check`.
Automatic adoption of existing source schemas and distributable wheel packaging
of external SQL files are not implemented. The documented install is editable
from this repository. Business rules and source contracts are unchanged.
