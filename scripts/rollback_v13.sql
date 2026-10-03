DROP INDEX IF EXISTS ix_token_events_call_time_agent;
ALTER TABLE token_events DROP COLUMN agent;
UPDATE export_state SET revision = revision + 1 WHERE id = 1;
DELETE FROM schema_migrations WHERE version = 13;
