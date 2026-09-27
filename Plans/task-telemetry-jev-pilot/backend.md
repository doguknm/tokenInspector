# Backend — Task-Level Telemetry Pilot (ingest/task derivation, auth guards, retention, JEV worker, Tasks API, pilot CLI, Hermes plugin Faz 0)

**Lane**: backend
**Tool**: Claude Code — Opus 5.5, direct (no handoff); Part B on hermes
**Status**: not started
**Brief**: ./2026-09-27-summary.md
**Depends on**: ./database.md (must be implemented and reviewed first)
**Revision**: r1 (2026-09-27), plan-review round 1 fixes applied (items 1–21, 25, 27, 29 backend parts, 30)
**Revision**: r2 (2026-09-27), plan-review round 2 fixes applied (items 1–8, 9 seed, 13). **Plan LOCKED** — later changes go to the status.md Drift Log only.

## Goal

Add task handling to tokenInspector:
- derive tasks at ingest, with deterministic start complexity, hierarchy status and a completion signal;
- store redacted per-task prompt text, retained at most 30 days from producer capture across the live DB, WAL and backups;
- accept request-composition counts;
- guard the sensitive features with mandatory auth plus Host/Origin checks;
- run a JEV difficulty worker with atomic runs, durable per-call budget reservations and an exact Retry-After policy;
- serve a minimal Tasks API;
- ship a pilot CLI (select, blind label, score, report on a paired cohort).

Part B fixes and extends the Hermes producer plugin. It is implemented and tested **on hermes only**.

This lane has two parts with different repos:
- **Part A:** the tokenInspector backend. The canonical working copy is on hermes at `~/Projects/tokenInspector`.
- **Part B:** the Hermes producer plugin at `/home/dogukan/Projects/token_inspector` (hermes only).

Order: **D0 discovery → Part B 0a/0b → database.md migration → Part A → Part B 0c → Activation Gate (status.md) → pilot.**

---

## Locked Schema (from database.md; embedded)

`tasks` (v10):

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT PK | `task_ref` = `sha256(project_name+"\x1f"+task_id).hexdigest()[:32]` |
| `project_name` | TEXT NOT NULL | Unique with `task_id` |
| `task_id` | TEXT NOT NULL | Unique with `project_name` |
| `session_id` | TEXT | |
| `parent_task_id` | TEXT | External id, same project |
| `root_task_id` | TEXT | External id, same project |
| `hierarchy_status` | TEXT NOT NULL DEFAULT `'unknown'` | `root` \| `child` \| `unknown` |
| `source` | TEXT NOT NULL | `backfill` \| `ingest` |
| `first_seen_at`, `last_seen_at` | TEXT NOT NULL | |
| `completion` | TEXT | `session_end` \| `next_task` \| NULL |
| `completed_at` | TEXT | |
| `start_complexity` | INTEGER | 1..5 |
| `start_complexity_method` | TEXT | `request-shape-v1` \| `request-shape-v1-inferred` |
| `start_complexity_event_at` | TEXT | Ordering key part 1 |
| `start_complexity_event_id` | TEXT | Ordering key part 2 |
| `prompt_text` | TEXT | Redacted |
| `prompt_hash` | TEXT | |
| `prompt_length` | INTEGER | |
| `prompt_truncated` | INTEGER NOT NULL DEFAULT 0 | |
| `prompt_redaction_version` | TEXT | |
| `prompt_captured_at` | TEXT | Producer capture time; first write wins |
| `prompt_expires_at` | TEXT | captured + 30 d, never extended |
| `prompt_purged_at` | TEXT | |
| `created_at`, `updated_at` | TEXT NOT NULL | |

`evaluator_runs`:
- Columns: `id` (run_id), `evaluator`, `status` (`queued|running|done|stopped|aborted`), `requested_count`, `queued_count`, `stop_reason`, `retry_not_before`, `queued_at`, `started_at`, `finished_at`.
- Partial unique index: one `queued|running` run per evaluator.

`task_evaluations`:
- Columns: `id`, `task_ref`→tasks, `run_id`→evaluator_runs, `evaluator` (`jev|human`), `rubric_version` (`difficulty-v0`), `status` (jev: `ok|error|rate_limited|deferred`; human: `ok|skipped`), `label`, `labeler`, `note`, `raw_score`, `confidence`, `probabilities_json` (5 floats), `legend_json` (5 strings), `model`, `provider_used`, `input_hash`, `input_tokens`, `cost_usd`, `http_attempts`, `latency_ms`, `error_type`, `evaluated_at`.
- Unique: JEV ok per `(task_ref, rubric_version, input_hash)`; human per `(task_ref, rubric_version, labeler)`.

`evaluator_attempts` (the durable budget ledger; one row per HTTP call, committed before the call):
- Columns: `id`, `run_id`, `task_ref`, `provider`, `status` (`reserved|succeeded|failed|uncertain`), `reserved_tokens`, `reserved_cost_usd`, `actual_input_tokens`, `actual_cost_usd`, `http_status`, `error_type`, `retry_after_s`, `started_at`, `completed_at`.

`provider_cooldowns` (r2 item 4) has columns `provider` (PK), `not_before`, `reason` (`retry_after|backoff`) and `updated_at`. It holds a persisted per-provider no-call deadline.

`retention_state` (r2 items 1–2) holds key/value rows: `pending_wal_checkpoint`, `last_purge_at`, `last_purge_ok`, `last_error` and `all_copy_purge_verified_at`.

`token_events` new nullable columns:
- `request_system_chars`
- `request_history_chars`
- `request_tool_output_chars`
- `request_file_content_chars`
- `request_file_ref_count`
- `request_tool_names_json`
- `complexity_method`

New index `ix_token_events_project_task`.

---

## Part A — tokenInspector backend

### §0 Security guards (AC11)

1. **Mandatory token for sensitive features.**

   Prompt capture is **enabled** only if all of these hold:
   - `STORE_TASK_PROMPTS` is truthy;
   - `INGEST_TOKEN` is set, with at least 16 chars;
   - `TASK_PROMPT_PURGE_INTERVAL_S` is in 1..21600.

   JEV is **enabled** only if all of these hold:
   - `JEV_ENABLED` is truthy;
   - `AI_GATEWAY_API_KEY` is non-empty;
   - `INGEST_TOKEN` is set (≥16 chars);
   - the budget config is valid (§6).

   If a flag is on but a requirement is missing, the feature **refuses to start**. The service still starts; only that feature stays off. It logs one ERROR line at startup, e.g. `[SECURITY] task prompt capture disabled: ingest_token_missing`, and reports `disabled_reason` in `/api/meta`, `/api/tasks/evaluator-status` and `/api/tasks/retention-status`.

   `disabled_reason` values:

   | Value | Meaning |
   |---|---|
   | `not_enabled` | Flag off; logged at INFO |
   | `credentials_missing` | JEV key absent; logged at INFO (the silent-skip path) |
   | `ingest_token_missing` | ERROR |
   | `invalid_purge_interval` | ERROR |
   | `invalid_budget_config` | ERROR |

   While capture is disabled, incoming `task_prompt_text` is discarded.

   **Configuration preflight (r2 item 8).** Regardless of the flags, the service evaluates the would-be configuration at startup and reports the result in `/api/meta` as `config_errors`. Each entry is `{"feature": "capture"|"jev", "error": "ingest_token_missing"|"invalid_purge_interval"|"invalid_budget_config"|"credentials_missing"}`. With the flags off and a correct configuration, `config_errors == []` while `disabled_reason == 'not_enabled'`. That is exactly the state the Activation Gate's preflight phase requires (status.md).
2. **`require_sensitive_auth` dependency** (new, in `auth.py`):
   - `INGEST_TOKEN` unset → **403** `{"detail":"auth_not_configured"}`, regardless of feature flags;
   - `X-Ingest-Token` missing or wrong → **401**, compared with `hmac.compare_digest`;
   - an `Origin` header present and not in the allowed origins → **403** `{"detail":"origin_not_allowed"}`. Allowed origins are `http://127.0.0.1:8100` and `http://localhost:8100`, extended by `TOKEN_INSPECTOR_ALLOWED_ORIGINS`.

   It applies to `GET /api/tasks/{ref}?include_prompt=true`, `POST /api/tasks/{ref}/labels`, `POST /api/tasks/evaluate` and `POST /api/tasks/purge-expired`. The existing ingest endpoints keep `require_ingest_auth`. When capture is enabled a token is necessarily configured, so ingest is enforced too.
3. **Host allowlist.** Add Starlette `TrustedHostMiddleware` globally, with `allowed_hosts` from `TOKEN_INSPECTOR_ALLOWED_HOSTS` (default `127.0.0.1,localhost`). Any other `Host` → 400. This blocks DNS rebinding. The test conftest adds `test`. The SSH-tunnel access pattern (`localhost:8100`) keeps working.

### Endpoints

Register the static paths before `/api/tasks/{task_ref}` in `routes/tasks.py`.

| Method | Path | Auth | Request shape | Response shape | Status codes |
|---|---|---|---|---|---|
| POST | `/api/events`, `/api/events/batch` (existing) | `require_ingest_auth` | Existing `EventIn` plus the new optional fields (§1) | Unchanged | Unchanged |
| GET | `/api/meta` | none (Host allowlist only) | — | `{"schema_version":10, "task_prompt_capture":bool, "task_prompt_capture_disabled_reason":str\|null, "jev_enabled":bool, "jev_disabled_reason":str\|null, "config_errors":[{"feature","error"}]}` (r2 item 8) | 200 |
| GET | `/api/tasks` | none | Query: `days` (default 30, clamp 1..3650, on `last_seen_at`), `project`, `evaluated` (bool), `has_prompt` (bool; means `prompt_state='retained'`), `root_only` (bool; means `hierarchy_status='root'`), `page` (≥1), `page_size` (1..200, default 50) | `{"items":[TaskItem], "total":int, "page":int, "page_size":int, "projects":[str]}`. `projects` (r2 item 13) = distinct `project_name` of **all** tasks whose `last_seen_at` falls in the `days` window, sorted ascending. It **ignores** the `project`, `evaluated`, `has_prompt` and `root_only` filters and pagination, so the frontend selector always keeps every option. Order `last_seen_at DESC, task_ref ASC` (stable). A page past the end returns `items: []` with the true `total`. **Never contains prompt text** | 200, 422 |
| GET | `/api/tasks/evaluator-status` | none | — | `{"enabled":bool, "disabled_reason":str\|null, "credentials_present":bool, "running":bool, "current_run":null\|{"run_id","status","queued_count","attempted"}, "last_run":null\|{"run_id","status","stop_reason","retry_not_before","finished_at"}, "spent_today_usd":float, "spent_month_usd":float, "calls_today":int, "budget_day_usd":float, "budget_month_usd":float, "max_calls_per_day":int, "last_error_type":str\|null, "provider_cooldowns":[{"provider","not_before"}]}` (cooldowns: r2 item 4). `last_error_type` = `last_run.stop_reason` | 200 |
| GET | `/api/tasks/evaluator-runs/{run_id}` | none | — | `{"run_id","status","requested_count","queued_count","attempted","ok","error","rate_limited","deferred","skipped","stop_reason","retry_not_before","queued_at","started_at","finished_at"}` | 200, 404 |
| GET | `/api/tasks/retention-status` | none | — | `{"capture_enabled":bool, "disabled_reason":str\|null, "purge_interval_s":int, "last_purge_at":str\|null, "last_purge_ok":bool\|null, "last_error":str\|null, "pending_expired":int, "last_checkpoint_ok":bool\|null, "pending_wal_checkpoint":bool, "backups_scanned":int, "backups_repurged":int, "scheduled":bool, "interval_refused":bool, "retained_data_present":bool, "oldest_overdue_expires_at":str\|null, "all_copy_purge_verified_at":str\|null}` (r2 items 1–2) | 200 |
| GET | `/api/tasks/{task_ref}` | `require_sensitive_auth` **only when** `include_prompt=true` | Query `include_prompt` (bool, default false) | `TaskItem` + `children` (element shape below), `children_truncated`, `evaluations`, `prompt_length`, `prompt_truncated`, `prompt_redaction_version`. `prompt_text` only with `include_prompt=true`, and **null when expired, purged or never captured** | 200, 401, 403, 404 |
| POST | `/api/tasks/{task_ref}/labels` | `require_sensitive_auth` | `{"label":0..4\|null, "skipped":bool (default false), "labeler":^[a-z0-9._-]{1,32}$, "rubric_version":"difficulty-v0", "note":str≤280\|null}`. Exactly one of `label` (0..4) or `skipped:true` must be given; otherwise 422 (r2 item 7) | `{"id","task_ref","status":"ok\|skipped","label","labeler","rubric_version","evaluated_at"}`. The note is scrubbed (§4) before storage. Upsert per `(task, rubric, labeler)`: a label replaces a skip, and a skip replaces a label | 201, 200, 401, 403, 404, 422 |
| POST | `/api/tasks/evaluate` | `require_sensitive_auth` | `{"task_refs":[str] (1..100)}` | `{"run_id":str\|null, "enabled":bool, "queued":int, "skipped":[{"task_ref","reason"}], "retry_not_before":str\|null}`. `retry_not_before` is set, and nothing is queued, when every provider is cooling down beyond the allowance at request time (r2 item 4). Reasons: `jev_disabled`, `not_found`, `no_prompt`, `prompt_expired`, `prompt_purged`, `already_scored`, `evaluator_task` | 202 (run reserved), 200 (nothing queued, `run_id` null), 401, 403, 409 `{"detail":"evaluation_in_progress"}`, 422 |
| POST | `/api/tasks/purge-expired` | `require_sensitive_auth` | Query `dry_run` (default **true**) | `{"dry_run","cutoff","eligible","purged","notes_purged","backups_scanned","backups_repurged","checkpoint_ok"}` | 200, 401, 403 |

**`TaskItem`:**

```json
{
  "task_ref": "32-hex", "project_name": "…", "task_id": "…",
  "session_id": "…|null", "parent_task_id": "…|null", "root_task_id": "…|null",
  "hierarchy_status": "root|child|unknown", "child_count": 0,
  "first_seen_at": "…Z", "last_seen_at": "…Z", "wall_time_ms": 0,
  "completion": "session_end|next_task|inferred|open", "completed_at": "…Z|null",
  "llm_request_count": 0, "tool_call_count": 0, "error_count": 0, "retry_count": 0,
  "prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "total_tokens": 0,
  "cost_usd": null, "estimated_cost_usd": 0.0, "unpriced_count": 0,
  "start_complexity": 3, "start_complexity_method": "request-shape-v1",
  "prompt_state": "retained|expired|purged|none", "has_prompt": true, "prompt_purged": false, "prompt_expires_at": "…Z|null",
  "jev": null,
  "human_label_count": 0
}
```

Field rules:

- **`completion`:** the stored value if set. Otherwise `inferred` when `last_seen_at < now − 60 min`, else `open`. `completed_at` for inferred = `last_seen_at`.
- **`prompt_state`:**
  - `purged` if `prompt_purged_at` is set;
  - else `expired` if `prompt_text` is not null and `prompt_expires_at ≤ now` (**read-time enforcement**: the text is never served);
  - else `retained` if `prompt_text` is not null;
  - else `none`.
  - `has_prompt` = (`prompt_state == 'retained'`).
- **`jev`:** the latest `status='ok'` JEV evaluation for `difficulty-v0`: `{"raw_score", "display_score" (=raw+1), "confidence", "probabilities" (5-list), "legend" (5 strings), "provider_used", "rubric_version", "evaluated_at"}`.
- **Aggregates** are computed at query time over `token_events` joined on `(project_name, task_id)`:
  - token sums cover `llm_request` events;
  - `total_tokens` = prompt + completion + cache_read + cache_creation;
  - `error_count` = llm_request events with status ≠ success;
  - cost follows the `_cost_columns()` semantics, and `cost_usd` is **null when there are zero priced llm_request events**.
- **`EvaluationItem`:** `{"id","evaluator","rubric_version","status","label","labeler","raw_score","display_score","confidence","probabilities","provider_used","cost_usd","http_attempts","error_type","evaluated_at"}`. `note` is never returned.
- **`children[]` element (r2 item 13):**

  ```json
  {"task_ref":"32-hex", "task_id":"…", "hierarchy_status":"child", "start_complexity":2, "jev_raw_score":1.4, "first_seen_at":"…Z"}
  ```

  - `start_complexity` and `jev_raw_score` may be null.
  - `jev_raw_score` = the `raw_score` of the child's latest `ok` JEV evaluation for `difficulty-v0`, or null.
  - Children are tasks in the **same `project_name`** whose `parent_task_id` equals this task's `task_id`.
  - Order `first_seen_at ASC, task_ref ASC`. At most 200 children are returned; `children_truncated` is true when capped.

### Business Logic

**§1 Ingest extensions (`routes/events.py`, `EventIn`, `extra="ignore"` kept).** New optional fields:

| Field | Type / validation |
|---|---|
| `parent_task_id`, `root_task_id` | ≤128 |
| `task_hierarchy` | `root` \| `child` \| `unknown` |
| `task_prompt_text` | ≤200,000 chars |
| `task_prompt_captured_at` | ISO-8601 with timezone, normalised like `occurred_at` |
| `complexity_method` | ≤32; stored in `token_events.complexity_method` |
| `request_system_chars`, `request_history_chars`, `request_tool_output_chars`, `request_file_content_chars`, `request_file_ref_count` | int ≥0 |
| `request_tool_names` | ≤32 items matching `^[A-Za-z0-9_.:\-/]{1,128}$`; stored deduplicated and sorted as JSON |

Rules:
- `task_prompt_text` is never written to `token_events.prompt_*` and never triggers `score_complexity`.
- A session-end signal is an event with `event_type='session'` and `tags.session_phase='end'`. The exact producer mapping is confirmed in D0.

**§2 Task derivation at ingest (`task_store.py`).** It runs for each **newly inserted** (non-duplicate) event with a non-empty `task_id`, in the same transaction as the event insert.

- **Upsert on `(project_name, task_id)`:**
  - `first_seen_at` = MIN, `last_seen_at` = MAX.
  - `session_id`, `parent_task_id`, `root_task_id` = COALESCE (first non-null wins).
  - `hierarchy_status` becomes `child` if `parent_task_id` is present or `task_hierarchy='child'`. Otherwise it becomes `root` if `task_hierarchy='root'` and the stored value is not `child`. Otherwise it stays unchanged; the insert default is `unknown`.
  - **`child` is never downgraded.**
- **Start complexity (r2 item 6).** Use the single shared rule `start_complexity_candidate(event)` from database.md (D0 decision rules), applied **before** ordering:
  - A candidate is an `llm_request` with non-null `complexity` whose method (the field, else `tags.complexity_method`) is in `APPROVED_METHODS = {'request-shape-v1'}`.
  - At ingest, events with an explicit non-approved method (e.g. `ai-v0`) or a NULL method are never candidates. The NULL-method `request-shape-v1-inferred` exception exists **only** in the backfill.
  - The stored `start_complexity_method` is always the rule's returned value, never the raw producer string.
  - A candidate replaces the stored value iff the stored value is NULL, or `(event_at, event_id) < (start_complexity_event_at, start_complexity_event_id)` compared lexicographically. `event_at` is the normalized `occurred_at` and `event_id` is `token_events.id`. This is the same ordering as the backfill.
- **Completion: recomputed, independent of arrival order (r2 item 5).** `recompute_session_completion(session, project_name, session_id)` is shared with the backfill.
  - **When it runs:** in the same transaction, after **any** newly inserted event that has a `session_id`. That **includes session-end markers without a `task_id`**; this call sits outside the `task_id` guard.
  - **What it does:** recomputes every task of that `(project_name, session_id)` from stored data only:
    1. Order the session's tasks by `(first_seen_at, id)`.
    2. A task that has a later-starting task gets `completion='next_task'`, with `completed_at` = the next task's `first_seen_at`.
    3. The last task gets `completion='session_end'` if a marker exists (`event_type='session'`, `tags.session_phase='end'`, same project+session, `occurred_at ≥ task.first_seen_at`). Its `completed_at` = the earliest such marker. Otherwise it is NULL.
    4. Rows are written only when the value changes.
  - **Consequences:** a late (spooled) older task A that arrives after B still gets `next_task` at B's start. A marker that arrived before its tasks still marks the last task. The result equals the backfill's on the same event set.
- **Duplicate replays** touch nothing. That makes spool replay safe.

**§3 Task prompt storage.** Applies only when capture is **enabled** (§0) and the event carries `task_prompt_text` and `task_id`.
1. `captured_at` = `task_prompt_captured_at`, else the event's `occurred_at`.
   - If `captured_at > now + 5 min`, clamp it to `now`.
   - **If `captured_at + 30 days ≤ now`, discard the prompt entirely** (log count only). An old spool replay therefore never gets a fresh retention period.
2. `redacted = scrub_text(text)` (§4), truncated to 32,000 chars, setting `prompt_truncated`. `prompt_length` is the original length.
3. `UPDATE tasks SET prompt_text, prompt_hash (sha256 of stored), prompt_length, prompt_truncated, prompt_redaction_version='task-redact-v1', prompt_captured_at=:captured_at, prompt_expires_at=:captured_at+30d WHERE id=:id AND prompt_captured_at IS NULL`.
   - First write wins.
   - The expiry is **derived from producer capture time and never extended**.
   - A purged row keeps `prompt_captured_at`, so it is never repopulated.
4. **Read-time expiry enforcement.** Every prompt read (API `include_prompt`, pilot selection, JEV worker) requires `prompt_text IS NOT NULL AND prompt_expires_at > now`. Otherwise it behaves as expired, even if the purge has not run yet.

**§4 Redaction `task-redact-v1` (`redaction.py`; ported identically to the plugin, Part B 0b).** `scrub_text(str) -> str` is pure and idempotent. Rules, in order:
1. PEM private-key blocks → `[REDACTED:private_key]`.
2. Key shapes → `[REDACTED:secret]`:
   - `sk-[A-Za-z0-9_\-]{16,}`
   - `AKIA[0-9A-Z]{16}`
   - `gh[pousr]_[A-Za-z0-9]{30,}`
   - `github_pat_[A-Za-z0-9_]{20,}`
   - `xox[abprs]-[A-Za-z0-9\-]{10,}`
   - `AIza[0-9A-Za-z_\-]{35}`
3. JWTs → `[REDACTED:jwt]`.
4. `Bearer <16+ token chars>` → `Bearer [REDACTED:secret]`.
5. Secret-named assignments (`*KEY*|*TOKEN*|*SECRET*|*PASSWORD*|*PASSWD*|*CREDENTIAL*` followed by `=` or `:`) → keep the name and replace the value.
6. **Git remotes** → `[REDACTED:git_remote]`: `git@host:owner/repo(.git)?`, `ssh://…`, `git://…`, and `https?://host/…/*.git`. This runs before the URL and path rules.
7. URL userinfo → `://[REDACTED:userinfo]@`.
8. Home prefixes: `/home/<u>/` → `~/`, `/Users/<u>/` → `~/`, `<Drive>:\Users\<u>\` → `~\`.
9. **Other absolute paths** → `[REDACTED:abs_path]`:
   - POSIX `/<seg>/<seg>…`, i.e. at least two segments starting with `/`, preceded by start-of-text, whitespace, a quote, `(`, `=` or `:`, and **not** part of a URL (not preceded by `//host`);
   - Windows `<Drive>:\…`.

   Examples: `/srv/private/workspace`, `/etc/hosts`, `D:\work\x`.
10. **Internal hostnames** → `[REDACTED:internal_host]`:
    - names ending in `.local`, `.lan`, `.internal`, `.intranet`, `.corp`, `.home.arpa` or `.ts.net`;
    - any extra suffixes in `REDACT_INTERNAL_HOST_SUFFIXES`;
    - literal terms in `REDACT_EXTRA_TERMS` (comma-separated, e.g. machine names such as `hermes`), matched as whole words, case-insensitive.
11. Private IPv4 (10/8, 172.16/12, 192.168/16, 100.64/10) → `[REDACTED:private_ip]`.
12. Emails → `[REDACTED:email]`.

Supporting rules:
- **Shared test vectors** live in `tests/fixtures/redaction_vectors.json`, a list of `{input, expected}`. The plugin repo carries a byte-identical copy, and both suites must pass it. Any rule change bumps the version in both.
- Residual risk remains: pattern-based scrubbing is not exhaustive. That risk is accepted only via the ADR-002 approval (status.md Activation Gate).

**§5 Retention (`retention.py`).**
- `purge_expired(session, now, dry_run)` does the following:
  1. `UPDATE tasks SET prompt_text=NULL, prompt_purged_at=:now, updated_at=:now WHERE prompt_text IS NOT NULL AND prompt_expires_at <= :now`.
  2. `UPDATE task_evaluations SET note=NULL WHERE evaluator='human' AND note IS NOT NULL AND evaluated_at <= :now-30d`.
  3. **Commit steps 1–2 first (r2 item 1).** If anything changed, set `retention_state.pending_wal_checkpoint='1'` in the same commit.
     - Then, **whenever `pending_wal_checkpoint='1'`**, run `PRAGMA wal_checkpoint(TRUNCATE)`. This happens independently of whether the current run changed any rows.
     - Success (first result column 0): set the flag to `'0'` and `last_checkpoint_ok=true`.
     - Busy: keep the flag and set `last_checkpoint_ok=false`. The checkpoint is retried on the next run even if no new rows expire, and also after a restart.
  4. **Backup re-purge.** For every file matching `<DB_PATH>.bak-v*` in the DB directory that has a `tasks` table, run the same two UPDATEs (stdlib `sqlite3` in a thread, `secure_delete=ON`). If rows changed, run `VACUUM`, which rewrites the file with no free pages holding the old text.
  5. No row is deleted anywhere.

  One INFO line per run: `[RETENTION] purged=<n> notes=<n> backups=<scanned>/<repurged> checkpoint=<ok>`. No text is ever logged. Rows are traceable by `prompt_purged_at` plus `session_id`/`task_id`.
- **Scheduling, independent of capture (r2 item 2):**
  - **Startup.** The purge runs at lifespan startup **before serving**, whatever the flags, so a restored backup is purged immediately.
  - **When the loop is scheduled.** Always while capture is enabled, and also whenever **retained data exists**, **regardless of `STORE_TASK_PROMPTS`**. Retained data means any of:
    - a non-null `tasks.prompt_text` or human `note`;
    - a DB-directory backup containing either;
    - `pending_wal_checkpoint='1'`.
  - **`TASK_PROMPT_PURGE_INTERVAL_S`.** Allowed range 1..21600. `0` is honoured **only** if no retained data exists **and** `retention_state.all_copy_purge_verified_at` has been set by `scripts/purge_task_prompts.py --all --include-backups --verify`. Otherwise `0` (or an out-of-range value) is refused: the loop runs at the default 21600, an ERROR is logged, and `/api/tasks/retention-status` shows `interval_refused=true`.
  - **Lag.** The target lag is one interval, but it is **not guaranteed**: a failed or blocked run extends it. Failures are always surfaced:
    - an ERROR log;
    - `last_purge_ok=false` and `last_error`;
    - `oldest_overdue_expires_at`, the oldest `prompt_expires_at ≤ now` whose text is still present.

    The purge retries on the next interval. Meanwhile, read-time enforcement (§3.4) keeps expired text unreadable.
  - **Bookkeeping.** `last_purge_at`, `last_purge_ok` and `last_error` are persisted in `retention_state`, so they survive restarts.
- **No pre-purge backup.** A backup would copy the expiring text. This deliberate exception is recorded in ADR-002.
- **Standalone script `scripts/purge_task_prompts.py`:**
  - usage: `--db <path> (--expired | --all) [--include-backups] [--dry-run] [--verify]`;
  - `--verify` (with `--all --include-backups`) re-reads the DB and every backup and runs `wal_checkpoint(TRUNCATE)`. Only if no prompt or note text remains does it write `retention_state.all_copy_purge_verified_at` (r2 item 2);
  - stdlib only, with **no imports from the app**, so it works during a rollback;
  - `--all` nulls every prompt and every human note;
  - it is used by the rollback runbook (database.md).
- **Logical deletion ≠ physical erasure.** Document in ADR-002 what the purge guarantees and what it does not:

  | Scope | Guarantee |
  |---|---|
  | Main file | Column NULLed; `secure_delete` zeroes the freed page content |
  | WAL | Truncated after checkpoint |
  | DB-directory backups | Re-purged and VACUUMed |
  | Filesystem/SSD remnants, OS caches, manual copies outside the DB directory | **Not** guaranteed |
  | Copies held by JEV providers | Out of local control |

**§6 JEV worker (`jev_scorer.py`).** It follows the `complexity_scorer.py` pattern: BackgroundTasks, its own `AsyncSessionLocal()`, and it never raises into a request.

- **Parser fixture (real).** `Plans/task-telemetry-jev-pilot/fixtures/jev-response-2026-09-27.json`, captured on hermes on 2026-09-27, with no credentials. Copy it verbatim to `tests/fixtures/jev_response_2026-09-27.json`. Field paths in `response`:

  | Data | Path |
  |---|---|
  | Score | `answers.difficulty.score` |
  | Confidence | `answers.difficulty.confidence` |
  | Legend | `answers.difficulty.legend` (map `"0".."4"`) |
  | Probabilities | `answers.difficulty.probabilities` (map `"0".."4"`) |
  | Answer type | `answers.difficulty.type == "score"` |
  | Input tokens | `usage.input_tokens` |
  | Cost | `provider_metadata.gateway.cost` (USD string) |
  | Provider used | `provider_metadata.gateway.routing.finalProvider` |

  The 429 example body has `error.type == "rate_limit_exceeded"`.
  - **The capture did not record whether a 429 carried `Retry-After`.** Tests therefore cover present, absent, HTTP-date and malformed values.
  - The capture used default routing (no `only`). Requests here use `only`.
  - `provider_used` = `finalProvider`. If it differs from the requested `only` provider, store `finalProvider` and log a WARNING.
- **Run reservation (atomic).** The `/evaluate` handler:
  1. validates refs and computes skips;
  2. `INSERT INTO evaluator_runs(status='queued', …)` and commits. The partial unique index makes a concurrent second request fail with IntegrityError → **409**;
  3. schedules the background task with `run_id` and returns 202 with `run_id`.

  The worker sets `running`/`started_at`, and at the end `done`, or `stopped` with a `stop_reason`, plus `finished_at`. At startup, any `queued`/`running` run → `aborted` (`stop_reason='restart'`), and any `reserved` attempt → `uncertain`. The CLI polls `/api/tasks/evaluator-runs/{run_id}` until the status is terminal.
- **Request body** (rubric `difficulty-v0`, not validated):

  ```json
  {"model":"typesafe-ai/jev",
   "state":"<redacted prompt>",
   "questions":{"difficulty":{"type":"score",
     "instructions":"Rate the start-of-task difficulty of this software-engineering request, judged only from the request text.",
     "criteria":["Single obvious routine step","A few known steps","Multi-file / multi-step analysis with tests","Unclear root cause or multi-system coordination","Open-ended research or deep architectural uncertainty"]}},
   "providerOptions":{"gateway":{"only":["<provider>"]}}}
  ```

  - The criteria strings equal the fixture legend.
  - The state is the redacted prompt only; no intensity metadata.
  - `input_hash` = sha256 of `json.dumps({rubric_version, state, questions}, sort_keys=True)`.
  - An existing `ok` row with the same hash → skip as `already_scored`.
- **Pre-call checks, before every HTTP call:**
  1. Re-read the task. Skip as `prompt_expired`/`prompt_purged` if the text is gone or `prompt_expires_at ≤ now`. The worker never sends expired text.
  2. Budget check (below).
- **Durable budget reservation (item 9) and upper-bound estimate (item 10):**
  - `reserved_tokens` = `len(request_body_utf8_bytes) + 1024`.
    - Assumption, documented in ADR-002: a byte-level/BPE tokenizer emits at most one token per input byte, and 1024 covers the evaluator's hidden template.
    - Fixture check: 446 billed tokens against a request body of well under 1 KB.
  - `reserved_cost_usd` = `reserved_tokens × 0.042 / 1e6`.
  - Insert an `evaluator_attempts` row (`status='reserved'`) and **commit before** sending.
  - **Reconciliation.** Set `actual_input_tokens` = `usage.input_tokens` and `actual_cost_usd` = `float(gateway.cost)`; both must be finite and ≥ 0. If cost is absent but usage is present, compute it from the price. Set status `succeeded`, or `failed` with `http_status`/`error_type`.
  - Failed and uncertain attempts keep their reserved cost, as a conservative assumption.
  - If `actual > reserved`: WARNING `[JEV] reservation exceeded`.
  - **Spend** = `SUM(COALESCE(actual_cost_usd, reserved_cost_usd))`. **Calls** = `COUNT(*)` of attempts in the UTC day or month.
  - **Stop the run before the call** if `spend_day + reserved > JEV_DAILY_BUDGET_USD`, or `spend_month + reserved > JEV_MONTHLY_BUDGET_USD`, or `calls_day + 1 > JEV_MAX_CALLS_PER_DAY`. `stop_reason='budget_exceeded'`, one WARNING.
  - **Budget config validation:** all values finite and > 0, daily ≤ monthly, max calls an integer ≥ 1. Otherwise JEV is disabled (`invalid_budget_config`).
- **Routing and Retry-After (item 12).** Provider order is fixed: `typesafe-ai` then `digitalocean`.
  - **Persisted provider cooldowns (r2 item 4).**
    - Every 429, and every backoff wait, sets `provider_cooldowns.not_before = max(existing, now + wait)` for that provider, committed immediately. The cooldown therefore applies to **later tasks, later runs and after a restart**.
    - Before **every** call to provider P, including the first call of a task and of a run, check `not_before(P)`:
      - `now ≥ not_before(P)`: call normally.
      - Remaining wait ≤ `JEV_MAX_RETRY_WAIT_S`: sleep exactly that remaining wait, then call.
      - Remaining wait longer: P is unavailable for this call. If typesafe-ai is unavailable, use digitalocean. If digitalocean is also unavailable, stop the run as `deferred_rate_limited` with `retry_not_before = min(not_before of both providers)`.
    - `/evaluate` checks the table at request time. If both providers are cooling beyond the allowance, it queues nothing and returns 200 with `retry_not_before`.
    - The per-task rules below apply on top of this.
  - **At most 2 attempts per provider**, counting any mix of 429, 5xx, timeout and connection error. At most 4 per task.
  - **Retry-After parsing:** delta-seconds (a non-negative integer), or an HTTP-date via `email.utils.parsedate_to_datetime` converted to seconds from now (floored at 0). A malformed or absent header uses backoff 2 s, then 4 s. Store the parsed value in `retry_after_s`.
  - **On 429:**
    - If `wait ≤ JEV_MAX_RETRY_WAIT_S` (default 60) and this provider has an attempt left, sleep **exactly** `wait` and retry the same provider.
    - Otherwise, if the provider is typesafe-ai, **fall back to digitalocean immediately**. This never retries typesafe-ai early.
    - If the provider is digitalocean and `wait > allowance`, **stop the run** (`status='stopped'`, `stop_reason='deferred_rate_limited'`, `retry_not_before = now + wait`). This task's evaluation becomes `deferred`, and the remaining tasks are not attempted.
    - If digitalocean has used both attempts on 429s within the allowance, the task becomes `rate_limited`, and the run continues to the next task.
  - **Fallback happens iff the most recent typesafe-ai failure was a 429.** 5xx, timeout or connection error → retry the same provider after 2 s if an attempt remains, else `error` (`http_5xx`/`timeout`). No fallback in that case.
  - 401/403 → `error_type='auth_error'`, run `stopped` with `stop_reason='auth_error'`.
  - Other 4xx → `http_4xx`, continue.
  - Space tasks at least 0.5 s apart.
- **Strict response validation (item 13).** Any failure → `invalid_response`, and no numeric value is stored.
  - `answers.difficulty.type == "score"`.
  - `score` is a finite number in [0, 4]; `confidence` is a finite number in [0, 1].
  - `probabilities` has **exactly** keys `"0".."4"` (or is a list of length 5); each value is finite and in [0, 1]; the sum is 1 ± 0.02.
  - `legend` has exactly keys `"0".."4"`, and each value is a non-empty string equal to the corresponding request criterion (unambiguous level mapping).
  - `usage.input_tokens`, if present, is an integer ≥ 0. `gateway.cost`, if present, parses to a finite value ≥ 0.
  - `bool` is rejected where a number is expected.
- **Persistence.** One `task_evaluations` row per attempted task (`run_id`, sums of reconciled attempts, `http_attempts`). One evaluator-usage `token_events` row per task via `_prepare_event`/`_insert_event`:
  - `project_name='token-inspector'`, `model='typesafe-ai/jev'`, `provider=provider_used`, `role='evaluator'`;
  - `prompt_tokens` = actual or reserved tokens, `completion_tokens=0`;
  - `tags={"purpose":"evaluator","evaluator":"jev"}`, `client_event_id='jev-<evaluation id>'`;
  - **`task_id` and `session_id` NULL.** No task is derived from it, and tasks in project `token-inspector` are refused (`evaluator_task`).
- **Pricing seed:** add `("typesafe-ai/jev", 0.042, 0.0)` to `_BASE_PRICING`.
- **No automatic triggering.** JEV runs only via `/evaluate`.

**§7 Pilot CLI (`jev_pilot.py` + `pilot_metrics.py`; stdlib + httpx).**

It works over HTTP (`--url`, default `http://127.0.0.1:8100`) with `X-Ingest-Token` from `INGEST_TOKEN`, and never opens SQLite.

**`select --n 50 --seed <int> --out <file>`**
- Eligibility, all of which must hold:
  - `hierarchy_status='root'` (unknown is excluded);
  - `completion ∈ {session_end, next_task, inferred}`;
  - `prompt_state='retained'` and `prompt_expires_at > now + 5 days`;
  - `start_complexity` non-null with `start_complexity_method='request-shape-v1'`;
  - `project_name ≠ 'token-inspector'`.
- Stratified random sampling over strata `{1..5}` with `random.Random(seed)`:
  - equal allocation `floor(n/k)` over the non-empty strata;
  - a stratum smaller than its allocation contributes all its tasks;
  - the shortfall and remainder are drawn randomly from the leftover pool.
- The sample file records the seed, the rules, the per-stratum population/sample counts, the counts per completion type (`inferred` shown separately), and the refs. It holds no prompt text. Its default location is outside Git (`~/.local/share/token-inspector/pilot/`).
- Selection is a pure function `select_sample(candidates, n, seed)`.

**`label --sample <file> --labeler <handle> [--notes]`**
- Blind labelling, in a seeded shuffled order.
- It shows only `project_name` and the redacted prompt, both passed through **`safe_terminal()`**. That function replaces every Unicode `Cc` character except `\n`/`\t`, and the bidi/format controls U+200E, U+200F, U+202A–U+202E and U+2066–U+2069, with visible `\xNN` / `\uNNNN` escapes. No ANSI sequence reaches the terminal.
- It never shows RS-v1, JEV, tokens or cost.
- Keys:
  - `0–4`: label;
  - `s`: skip. The skip is **persisted** via `POST …/labels` with `skipped:true` and gets status `skipped`;
  - `q`: quit, and resume later.
- **Labelling progress is durable and metadata-only (r2 item 7).** It is the set of human `task_evaluations` rows for `(task, difficulty-v0, labeler)`:
  - `ok` = labelled;
  - `skipped` = skipped_by_labeler;
  - no row = not_labelled.

  No prompt text is stored in it. On start, `label` fetches each sampled task's evaluations and **resumes** with the tasks that have no row for this labeler. `--revisit-skipped` also re-offers skipped ones. Rows of other labelers are independent.
- The optional `--notes` flag asks for a note of at most 280 chars. The server scrubs it, and it is purged after 30 days.
- It warns about prompts that are expired or expire within 48 h.

**`score --sample <file> --labeler <handle>`** (`--labeler` required, r2 item 7)
- Refuses unless every sampled task has a row (`ok` or `skipped`) from **that** labeler, unless `--allow-unlabelled` is passed. Other labelers' rows do not count.
- POSTs `/evaluate` in chunks, then polls `/evaluator-runs/{run_id}`.
- If a run ends `deferred_rate_limited`, it prints `retry_not_before` and exits non-zero, so the user reruns later. Already-scored tasks are skipped.

**`report --sample <file> --labeler <handle> [--out]`**
- **Paired complete-case cohort.** A sampled task is in the cohort iff it has:
  - a label by `--labeler` (not skipped);
  - a JEV `ok` result for `difficulty-v0`;
  - `start_complexity` with method `request-shape-v1`.
- **Every metric uses this same cohort:**
  - Spearman ρ with average ranks: JEV vs human, and RS-v1−1 vs human;
  - MAE;
  - exact and within-1 agreement (JEV rounded half-up);
  - quadratic-weighted κ;
  - a 5×5 confusion matrix;
  - mean confidence for |error| ≤ 0.5 vs > 0.5;
  - `provider_used` breakdown and total JEV cost.
- **Exclusions table**, with counts per reason. It is built from the durable progress record: `skipped` rows → `skipped_by_labeler`, no row → `not_labelled`. Only `--labeler`'s rows count, and the number of other labelers present is printed. Reasons: `not_labelled`, `skipped_by_labeler`, `jev_error`, `jev_rate_limited`, `jev_deferred`, `prompt_expired_before_scoring`, `rs1_missing`. Plus coverage = cohort / sample, the per-stratum sample vs population counts, and the counts per completion type.
- **Bootstrap:** 1,000 resamples with seed = the sample seed. Resamples with an undefined statistic are dropped and counted. If more than 10% are dropped, the CI is "unavailable".
- **Verdict:**
  - `INCONCLUSIVE — insufficient evidence` if the cohort has fewer than **40** pairs, or if any decision statistic is undefined (constant labels or scores → Spearman undefined; expected disagreement 0 → κ undefined).
  - Otherwise `PASS` iff ρ_JEV ≥ 0.5 **and** ρ_JEV − ρ_rs1 ≥ 0.10 **and** κ_JEV ≥ 0.40, else `FAIL`.
  - The thresholds and the minimum were pre-registered in the report header. Pooled metrics are sample-level (equal allocation).
- Output is Markdown with short `task_ref` prefixes and **no prompt text**. It may be committed as `Plans/task-telemetry-jev-pilot/pilot-report.md`.

**§8 Pilot data is prospective.**
- Backfilled tasks are metadata-only and never eligible.
- The pilot starts when both of these are true:
  - the Activation Gate (status.md) is passed;
  - there are at least 50 eligible completed root tasks. At least 65 is recommended, so that ≥ 40 complete pairs survive after exclusions.
- The start date comes from the D0 tasks/day measurement and is owned by the user plus Claude Code (status.md).

**§9 Demo seed (`scripts/seed_tasks_demo.py`; dev/E2E only).**
- `--db <path>` must not exist; otherwise the script refuses with a non-zero exit.
- `--variant standard|many-scored` (default `standard`).
- It inserts through SQLModel with no JEV calls. Times are relative to now.

`standard` dataset:

| Project | task_id | Hierarchy | Age | RS-v1 | Prompt | JEV ok | Other |
|---|---|---|---|---|---|---|---|
| demo-alpha | `demo-task-1` | root | 1 d | 2 | retained, contains `CANARY-PROMPT-TEXT-7731` | raw 2.2, conf 0.83, probs `[0,0,0.8,0.2,0]`, typesafe-ai | 3 tool calls; `completion=session_end`; human label 2 with note containing `CANARY-NOTE-5512` |
| demo-alpha | `demo-task-1-child` | child of demo-task-1 | 1 d | 1 | none | — | |
| demo-alpha | `demo-task-2` | root | 2 d | 4 | retained | raw 3.6, conf 0.61, probs `[0,0,0.1,0.2,0.7]`, digitalocean | `completion=next_task`; 1 priced + 1 unpriced event |
| demo-alpha | `demo-task-3` | root | 3 d | null | purged | — | all events unpriced → `cost_usd` null |
| demo-beta | `demo-task-4` | root | 2 d | 5 | retained | — | |
| demo-beta | `x"><img src=x onerror="window.__xss=1">` | root | 1 d | 3 | none | — | session_id `<svg onload="window.__xss=2">`; one `error` JEV evaluation |
| demo-beta | `demo-filler-01`…`59` | root | 1–5 d | cycling 1..5 | none (filler-59 is seeded with an **expired** prompt; the app's startup purge turns it into `purged`, r2 item 9) | — | fillers 01–10 share an identical `last_seen_at` |
| demo-beta | `demo-old` | unknown | 40 d | 3 | none | — | |

Counts:
- 7 or 30 days: 65 tasks;
- 90 days: 66;
- root-only at 30 days: 64;
- scored: 2;
- demo-alpha: 4.

The `expired`-but-unpurged UI state cannot be seeded, because the startup purge runs before serving. It is tested with an injected clock (API test) and with a stubbed list response (Playwright), both in tests-other (r2 item 9).

`many-scored` adds project `demo-gamma` with 205 scored root tasks (1–5 d, raw 0.0–4.0 cycling). That gives 207 scored at 30 days, for the chart-cap label.

**§10 Documentation (owned by this lane; see status.md maintenance-docs row).**

`docs/adr/002-task-prompt-retention-and-jev.md` covers:
- the exception to `STORE_RAW_PROMPTS=0`, and the separate `STORE_TASK_PROMPTS` flag;
- `task-redact-v1` categories and residual risk;
- producer capture-time retention and read-time enforcement;
- logical vs physical deletion, backups/WAL handling, and the no-pre-purge-backup exception;
- the rollback runbook;
- JEV data exposure to typesafe-ai and digitalocean. `no_training: all` / `zdr: none` are **descriptive gateway metadata, not a verified contractual guarantee**; provider-side deletion is not controlled;
- the fallback, Retry-After and budget reservation assumptions;
- evaluator isolation;
- the mandatory token and Host/Origin guards;
- rejected alternatives;
- an **"Approval" section** that the user fills in before activation (status.md Activation Gate).

Also update:
- `AGENTS.md`: privacy contract, module map (`routes/tasks.py`, `task_store.py`, `redaction.py`, `retention.py`, `jev_scorer.py`, `jev_pilot.py`, `pilot_metrics.py`, `scripts/purge_task_prompts.py`, `scripts/seed_tasks_demo.py`), API families, Complexity (JEV separate from RS-v1 and intensity), gotchas (`create_all` before migrations; Host allowlist).
- `README.md` (env vars, Tasks view, pilot CLI, unit `EnvironmentFile`, runbook link), `CHANGELOG.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`.
- `.github/workflows/ci.yml`: `py_compile scripts/*.py`, plus the Playwright browser job from tests-other.
- Project `CLAUDE.md` Key File Locations.

### Schema Dependencies

- All columns of `tasks`, `task_evaluations`, `evaluator_runs` and `evaluator_attempts`.
- `token_events`: `project_name`, `task_id` (+ index), `id`, `event_type`, `occurred_at`, `recorded_at`, `complexity`, `complexity_method`, `status`, `retry_count`, token columns, `cost_status`, `estimated_cost_usd`, `tags_json`, and the composition columns (write only).
- `pricing_rules` row `typesafe-ai/jev`.

### Auth and Permissions

- Single-user service; the loopback bind is the base control.
- Host allowlist applies globally.
- `require_sensitive_auth` (token always required, plus the Origin check) guards prompt reads, labels, evaluate and purge.
- Capture and JEV refuse to start without `INGEST_TOKEN`.
- `AI_GATEWAY_API_KEY` is read only from the environment and never logged, returned or stored.

### Environment Variables (new)

| Var | Default | Meaning |
|---|---|---|
| `STORE_TASK_PROMPTS` | off | Prompt capture (also requires `INGEST_TOKEN` and a valid purge interval) |
| `TASK_PROMPT_PURGE_INTERVAL_S` | `21600` | 1..21600. `0` is honoured only when no retained data exists and an all-copy purge was verified; otherwise it is refused and the default is used. This is a target lag, not a guarantee (r2 item 2) |
| `JEV_ENABLED` | off | JEV worker (also requires the key, `INGEST_TOKEN` and a valid budget config) |
| `AI_GATEWAY_API_KEY` | — | From `secrets.env` |
| `JEV_DAILY_BUDGET_USD` | `0.05` | Ceiling |
| `JEV_MONTHLY_BUDGET_USD` | `0.50` | Ceiling |
| `JEV_MAX_CALLS_PER_DAY` | `200` | Includes 429s |
| `JEV_MAX_RETRY_WAIT_S` | `60` | Longest Retry-After wait honored in-run; longer → fallback or defer |
| `TOKEN_INSPECTOR_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Host allowlist |
| `TOKEN_INSPECTOR_ALLOWED_ORIGINS` | (adds to the loopback origins) | Origin allowlist for sensitive endpoints |
| `REDACT_INTERNAL_HOST_SUFFIXES` | — | Extra internal DNS suffixes |
| `REDACT_EXTRA_TERMS` | — | Whole-word terms to redact (e.g. machine names) |

---

## Part B — Hermes producer plugin (Faz 0)

> **Implemented and tested on hermes only**, in `/home/dogukan/Projects/token_inspector` (no git remote). Take a tarball backup before editing. The plugin's `tests/` suite (25 existing tests) runs with the plugin's Python. After install, run `hermes gateway restart` from an external shell. **Hermes core is not modified.** The plugin stays bounded, short-timeout, asynchronous, fail-open and privacy-first.

### D0 plugin evidence (blocking for 0a role, 0c; owner: Claude Code on hermes; artifact: `discovery-evidence.md`)

Record field and hook names only, from redacted hook payload samples:
- which hook mints `task_id`, and whether it equals one user prompt → final stop;
- the `role` source;
- the delegation/subagent parent identifier, if any;
- the session end/finalize hook;
- whether request messages are available at `pre_llm_call`;
- the Hermes tool names that read files;
- the in-memory queue bound (for the loss bound).

If the evidence contradicts the task unit, stop and escalate (Drift Log).

### 0a — Response-metadata mapping (`mapping.py`) → AC1

- **`response_size_bytes`:** the UTF-8 byte length of `response`/`assistant_message` content if available; else `assistant_content_chars`; else the legacy names. It is size only; the text never goes on the event.
- **`tool_call_count`:** `assistant_tool_call_count`, else `len(assistant_message.tool_calls)`.
- **`role`:** from the D0-identified field. If there is none, use `"main"` for top-level turns and `"delegate"` for delegated turns.
- Legacy names stay as fallbacks.
- Every event with `complexity` also gets `complexity_method='request-shape-v1'`.

### 0b — Scrub-before-queue, durable spool, dead-letter, counters (`redact.py`, `sink.py`, `state.py`) → AC2

1. **Producer scrubbing (item 1).** Port `task-redact-v1` into `redact.py` as `scrub_text()`, identical to backend §4 and verified by the shared `redaction_vectors.json`. Apply it to `task_prompt_text` **when the event is built, before it enters the in-memory queue**. Neither memory nor spool ever holds unscrubbed prompt text.
2. **Spool layout:** `~/.local/state/token_inspector/spool/` (dir `0700`, files `0600`, created with `os.open(..., 0o600)`).
3. **Durable write (item 16):**
   1. write `<stamp>-<uuid>.json.tmp`;
   2. `flush` + `os.fsync(fd)`;
   3. `os.replace` to `.json`;
   4. `os.fsync(dir_fd)`.

   **Acknowledgement ordering:** a spool file is deleted only after a 2xx, then the directory is fsynced.
4. **Send failure** (connection error, timeout, 5xx, 429) → spool the batch. A failed spool write → `dropped_batches` +1 + WARNING. Nothing ever raises into Hermes.
5. **Replay:**
   - At the start of each flush, replay up to 5 files, oldest first.
   - 2xx → delete the file.
   - 5xx/429/timeout → keep it and stop replay for this flush.
   - **400/413/422 → move to `spool/dead-letter/`** (`dead_lettered_batches` +1, WARNING with the status code only), then **continue with the next file**. A poisoned batch never blocks the queue (item 15).
   - 401/403 → keep the file, stop replay, WARNING `auth` (a config problem, not the batch's fault).
   - A file that cannot be parsed (corrupt JSON) → dead-letter + `corrupt_batches` +1.
   - A 2xx with an unparseable body → treat as delivered (the server is idempotent), delete, `malformed_acks` +1.
6. **Bounds:**
   - spool ≤ 1,000 files / 50 MiB: overflow deletes the oldest, `dropped_batches` +1;
   - dead-letter ≤ 200 files;
   - telemetry-only files (no prompt text) in either folder older than 30 days are deleted (`expired_batches` +1).
6a. **Capture-time based prompt expiry (r2 item 3).**
   - **Envelope.** Every spool/dead-letter file is an envelope `{"format":2, "spooled_at", "prompt_captured_at_min", "events":[…]}`, where `prompt_captured_at_min` is the minimum `task_prompt_captured_at` of the events carrying a prompt, or null.
   - **Maintenance** runs at plugin startup and at the start of every flush, bounded to 50 files per pass. For each event whose `task_prompt_captured_at + 30 days ≤ now`:
     - **strip** `task_prompt_text` and keep all non-content telemetry;
     - rewrite the file durably (tmp + fsync + replace + dir fsync) and recompute `prompt_captured_at_min`;
     - `expired_prompts_stripped` +1 per event.
   - **Before spooling**, and before a dead-letter move, the same strip is applied. An event that waited in memory past its expiry is therefore never written with its prompt.
   - **Orphan `*.json.tmp`** files, left by a crash before rename, are handled at startup:
     - if it parses as a valid envelope, strip expired prompts and complete the rename, recovering the telemetry (`recovered_tmp` +1);
     - otherwise delete it (`corrupt_tmp_deleted` +1).

     A `.tmp` file never survives a startup.
7. **Non-retryable direct sends** (400/413/422) → dead-letter as well. Per-item `rejected` counts in 2xx responses → `rejected_events`. Queue-full drops → `dropped_batches` +1.
8. **Counters** live in `~/.local/state/token_inspector/counters.json`, written with the same fsync protocol: `sent_batches`, `sent_events`, `spooled_batches`, `replayed_batches`, `dropped_batches`, `dead_lettered_batches`, `corrupt_batches`, `malformed_acks`, `expired_batches`, `expired_prompts_stripped`, `recovered_tmp`, `corrupt_tmp_deleted`, `rejected_events`, `spool_pending`, `updated_at`. At startup, `spool_pending` is **recomputed from the directory** (reconciliation after a crash). A WARNING is logged whenever a drop, reject or dead-letter counter increases.
9. **Documented loss bound:** events still in the in-memory queue (the D0-recorded bound) plus the one in-flight batch can be lost on crash or power loss. Everything spooled survives.
10. All spool I/O runs on the plugin's existing background flush path, never on a hook thread.

### 0c — Capture fields for Faz 2 (gated on backend readiness) → AC9, AC10

- **Backend readiness gate (item 17).**
  - The plugin calls `GET /api/meta` (short timeout) at startup and every 10 minutes.
  - `task_prompt_text` is included **only if** the last successful probe returned `schema_version ≥ 10` **and** `task_prompt_capture == true`, **and** plugin config `capture_task_prompt` (default **false**) is on.
  - A failed or old probe means capture is off (fail closed for content). Telemetry itself stays fail-open.
- **`task_prompt_text`:** scrubbed (0b.1), capped at 200,000 chars, sent once per task on its first `llm_request`, with `task_prompt_captured_at` = the time the user prompt was observed.
- **Hierarchy:** `task_hierarchy` (`root`/`child`/`unknown`) plus `parent_task_id`/`root_task_id` from the D0-identified delegation data. If that data is unavailable, send `unknown`, and those tasks are excluded from the pilot.
- **Session end:** the session end/finalize hook (D0) emits `event_type='session'` with `tags.session_phase='end'`.
- **Request composition** on `llm_request`, as counts only:
  - `request_system_chars`;
  - `request_history_chars` (prior user/assistant turns; excludes the current user message);
  - `request_tool_output_chars` (tool results from non-file-read tools);
  - `request_file_content_chars` (results of D0-listed file-read tools);
  - `request_file_ref_count` (distinct paths referenced by file-read calls; paths are never sent);
  - `request_tool_names` (distinct names of tools whose results appear in the request, ≤32).

---

## Acceptance Criteria

**Plugin mapping and spool**
- **AC1:** Recorded hook payloads with the current Hermes field names map to non-null `response_size_bytes` / `tool_call_count` and to `role` per the D0 rule. No response text reaches the event. Legacy payloads still map. After deploy, new prod rows are populated (read-only check).
- **AC2a:** Backend down → the batch is spooled with fsync; the next successful flush replays and deletes it, with no server duplicates.
- **AC2b:** A replayed 400/413/422 or corrupt file is dead-lettered and the files behind it still replay. 401/403 keeps the file.
- **AC2c:** A spooled prompt canary secret is absent from the spool and dead-letter bytes (scrubbed before queueing). Files are `0600`, the dir is `0700`.
- **AC2d:** Overflow, expiry, queue-full, dead-letter and malformed-ack each increment their counter, and the drop/reject paths log a WARNING. `spool_pending` is reconciled at startup. No path raises into Hermes.

**Task derivation**
- **AC4c:** An ingested event with `task_id` upserts one task with correct first/last seen. A duplicate replay changes nothing. No task is created without `task_id`.
- **AC4d:** Start complexity comes only from events with a method, with `(occurred_at, id)` ordering identical to the backfill (equal timestamps give the lowest id).
- **AC10a:** `hierarchy_status` follows the rules (child never downgraded). The `next_task` and `session_end` completions are set. The API shows `inferred` after 60 minutes of inactivity.

**Prompt capture and retention**
- **AC5b:** Capture enabled → `task_prompt_text` is scrubbed and truncated into `tasks` only, never into `token_events`. First write wins. A purged prompt is never repopulated. `expires_at` = producer `captured_at` + 30 d, never extended. A prompt whose capture time is already older than 30 d is discarded.
- **AC5c:** Expired-but-unpurged text is not returned by `include_prompt`, is excluded from selection and is never sent to JEV.
- **AC5d:** The purge nulls expired prompts and notes older than 30 d (no row deletes), runs `wal_checkpoint(TRUNCATE)`, and re-purges + VACUUMs backups. `retention-status` reports the outcome. `dry_run` changes nothing. No text is logged.
- **AC5e:** `scripts/purge_task_prompts.py --all --include-backups` works without app imports, on the live DB and the backups.
- **AC5f:** ADR-002 and AGENTS.md document the exception, the categories, the retention mechanics and limits, the provider exposure and the Approval section.

**JEV worker**
- **AC6a:** JEV not enabled or credentials missing → zero HTTP calls and `enabled:false`. Token missing or invalid budget config with the flag on → refused, with an ERROR log and `disabled_reason`.
- **AC6b:** Atomic run reservation: a concurrent second `/evaluate` → 409. `run_id` is returned. Startup aborts stale runs and marks reserved attempts `uncertain`.
- **AC6c:** An `evaluator_attempts` row is committed before every HTTP call and reconciled after it. The daily, monthly and call ceilings stop the run before an exceeding call, with uncertain and failed attempts counted at reserved cost.
- **AC6d:** Retry-After is honored exactly (seconds or HTTP-date). Waits over the allowance fall back (typesafe-ai) or defer the run (digitalocean). There are at most 2 attempts per provider across mixed errors. Fallback happens only after a typesafe-ai 429. 401/403 stops the run.
- **AC6e:** The parser reads the real fixture paths. Validation rejects non-finite, out-of-range, wrong-cardinality or legend-mismatched responses.
- **AC6f:** Evaluator usage is recorded as a `token-inspector` `token_events` row with `task_id` NULL and is never scored. An unchanged input is `already_scored`.

**Round-2 additions (each owned by a tests-other test; no further review round)**
- **AC2e (r2 item 3):** Spool, dead-letter and orphan `.tmp` files never hold a prompt past `captured_at + 30 d`:
  - expired prompts are stripped, with telemetry kept, at startup, at each flush, and before spooling or dead-lettering;
  - an orphan `.tmp` is recovered or deleted at startup.
- **AC4e (r2 item 6):** One start-complexity candidate rule is used in both paths. Explicit non-approved methods are never chosen. The `-inferred` exception is backfill-only.
- **AC10d (r2 item 5):** Completion is recomputed per project+session from stored timestamps after every event with a `session_id`, including task-less session-end markers. Reversed arrival gives the same result as the backfill.
- **AC5g (r2 item 1):** The WAL checkpoint is retried from the persisted `pending_wal_checkpoint` flag, even when no new rows expire.
- **AC5h (r2 item 2):**
  - Retention runs whenever retained data exists, regardless of capture.
  - Interval 0 is refused unless no data remains and an all-copy purge was verified.
  - Failures surface as `last_purge_ok=false` and `oldest_overdue_expires_at`.
- **AC6g (r2 item 4):** Provider cooldowns persist across tasks, runs and restarts, and are enforced before every call. `/evaluate` returns `retry_not_before` when both providers are cooling.
- **AC8d (r2 item 7):** Labelling progress (labelled / skipped / not labelled) is persisted per labeler. `label` resumes. `score` requires `--labeler` and counts only that labeler's rows.
- **AC11d (r2 item 8):** With the flags off and a valid configuration, `/api/meta` reports `config_errors: []` and `disabled_reason='not_enabled'`. A bad configuration lists its errors even while the flags are off.
- **AC7m (r2 item 13):** `projects` ignores all filters except `days`. `children[]` has the documented element shape, including `jev_raw_score`.

**API and security**
- **AC7a:** The API shapes, the stable ordering, the out-of-range page, the `cost_usd` null rule, `prompt_state`, `completion` and the `jev` block are as specified. Prompt text never appears without an authenticated `include_prompt`.
- **AC11a:** `require_sensitive_auth`: token unset → 403 `auth_not_configured`; missing or wrong header → 401; foreign Origin → 403.
- **AC11b:** A foreign `Host` → 400.
- **AC11c:** Capture with the flag on and no token → the prompt is discarded, the ERROR is logged, and `/api/meta` shows capture false with a reason.

**Pilot CLI**
- **AC8a:** `select` is deterministic per seed and uses only eligible tasks: root, completed or inferred, retained ≥ 5 d, RS-v1 method, not token-inspector. It records strata and completion counts.
- **AC8b:** `label` is blind, escapes terminal controls and stores 0–4 (plus the optional scrubbed note). `score` refuses while labels are missing, and handles a deferred run.
- **AC8c:** `report` uses one paired cohort, reports exclusions and coverage, and is `INCONCLUSIVE` below 40 pairs or with undefined statistics. Otherwise it gives PASS or FAIL against the thresholds. There is no prompt text in the output.

**Composition and readiness gate**
- **AC9a:** Plugin composition attribution is correct on synthetic messages (system/history/tool/file/file-ref/tool names), with no content or paths.
- **AC9b:** The backend validates and stores the composition fields.
- **AC10b:** The plugin sends `task_prompt_text` only when the meta probe shows schema ≥ 10 and capture on, and the config flag is on.
- **AC10c:** Delegation children carry `parent_task_id`/`root_task_id`/`task_hierarchy='child'`. Otherwise `unknown`.

## Out of Scope

- Faz 1 and the Windows→hermes path.
- Faz 4.
- Automatic or scheduled JEV.
- Dashboard label entry.
- Composition analytics.
- traceparent remapping and cross-source dedup.
- Plugin TTFT.
- Re-deriving historical `role`/size/tool-count values.
- Dashboard spool counters.
- JEV questions other than `difficulty`.
- Erasure at the JEV providers.
- Backups outside the DB directory.
- A full-population chart endpoint.
