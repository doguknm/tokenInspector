-- Schema rollback v12 -> v11. Run ONLY through scripts/rollback_schema.py --to 11 (or --to 10), with the
-- service stopped (Plans/o9-job-correlation-cc-producer/database.md). The runner checks the source version and
-- SQLite >= 3.35 (ALTER TABLE DROP COLUMN) and wraps this script in BEGIN IMMEDIATE ... COMMIT.
DROP INDEX IF EXISTS ux_token_events_ingest_seq;
DROP TABLE IF EXISTS export_state;
ALTER TABLE token_events DROP COLUMN ingest_seq;
DELETE FROM schema_migrations WHERE version = 12;
