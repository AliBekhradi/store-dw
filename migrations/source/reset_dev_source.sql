\set ON_ERROR_STOP on

-- Refuse to run against any other database.
SELECT current_database() = 'undead_schema_test' AS allowed
\gset

\if :allowed
    \conninfo
\else
    \echo 'Refusing reset: this command only supports undead_schema_test.'
    \quit 1
\endif

\echo 'WARNING: this destroys store_source and its dependent objects.'
\prompt 'Type RESET undead_schema_test to continue: ' confirmation

SELECT :'confirmation' = 'RESET undead_schema_test' AS confirmed
\gset

\if :confirmed
    DROP SCHEMA IF EXISTS store_source CASCADE;
    \ir 001_initial_source_schema.sql
    \echo 'Development source reset completed.'
\else
    \echo 'Reset cancelled.'
    \quit 1
\endif