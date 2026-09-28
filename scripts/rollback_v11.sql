-- Schema rollback v11 -> v10. Run ONLY through scripts/rollback_schema.py --to 10, with the service
-- stopped (Plans/o9-job-correlation-cc-producer/database.md). The runner checks the source version and
-- SQLite >= 3.35 (ALTER TABLE DROP COLUMN) and wraps this script in BEGIN IMMEDIATE ... COMMIT.
DROP INDEX IF EXISTS ux_token_events_cc_client_event;
DROP INDEX IF EXISTS ix_tasks_job_ref;
ALTER TABLE tasks DROP COLUMN job_ref_conflicts;
ALTER TABLE tasks DROP COLUMN job_ref;
DELETE FROM schema_migrations WHERE version = 11;
