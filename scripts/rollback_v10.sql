-- Schema rollback v10 -> v9. Run only after the privacy-first rollback runbook
-- (Plans/task-telemetry-jev-pilot/database.md): capture and JEV disabled, prompts purged,
-- task_evaluations exported outside Git. Requires SQLite >= 3.35 (ALTER TABLE DROP COLUMN).
BEGIN;
DROP TABLE IF EXISTS retention_state;
DROP TABLE IF EXISTS provider_cooldowns;
DROP TABLE IF EXISTS evaluator_attempts;
DROP TABLE IF EXISTS task_evaluations;
DROP TABLE IF EXISTS evaluator_runs;
DROP TABLE IF EXISTS tasks;
DROP INDEX IF EXISTS ix_token_events_project_session_turn;
ALTER TABLE token_events DROP COLUMN complexity_method;
ALTER TABLE token_events DROP COLUMN request_system_chars;
ALTER TABLE token_events DROP COLUMN request_history_chars;
ALTER TABLE token_events DROP COLUMN request_tool_output_chars;
ALTER TABLE token_events DROP COLUMN request_file_content_chars;
ALTER TABLE token_events DROP COLUMN request_file_ref_count;
ALTER TABLE token_events DROP COLUMN request_tool_names_json;
DELETE FROM schema_migrations WHERE version = 10;
COMMIT;
