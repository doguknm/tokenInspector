-- Pinned genuine schema v9, generated from commit 0aab993 (init_db on a fresh DB), 2026-09-27.
CREATE TABLE model_aliases (
	alias VARCHAR(128) NOT NULL, 
	model VARCHAR(128) NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (alias)
);
CREATE TABLE pricing_rules (
	model VARCHAR(128) NOT NULL, 
	input_price_per_1m FLOAT NOT NULL, 
	output_price_per_1m FLOAT NOT NULL, 
	cache_read_price_per_1m FLOAT, 
	cache_creation_price_per_1m FLOAT, 
	pricing_version INTEGER NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (model)
);
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE token_events (
	id VARCHAR NOT NULL, 
	project_name VARCHAR(64) NOT NULL, 
	client_event_id VARCHAR(128), 
	recorded_at VARCHAR NOT NULL, 
	occurred_at VARCHAR, 
	provider VARCHAR(64), 
	event_type VARCHAR(32) NOT NULL, 
	model VARCHAR(128) NOT NULL, 
	model_requested VARCHAR(128), 
	pricing_model VARCHAR(128), 
	session_id VARCHAR(128), 
	task_id VARCHAR(128), 
	turn_id VARCHAR(128), 
	turn_index INTEGER, 
	trace_id VARCHAR(128), 
	span_id VARCHAR(128), 
	parent_span_id VARCHAR(128), 
	api_request_id VARCHAR(128), 
	tool_call_id VARCHAR(128), 
	tool_name VARCHAR(128), 
	role VARCHAR(64), 
	environment VARCHAR(32), 
	platform VARCHAR(32), 
	user_id_hash VARCHAR(64), 
	tags_json VARCHAR, 
	prompt_tokens INTEGER NOT NULL, 
	completion_tokens INTEGER NOT NULL, 
	cache_read_tokens INTEGER NOT NULL, 
	cache_creation_tokens INTEGER NOT NULL, 
	reasoning_tokens INTEGER NOT NULL, 
	total_tokens INTEGER, 
	status VARCHAR NOT NULL, 
	http_status INTEGER, 
	finish_reason VARCHAR(64), 
	error_type VARCHAR(64), 
	error_message VARCHAR, 
	attempt INTEGER NOT NULL, 
	retry_count INTEGER NOT NULL, 
	ttft_ms INTEGER, 
	process_time_ms INTEGER, 
	request_size_bytes INTEGER, 
	response_size_bytes INTEGER, 
	tool_call_count INTEGER, 
	estimated_cost_usd FLOAT, 
	cost_status VARCHAR NOT NULL, 
	cost_status_reason VARCHAR(64), 
	pricing_version INTEGER, 
	prompt_hash VARCHAR(64), 
	prompt_length INTEGER, 
	prompt_text VARCHAR, 
	complexity INTEGER, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX idx_token_events_project_client_event ON token_events (project_name, client_event_id) WHERE client_event_id IS NOT NULL;
CREATE INDEX idx_token_events_project_recorded ON token_events (project_name, recorded_at);
CREATE INDEX ix_model_aliases_model ON model_aliases (model);
CREATE INDEX ix_token_events_model ON token_events (model);
CREATE INDEX ix_token_events_project_name ON token_events (project_name);
CREATE INDEX ix_token_events_recorded_at ON token_events (recorded_at);
CREATE INDEX ix_token_events_session_id ON token_events (session_id);
CREATE INDEX ix_token_events_trace_id ON token_events (trace_id);
