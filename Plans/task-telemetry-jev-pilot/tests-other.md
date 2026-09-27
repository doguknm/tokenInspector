# Tests (Non-UI + automated browser) — Task-Level Telemetry Pilot

**Lane**: tests-other
**Tool**: Claude Code (Opus 5.5, direct, no handoff) — pytest/vitest for unit/integration; Playwright for any automated
browser suites (Playwright belongs to this lane only, never to tests-e2e)
**Status**: not started
**Brief**: ./2026-09-27-summary.md
**Revision**: r1 (2026-09-27), plan-review round 1 fixes applied (all items with test impact: 1–29)
**Revision**: r2 (2026-09-27), plan-review round 2: every fix (items 1–13) has an owned test below, marked `(r2 item N)`. Round-2 fixes are verified by these tests, not by another review. **Plan LOCKED** — later changes go to the status.md Drift Log only.

## Goal

Verify the following without a human-driven browser:
- migration v10: pinned genuine v9 input, row-by-row preservation, backup verification, interrupted and rollback paths;
- task derivation: provenance, deterministic ordering, hierarchy, completion;
- security guards;
- prompt capture and retention (live DB, WAL, backups, read-time expiry, notes, standalone purge);
- the JEV worker: run reservation, durable budget ledger, exact Retry-After, strict parsing against the real fixture;
- the Tasks API;
- the pilot CLI: selection, blind labelling, paired-cohort report;
- the Hermes plugin (mapping, scrub-before-queue, fsync spool, dead-letter, meta gate, composition);
- Playwright stub scenarios for UI states that need forced responses.

## Environment and Conventions

- **Backend tests** use pytest (`asyncio_mode = auto`) in `tests/`, with the `tests/conftest.py` `client` fixture: temp `DB_PATH`, `lifespan(app)`, `httpx.ASGITransport`.
  - Extend the fixture cleanup, in FK order: `EvaluatorAttempt`, `TaskEvaluation`, `EvaluatorRun`, `Task`, `ProviderCooldown`, `RetentionState`, then the existing `TokenEvent`, `ModelAlias`.
  - Set `TOKEN_INSPECTOR_ALLOWED_HOSTS=127.0.0.1,localhost,test`.
  - Leave `STORE_TASK_PROMPTS`, `JEV_ENABLED`, `AI_GATEWAY_API_KEY` and `INGEST_TOKEN` unset by default. Tests enable them via `monkeypatch`; when a test enables capture or JEV, it sets `INGEST_TOKEN` to a 32-char test value.
  - Set `TASK_PROMPT_PURGE_INTERVAL_S=0` unless a test enables capture, in which case use a valid small value.
- **Commands (Windows):** `python -m pytest -q`, `python -m py_compile *.py routes/*.py scripts/*.py`, `node --check static/app.js`. Always `python`. On hermes: `.venv/bin/python -m pytest -q`.
- **No real network.**
  - The JEV client uses `httpx.MockTransport`.
  - `asyncio.sleep` is monkeypatched to record requested delays.
  - The "now" clock is injectable, so Retry-After HTTP-date and expiry tests are deterministic.
- **JEV fixture:**
  - `tests/fixtures/jev_response_2026-09-27.json` is a verbatim copy of `Plans/task-telemetry-jev-pilot/fixtures/jev-response-2026-09-27.json`, a real response captured 2026-09-27 on hermes, with no secrets.
  - Tests use its `response` object for the 200 path and its `rate_limited_response_example.body_prefix` shape (`error.type == "rate_limit_exceeded"`) for 429 bodies.
  - The capture **did not record whether 429 carried `Retry-After`**, so tests cover a present delta-seconds value, an HTTP-date, an absent header and a malformed header.
- **Pinned v9 schema:** `tests/fixtures/schema_v9.sql` is generated once from commit `0aab993` (dump of `sqlite_master.sql` after v9 `init_db` on an empty file) and checked in.
  - Migration tests build the "v9 DB" by executing this SQL, inserting `schema_migrations` rows 1–9 and inserting representative rows: several `task_id`s, events without `task_id`, `llm_request` + `tool_call`, mixed complexity including equal timestamps, priced and unpriced, session events.
  - **Before `init_db()`, assert that the new tables and the 7 new columns are absent.**
- **Shared redaction vectors:** `tests/fixtures/redaction_vectors.json` has a byte-identical copy in the plugin repo.
- **Plugin tests** run on **hermes**, in the plugin repo `tests/`, with its Python. Results are pasted into status.md.
- **Playwright:** a small Python `pytest-playwright` suite, `tests/browser/`, marked `browser`.
  - It starts uvicorn on a free port against a seeded demo DB (`scripts/seed_tasks_demo.py`) and uses `page.route` to stub or delay responses.
  - It is skipped with `pytest.importorskip("playwright")` when Playwright is not installed.
  - CI gets a job step: `python -m pip install -r requirements-dev.txt`, `python -m playwright install --with-deps chromium`, `python -m pytest -m browser`. Add `pytest-playwright` to `requirements-dev.txt`.

## Test Plan by Acceptance Criterion

### Database / migration

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC3a | database | integration | pytest | Fresh DB → `init_db()`: `schema_migrations` max = 10; the six new tables have exactly the specified columns and indexes, including the partial `WHERE` text in `sqlite_master.sql` |
| AC3b | database | integration | pytest | Pinned v9 DB (new tables and columns asserted absent first) → `init_db()`. For **every** pre-existing `token_events` row, keyed by `id`, **every v9 column value** is identical before and after (full-row dict comparison). The 7 new columns are NULL |
| AC3b | database | manual-scripted | python (hermes) | Take an online-backup clone of prod, run `PRAGMA integrity_check`, then `init_db()` with `DB_PATH` = clone. Run the same full-row comparison over all 9,146 rows. Record the result in status.md |
| AC3c | database | integration | pytest | Fresh vs migrated-v9: `sqlite_master.sql`, `table_info` and `index_list` are identical for the six new tables and `token_events` |
| AC3d | database | integration | pytest | WAL-resident row (`wal_autocheckpoint=0`, open writer) → `backup_database_if_needed` produces a `.bak-v9-*` that passes `integrity_check`, matches the `token_events` count and contains the row |
| AC3d | database | integration | pytest | Monkeypatch the verification to see a corrupt or short backup (e.g. truncate the target after the copy) → `init_db()` raises **before** `create_all`. The DB is still v9 (`schema_migrations` max 9, no new tables) and the source file is unchanged byte-for-byte |
| AC3d | database | unit | pytest | Returns `None` when already v10, the file is missing or the file is empty |
| AC3e | database | integration | pytest | Rollback SQL on a migrated DB leaves the `token_events` schema equal to the pinned v9 and removes the new tables. Skip with a reason if SQLite < 3.35 |
| AC3e | database | integration | manual-scripted (CI-able) | **v9 application on a v10 DB:** `git worktree add <tmp>/v9 0aab993`, set `DB_PATH` to a migrated v10 copy, run the v9 app via its `lifespan` + ASGI client (a subprocess using the worktree's Python path). Assert startup succeeds, `POST /api/events` returns 201 and `GET /api/analytics/summary` returns 200 |
| AC3f | database | integration | pytest | **(r2: explicit migration transaction)** Two injection points, each on a pinned v9 DB:<br>(a) monkeypatch `run_migrations` to raise **after `create_all` but before `_m10`**;<br>(b) monkeypatch `_m10` to raise **after creating `tasks` and adding 3 of the 7 columns**.<br>In both cases `init_db()` raises, `schema_migrations` max stays 9, and none of the 6 new tables or 7 new columns exist (proves the dedicated migration engine really emits `BEGIN IMMEDIATE`). Remove the patch and `init_db()` succeeds |
| AC4a | database | integration | pytest | Backfill checks:<br>• one task per distinct non-empty `(project_name, task_id)`, with MIN/MAX timestamps;<br>• `start_complexity` = earliest `(ts, id)` llm_request;<br>• method: `request-shape-v1` with the tag, `request-shape-v1-inferred` under the D0-approved flag, else NULL complexity;<br>• `hierarchy_status='unknown'`, `source='backfill'`;<br>• `completion='next_task'` for all but the last task per session;<br>• **`prompt_text`, `prompt_hash`, `prompt_length`, `prompt_redaction_version`, `prompt_captured_at`, `prompt_expires_at`, `prompt_purged_at` IS NULL, and `prompt_truncated = 0`** (item 23);<br>• `id = sha256(project+"\x1f"+task_id)[:32]` |
| AC4b | database | integration | pytest | Running `init_db()` twice, or `_m10` twice, gives no duplicates and leaves `updated_at` unchanged |
| AC4e | database | integration | pytest | **Mixed methods (r2 item 6)**, in the backfill and in live ingest (same events POSTed to a fresh DB):<br>• sequence [t1 `ai-v0` c=5, t2 `request-shape-v1` c=2] → both paths choose 2 with method `request-shape-v1`;<br>• [t1 `request-shape-v1` c=3, t2 `ai-v0` c=1] → both choose 3;<br>• [t1 NULL method c=4, t2 `request-shape-v1` c=2] → ingest chooses 2. The backfill chooses 4 with `request-shape-v1-inferred` only when `BACKFILL_INFER_RS1=True`, else 2;<br>• all non-approved → NULL complexity and method in both paths |
| AC4d | database | integration | pytest | Two llm_request events with the **same timestamp** and different complexity: the backfill and the live ingest (same events POSTed to a fresh DB) choose the same value, the one with the lower `id` |
| AC5a | database | unit | pytest | `PRAGMA secure_delete` = 1 on a new connection |

### Security guards

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC11a | backend | integration | pytest | `INGEST_TOKEN` unset: `include_prompt=true`, `POST labels`, `POST evaluate` and `POST purge-expired` each → 403 `auth_not_configured` |
| AC11a | backend | integration | pytest | Token set, header missing → 401. Wrong header → 401. Correct header → success |
| AC11a | backend | integration | pytest | Token set with a correct header but `Origin: http://evil.example` → 403 `origin_not_allowed`. `Origin: http://127.0.0.1:8100` → allowed |
| AC11b | backend | integration | pytest | `Host: evil.example` on `GET /api/tasks` and `POST /api/events` → 400. `Host: localhost:8100` → OK |
| AC11c | backend | integration | pytest | `STORE_TASK_PROMPTS=1` without `INGEST_TOKEN`: startup logs an ERROR containing `ingest_token_missing`; `/api/meta` shows `task_prompt_capture:false` with that reason; an event carrying `task_prompt_text` stores no prompt |
| AC11c | backend | integration | pytest | `STORE_TASK_PROMPTS=1` with a token but `TASK_PROMPT_PURGE_INTERVAL_S=0` (or 30000) → capture refused with `invalid_purge_interval` |
| AC11d | backend | integration | pytest | **Preflight (r2 item 8):**<br>• all flags off, valid `INGEST_TOKEN`, valid budget and interval → `/api/meta` has `config_errors == []`, `task_prompt_capture_disabled_reason='not_enabled'`, `jev_disabled_reason='not_enabled'`;<br>• flags off with no token → `config_errors` contains `{capture, ingest_token_missing}` and `{jev, ingest_token_missing}`;<br>• flags off with daily budget > monthly → `{jev, invalid_budget_config}`;<br>• flags on with a valid config → `config_errors == []` and both disabled reasons null |
| AC6a | backend | integration | pytest | `JEV_ENABLED=1` + key without `INGEST_TOKEN` → `evaluator-status.enabled=false` with `disabled_reason='ingest_token_missing'` and an ERROR log. Invalid budget config (negative, NaN, daily > monthly, max calls 0) → `invalid_budget_config` |

### Ingest / derivation / capture

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC4c | backend | integration | pytest | An llm_request with `task_id=T`, `complexity=3`, `complexity_method='request-shape-v1'` creates one task: `source='ingest'`, `start_complexity=3` |
| AC4c | backend | integration | pytest | An event with complexity but **no method** (neither field nor tag) never sets `start_complexity` |
| AC4c | backend | integration | pytest | An out-of-order earlier event (with method) moves `first_seen_at` and replaces `start_complexity`. A `tool_call` never changes it |
| AC4c | backend | integration | pytest | A duplicate replay leaves the task row byte-identical. No/empty `task_id` → no task. 20 concurrent distinct events for one task → exactly 1 row |
| AC10a | backend | integration | pytest | Hierarchy:<br>• `task_hierarchy='root'` → root;<br>• an event with `parent_task_id` → child;<br>• a later `task_hierarchy='root'` event does **not** downgrade child;<br>• no hierarchy info → unknown |
| AC10d | backend | integration | pytest | **Arrival-order independence (r2 item 5):**<br>• POST B (session S, first_seen 10:00), then the older A (S, 09:00) → A becomes `next_task` with `completed_at` 10:00, and B is NULL/open;<br>• POST the session-end marker (S, 11:00, **no `task_id`**) first, then A and B → B becomes `session_end` at 11:00 and A `next_task`;<br>• a marker at 08:00 (before B's first_seen) does not mark B;<br>• for each permutation of 3 tasks + 1 marker, the final `completion`/`completed_at` equal the backfill's result on the same rows;<br>• sessions with the same `session_id` in different projects do not affect each other |
| AC10a | backend | integration | pytest | Completion:<br>• creating task B in session S marks earlier task A `next_task` with `completed_at` = B.first_seen;<br>• a session event with `tags.session_phase='end'` marks the open tasks in S `session_end`;<br>• the API shows `inferred` for a task idle more than 60 minutes (injected clock), otherwise `open` |
| AC5b | backend | integration | pytest | Capture enabled; the prompt contains every vector category. `tasks.prompt_text` equals `scrub_text(input)`. `token_events.prompt_text/prompt_hash/prompt_length` are NULL. `score_complexity` is not called, even with `STORE_RAW_PROMPTS=1` (spy) |
| AC5b | backend | integration | pytest | `task_prompt_captured_at` = now − 3 d → `prompt_expires_at` = captured + 30 d, not now + 30 d. Captured now − 31 d → the prompt is discarded. Captured in the future (+1 h) → clamped to now. A missing value → the event's `occurred_at` is used |
| AC5b | backend | integration | pytest | First write wins. After a purge, a replay does not repopulate. 40,000 chars → stored 32,000 with `prompt_truncated=1` and `prompt_length=40000`. 200,001 chars → 422 (single) or a per-item reject (batch) |
| AC5b | backend | integration | pytest | Capture disabled (flag unset): the task has the nullable prompt fields NULL and `prompt_truncated=0` (item 23) |
| AC5c | backend | integration | pytest | Read-time enforcement: set `prompt_expires_at` = now − 1 s without purging. `include_prompt=true` returns `prompt_text: null`, `prompt_state='expired'` and `has_prompt=false`. `GET /api/tasks?has_prompt=true` excludes the task. `/evaluate` skips it as `prompt_expired` with 0 HTTP requests |
| AC5b | backend | unit | pytest | `scrub_text` against `redaction_vectors.json`. Positives: PEM, each key shape, JWT, bearer, secret assignment (keeps the name), **git remotes** (`git@github.com:org/repo.git`, `ssh://git@host/x.git`, `https://gitlab.corp/x/y.git`), URL userinfo, `/home`, `/Users`, `C:\Users\x\`, **absolute paths** (`/srv/private/workspace`, `/etc/hosts`, `D:\work\x`), **internal hosts** (`build01.corp`, `nas.local`, `hermes.tailnet-x.ts.net`, a `REDACT_EXTRA_TERMS=hermes` whole word), each private IPv4 range including 100.64/10, email. Negatives: a git SHA, `8.8.8.8`, `https://example.com/a/b` (path inside a public URL untouched), a relative path `src/app.js`, the word "token", "hermeses" (not a whole word). Idempotent |
| AC9b | backend | integration | pytest | The composition fields are stored. Tool names are deduplicated, sorted and JSON-encoded. 33 names → 422. A name with a space → 422. Negative counts → 422. `complexity_method` is stored on `token_events` |

### Retention

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC5d | backend | integration | pytest | Tasks set up as expired, fresh and already-purged, plus a human note older than 30 d and one fresh note. `dry_run` changes nothing. A real run nulls only the expired prompt and the old note, sets `prompt_purged_at`, and deletes no rows. `prompt_hash`, `prompt_length`, complexity and evaluations are unchanged |
| AC5g | backend | integration | pytest | **Checkpoint retry without new expiries (r2 item 1):**<br>1. Expire a prompt canary. Hold an open reader transaction and run the purge → the text is NULL and **committed**, the checkpoint is busy, `pending_wal_checkpoint='1'` and `last_checkpoint_ok=false`.<br>2. Close the reader. Run the purge again with **no new expiries** (0 rows changed) → TRUNCATE is attempted and succeeds, the flag is `'0'`, the `-wal` file size is 0, and the canary bytes are absent from the `-wal` and main files.<br>3. Variant: restart the app between steps 1 and 2 → the flag survives and the startup purge completes the truncation |
| AC5h | backend | integration | pytest | **Retention independent of capture (r2 item 2):**<br>• `STORE_TASK_PROMPTS` unset, one retained unexpired prompt, `TASK_PROMPT_PURGE_INTERVAL_S=0` → the loop is still scheduled at 21600, an ERROR is logged, and retention-status shows `scheduled=true`, `interval_refused=true`, `retained_data_present=true`;<br>• run `scripts/purge_task_prompts.py --all --include-backups --verify`, then restart with interval 0 → accepted (`scheduled=false`, `all_copy_purge_verified_at` set);<br>• a retained note only (no prompts) also keeps the loop scheduled;<br>• a forced purge failure with an overdue prompt → `last_purge_ok=false`, `oldest_overdue_expires_at` = that prompt's expiry, and the API still returns `prompt_state='expired'` with no text |
| AC5d | backend | integration | pytest | After a real purge, `PRAGMA wal_checkpoint(TRUNCATE)` was issued (spy on executed SQL). The `-wal` file size is 0 afterwards when no readers are open. With a concurrent open reader transaction, `last_checkpoint_ok=false` is reported and no exception is raised |
| AC5d | backend | integration | pytest | Backup re-purge: create `<db>.bak-v10-<stamp>` holding an expired prompt canary. Run the purge. The backup's `prompt_text` is NULL, the canary bytes are **absent from the backup file** (grep the raw bytes after VACUUM), and `backups_repurged=1`. A backup without a `tasks` table is skipped (`backups_scanned` counts it) |
| AC5d | backend | integration | pytest | The live DB file bytes no longer contain an expired-prompt canary after purge + checkpoint (`secure_delete`) |
| AC5d | backend | integration | pytest | `/api/tasks/retention-status` reflects the last run. A forced failure (monkeypatch) sets `last_purge_ok=false` and `last_error`, and logs an ERROR. The startup purge runs before the app serves its first request. The loop is cancelled cleanly on shutdown |
| AC5d | backend | unit | pytest | The purge log line (caplog) contains counts and never contains prompt or note substrings |
| AC5e | backend | integration | pytest | `--verify` writes `all_copy_purge_verified_at` only when no prompt or note remains in the DB and backups; with a backup still holding text (made read-only) it exits non-zero and writes nothing (r2 item 2). Base check: `scripts/purge_task_prompts.py --db <copy> --all --include-backups` (subprocess): all prompts and notes are NULL in the DB and its backups. `--dry-run` changes nothing. `--expired` touches only expired rows. The script source imports no app modules (AST check) |
| AC5f | backend | static | pytest | ADR-002 exists and contains the sections "Approval", "Logical vs physical deletion", "Provider exposure" and "Rollback runbook". `AGENTS.md` mentions `STORE_TASK_PROMPTS`, `INGEST_TOKEN`, `/api/tasks` and 30 days |

### JEV worker

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC6a | backend | integration | pytest | Not enabled / key empty → `/evaluate` returns 200 with `enabled:false`, all refs `jev_disabled`, 0 transport requests and no `evaluator_runs` or `evaluator_attempts` rows |
| AC6b | backend | integration | pytest | Two concurrent `/evaluate` calls: exactly one 202 with a `run_id` and one 409. The 409 created no run. `GET /evaluator-runs/{run_id}` goes `queued` → `running` → `done` |
| AC6b | backend | integration | pytest | Startup recovery: a seeded `running` run and a `reserved` attempt → after lifespan start, the run is `aborted` (`restart`) and the attempt is `uncertain` |
| AC6c | backend | integration | pytest | Durable reservation: the transport handler asserts that, **at the moment the request arrives**, a committed `evaluator_attempts` row with `status='reserved'` exists (read via a separate session). After the response the row is `succeeded`, `actual_input_tokens=446` and `actual_cost_usd=0.000018732` (from the fixture) |
| AC6c | backend | integration | pytest | Crash simulation: the transport raises `SystemExit`-like cancellation after the reservation. On restart the attempt is `uncertain`, and the budget still counts its reserved cost and the call |
| AC6c | backend | unit | pytest | `reserved_tokens = len(body_bytes) + 1024` ≥ the fixture's 446 for the fixture-sized request. `reserved_cost = reserved_tokens × 0.042/1e6`. When the reported cost exceeds the reservation, a WARNING is logged and the spend uses the actual value |
| AC6c | backend | integration | pytest | Ceilings: a daily budget just above one reservation with 3 tasks → exactly 1 request, `stop_reason='budget_exceeded'`. `JEV_MAX_CALLS_PER_DAY=2` with 429s → 2 requests. A monthly ceiling with pre-seeded month spend → 0 requests. Failed/uncertain attempts are counted at reserved cost |
| AC6d | backend | integration | pytest | 429 with `Retry-After: 7` → recorded sleep exactly 7.0, then a retry of typesafe-ai. 429 with an HTTP-date 20 s ahead (injected clock) → sleep 20. A malformed header → backoff 2 s. Absent → 2 then 4 |
| AC6d | backend | integration | pytest | typesafe-ai 429 with `Retry-After: 120` (above the allowance of 60) → **no** sleep and **no** typesafe-ai retry; the next request is `only:['digitalocean']`. digitalocean then 429 with `Retry-After: 120` → run `stopped`, `deferred_rate_limited`, `retry_not_before` = now + 120, the task evaluation `deferred`, and the remaining tasks get 0 requests |
| AC6d | backend | integration | pytest | Per-provider limit: typesafe-ai 500 then 429 → 2 attempts used, last failure 429 → fall back. typesafe-ai 429 then 500 → last failure 5xx → `error` `http_5xx`, with **no** digitalocean request. Never more than 2 requests per provider or 4 per task |
| AC6g | backend | integration | pytest | **Persisted cooldowns (r2 item 4):**<br>• bypass 1 (across tasks): task 1 typesafe-ai 429 `Retry-After: 120` → digitalocean 200. Task 2's **first** request goes to digitalocean, with **no** typesafe-ai request, while the cooldown is active (injected clock);<br>• bypass 2 (new run): a run is deferred with both providers cooling. A new `/evaluate` before `retry_not_before` → 200, nothing queued, `retry_not_before` returned, 0 transport requests;<br>• restart: after a lifespan restart the `provider_cooldowns` rows persist and the same checks hold;<br>• a remaining wait ≤ 60 s → a recorded sleep of exactly the remaining seconds, then the call;<br>• after the clock passes `not_before` → typesafe-ai is called again first |
| AC6d | backend | integration | pytest | 401 on task 1 of 3 → `auth_error`, run `stopped`, and 0 requests for tasks 2–3. Other 4xx → `http_4xx`, and the run continues |
| AC6e | backend | unit | pytest | Parsing the real fixture gives score 2.2, confidence 0.83, probabilities `[0,0,0.8,0.2,0]`, 5 legend strings equal to the criteria, and `provider_used` = `finalProvider` ('typesafe-ai'), with no WARNING |
| AC6e | backend | unit | pytest | Fixture variants rejected as `invalid_response` with nothing numeric stored:<br>• probabilities with a negative value summing to 1;<br>• NaN or Infinity score;<br>• confidence 1.2;<br>• 4 levels;<br>• 6 levels;<br>• legend text mismatch;<br>• missing `"3"` key;<br>• `type` ≠ score;<br>• a boolean score;<br>• negative `usage.input_tokens`;<br>• non-numeric `gateway.cost` |
| AC6e | backend | unit | pytest | The request body has `model`, the `state` = redacted prompt, 5 criteria identical to the fixture legend, `providerOptions.gateway.only`, and `Authorization: Bearer <test key>` |
| AC6f | backend | integration | pytest | After an ok evaluation there is one `token_events` row with project `token-inspector`, role `evaluator`, tag `purpose=evaluator` and `task_id`/`session_id` NULL, and the `tasks` count is unchanged. A task in project `token-inspector` → `evaluator_task`, 0 requests. Re-evaluating an unchanged input → `already_scored`. The `typesafe-ai/jev` pricing rule is seeded |
| AC6 | backend | unit | pytest | The test key string never appears in logs (caplog), in responses, or in the raw DB file bytes |

### Tasks API

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC7a | backend | integration | pytest | `TaskItem` keys and aggregates are hand-verified. `cost_usd` is null with only unpriced events. `prompt_state` covers all 4 values. `completion` covers all 4 values |
| AC7a | backend | integration | pytest | Filters: `days`, `project`, `evaluated`, `has_prompt` (retained only), `root_only` (`hierarchy_status='root'`, unknown excluded). `page_size` is clamped. `projects` is sorted |
| AC7a | backend | integration | pytest | **Stable order:** 15 tasks with an identical `last_seen_at`, `page_size=4`. The union of all pages = all 15 with no duplicates, ordered by `task_ref` asc within the tie. A page past the end → `items: []` with the correct `total` |
| AC7a | backend | integration | pytest | The `jev` block uses the latest `ok` evaluation (an older ok plus a newer error → the older ok). `display_score = raw+1`. 5 probabilities |
| AC7a | backend | integration | pytest | Privacy: the prompt canary and the note canary are absent from the JSON of list, detail (without prompt), status, runs and retention-status. `note` is never a key in `evaluations` |
| AC7m | backend | integration | pytest | **Contracts (r2 item 13):**<br>• `GET /api/tasks?project=demo-alpha&evaluated=true&root_only=true` still returns `projects` = every project with tasks in the `days` window (e.g. `['demo-alpha','demo-beta']`);<br>• detail `children[]` elements have exactly the keys `task_ref, task_id, hierarchy_status, start_complexity, jev_raw_score, first_seen_at`, where `jev_raw_score` equals the child's latest ok raw score or null, ordered `first_seen_at` asc;<br>• 201 children → 200 returned and `children_truncated=true` |
| AC7a | backend | integration | pytest | Detail `children` are matched within the same project only. Unknown ref → 404. Static routes (`evaluator-status`, `evaluator-runs/{id}`, `retention-status`) are not captured by `{task_ref}` |
| AC8d | backend | integration | pytest | **Durable labelling progress (r2 item 7):**<br>• POST `skipped:true` → row status `skipped`, label NULL. A later label replaces it (one row). Both `label` and `skipped` given → 422. Neither given → 422;<br>• resume: `label` with scripted stdin labels 2 tasks then quits; a second `label` run offers only the remaining tasks (and skipped ones only with `--revisit-skipped`);<br>• multiple labelers: labeler `a` labels all tasks and `b` labels 3. Then `score --labeler b` refuses, `score --labeler a` proceeds, and `score` without `--labeler` exits with a usage error;<br>• `report --labeler a` counts `skipped_by_labeler` from `a`'s skipped rows and `not_labelled` from missing rows, and prints the number of other labelers |
| AC8b | backend | integration | pytest | Labels: 201 new, 200 update (one row). Label 5 → 422. A bad labeler → 422. Unknown ref → 404. A note containing `sk-ant-…` is stored scrubbed. A note of 281 chars → 422 |
| — | backend | integration | pytest | `GET /api/meta` returns `schema_version:10` and the capture/JEV flags with reasons |
| AC5c | backend | integration | pytest | **`expired` state via an injected clock (r2 item 9):** seed a task with a prompt expiring at T. Start the app at T−1 min (the startup purge leaves it). Advance the injected clock to T+1 min **without** running the purge. The list shows `prompt_state='expired'`, the detail with `include_prompt` returns null text, and the next purge run turns it into `purged` |

### Pilot CLI and metrics

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC8a | backend | unit | pytest | `select_sample` is deterministic per seed. Strata {1:30, 2:30, 3:3, 4:0, 5:30} with n=50 → 12 each from the 4 non-empty strata ⇒ floor(50/4)=12; stratum 3 gives 3; the shortfall (9) plus the remainder (2) are drawn from the leftovers. Total 50 |
| AC8a | backend | unit | pytest | Eligibility excludes: child, **unknown hierarchy**, completion `open`, prompt not retained or expiring within 5 d, null RS-v1, method `request-shape-v1-inferred`, project `token-inspector`. `inferred` completion is included but counted separately |
| AC8a | backend | integration | pytest | `jev_pilot.py select` via an injected ASGI transport writes the sample file (seed, rules, strata, completion counts, refs, no prompt text) |
| AC8b | backend | integration | pytest | `label` with scripted stdin (`2`, `s`, `q`): stdout contains the project and the prompt only, with no RS-v1, JEV or token values, and it POSTs one label. **A prompt containing `\x1b[2J\x1b]0;pwn\x07` and `\u202e` prints as visible escapes (`\x1b`, `\u202e`), with no raw ESC/BEL/bidi bytes in stdout** (item 6). The order is a seeded shuffle |
| AC8b | backend | integration | pytest | `score` refuses while labels are missing, and `--allow-unlabelled` proceeds. A run ending `deferred_rate_limited` → the CLI prints `retry_not_before` and exits non-zero. It polls by `run_id` |
| AC8c | backend | unit | pytest | `pilot_metrics`: Spearman with ties matches a hand-computed fixture. The weighted κ worked example. MAE, agreement, confusion matrix, half-up rounding. A deterministic bootstrap for a fixed seed |
| AC8c | backend | unit | pytest | **Undefined statistics:** constant human labels → Spearman `undefined`, and the verdict `INCONCLUSIVE`. All-identical label and score categories → κ undefined → `INCONCLUSIVE`. Bootstrap with more than 10% undefined resamples → CI "unavailable" |
| AC8c | backend | integration | pytest | **Paired cohort:** 50 sampled; 5 unlabelled, 3 skipped, 4 JEV error, 2 deferred → cohort 36 → `INCONCLUSIVE — insufficient evidence (36 < 40)`. The exclusions table lists each reason with its count. All metrics are computed on the same 36, not per-pair sets. With 45 complete pairs and synthetic data meeting the thresholds → `PASS`, and missing a threshold → `FAIL` |
| AC8c | backend | integration | pytest | The report contains the selection method, the thresholds, the minimum of 40, coverage, per-stratum and completion counts, and the sample-level caveat. It contains no prompt or note canary |

### Demo seed

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| — | backend | integration | pytest | `standard` → 65 tasks at 30 d, 66 at 90 d, 64 root-only at 30 d, 2 scored, demo-alpha 4, the hostile task present verbatim, `demo-filler-59` seeded with an expired prompt. **After app startup it shows `prompt_state='purged'`** (the real startup-purge check, r2 item 9), and fillers 01–10 with identical `last_seen_at`. `many-scored` → 207 scored at 30 d. An existing path is refused with the file unchanged |

### Playwright stub suite (`tests/browser/`, Claude Code only)

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC7i | frontend | browser | Playwright | `page.route` stubs `/api/tasks/evaluator-status` with fixed JSON, one per state. Assert the status-line text for each:<br>• enabled, spend $0.0012 of $0.05, 3/200 calls;<br>• running;<br>• `last_error_type='budget_exceeded'` (warning colour);<br>• `deferred_rate_limited` with `retry_not_before`;<br>• `disabled_reason='ingest_token_missing'` (config-error suffix);<br>• HTTP 500 → "JEV: status unavailable" while the table still renders |
| AC7g | frontend | browser | Playwright | Stub the detail endpoint to 404 → the panel shows "Task not found.". A 500 → "Could not load task detail." |
| AC7k | frontend | browser | Playwright | Stub the list endpoint to fail with 500 → the error line is visible and no stale rows remain. |
| AC7p | frontend | browser | Playwright | **Real out-of-range (r2 item 10):**<br>1. Stub the list with `total=200` and click Next to page 4 (50 rows).<br>2. Switch the stub so every request reports `total=120`: page 4 → `items=[]`, page 3 → rows 101–120.<br>3. Leave the view and re-enter it via the nav link.<br>Assert, from the request log, exactly one `page=4` request followed by **exactly one** `page=3` request. The table shows the 20 rows of page 3 and "Page 3 of 3 (120 tasks)", with Next disabled and Prev enabled |
| AC7o | frontend | browser | Playwright | **Chart-only failure (r2 item 12):**<br>1. Load successfully once, so the chart has points.<br>2. Stub the chart call (`evaluated=true&page_size=200`) with 500 while the table call succeeds, then change the day filter.<br>Assert: "Could not load chart." is visible; the canvas is hidden and the Chart.js instance is destroyed (`Chart.getChart(canvas)` is undefined); the cap note is hidden; the table shows the new rows with correct pagination; the table error line is hidden. A following successful chart load hides the error |
| AC7n | frontend | browser | Playwright | **Close invalidates in-flight detail (r2 item 11):** delay the detail response for task A by 1.5 s. Click A, then click Close before it resolves, and wait 2 s → the panel stays hidden and contains no A data. Repeat with a delayed 500 → the panel stays hidden with no error text. Then open B normally → B renders |
| AC7d | frontend | browser | Playwright | **`expired` badge (r2 item 9):** stub one list item with `prompt_state='expired'` → its Prompt cell reads "expired", and the detail's prompt status reads "expired" |
| AC7l | frontend | browser | Playwright | **Reordered responses:** delay the 7-day list response by 1.5 s and answer the 90-day one immediately. Click 7, then 90. Assert the final table is the 90-day data and the 7-day response is ignored. The same for the chart, and for the detail (delay A, answer B immediately, click A then B → B shown) |
| AC7h | frontend | browser | Playwright | Stub the chart call with `total=350` and 200 items → "Showing latest 200 of 350 scored tasks". `total=150` → no note |
| AC7j | frontend | browser | Playwright | Against the seeded `standard` DB, with no stubs: `page.evaluate` confirms `window.__xss` is undefined and `document.querySelectorAll('img[src="x"], svg[onload]').length === 0`, and the hostile id appears as text. The request log contains no `include_prompt` and no non-GET `/api/` call. `document.documentElement.outerHTML` contains neither canary |

### Hermes plugin (Part B), run on hermes in the plugin repo `tests/`

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC1 | backend (plugin) | unit | pytest (hermes) | Recorded redacted payloads with the current field names → `response_size_bytes` = UTF-8 bytes (or `assistant_content_chars`), `tool_call_count` from `assistant_tool_call_count`, `role` per the D0 rule, no response-text canary anywhere in the event, and `complexity_method` set. Legacy payloads still map. Payloads with no fields → unset, no raise |
| AC1 | backend (plugin) | manual-scripted | python (hermes, read-only) | Post-deploy prod rows have the fields populated. Counts go into status.md |
| AC2a | backend (plugin) | unit | pytest (hermes) | `ConnectError` → a spool file exists (0600, dir 0700). The next 200 flush replays and deletes it. `client_event_id`s are preserved. `os.fsync` is called on the file and the directory in that order around `os.replace` (spy). The file is deleted only after a 2xx |
| AC2b | backend (plugin) | unit | pytest (hermes) | Spool order [422-file, ok-file]: the 422 file moves to `dead-letter/` (`dead_lettered_batches` +1) **and the ok file is replayed in the same flush**. A corrupt JSON file → dead-letter + `corrupt_batches`. A 401 → the file is kept and replay stops. A 2xx with a garbage body → the file is deleted and `malformed_acks` +1 |
| AC2c | backend (plugin) | unit | pytest (hermes) | An event whose prompt contains `sk-ant-CANARYSECRET…` and `/srv/private/x`, with the send failing. The spool file bytes contain neither the secret nor the path. The in-memory queue item is already scrubbed. `redaction_vectors.json` passes against the plugin's `scrub_text` |
| AC2d | backend (plugin) | unit | pytest (hermes) | Counters:<br>• spool > 1,000 files / 50 MiB → the oldest is deleted, `dropped_batches` +1;<br>• dead-letter > 200 → the oldest is deleted;<br>• files older than 30 d → `expired_batches`;<br>• queue-full → `dropped_batches`;<br>• an unwritable spool dir → no raise, `dropped_batches` +1, WARNING;<br>• `spool_pending` is recomputed from the directory at startup after the counters file was deleted mid-run |
| AC2e | backend (plugin) | unit | pytest (hermes) | **Capture-time expiry (r2 item 3)**, with an injected clock:<br>• delayed spooling: an event with `task_prompt_captured_at` = now − 31 d is spooled → the file contains the event's telemetry but **no** `task_prompt_text` (canary absent from the bytes);<br>• aging in the spool: a file spooled with a prompt captured at now − 29 d, clock advanced 2 d, maintenance runs → the prompt is stripped, telemetry is kept, `prompt_captured_at_min` is recomputed, and `expired_prompts_stripped` +1;<br>• dead-letter: a 422 replay of a batch whose prompt is expired → the dead-letter file holds no prompt; aging in `dead-letter/` is also stripped;<br>• interrupted write: a valid-envelope `*.json.tmp` holding an expired prompt → after startup it is renamed to `.json` with the prompt stripped (`recovered_tmp` +1). A corrupt `.tmp` is deleted (`corrupt_tmp_deleted` +1). No `.tmp` remains |
| AC2d | backend (plugin) | regression | pytest (hermes) | All 25 existing plugin tests pass |
| AC10b | backend (plugin) | unit | pytest (hermes) | Meta gate:<br>• `/api/meta` returns `schema_version:9` → no `task_prompt_text` sent, even with `capture_task_prompt=true`;<br>• v10 with capture false → none;<br>• v10 with capture true and the flag on → sent once, on the first llm_request, with `task_prompt_captured_at`;<br>• probe timeout → none, and telemetry still flows |
| AC10c | backend (plugin) | unit | pytest (hermes) | A delegation payload (D0 fixture) → `task_hierarchy='child'` plus parent/root ids. A top-level turn → `root`. Missing data → `unknown`. A session end hook → a session event with `tags.session_phase='end'` |
| AC9a | backend (plugin) | unit | pytest (hermes) | Synthetic request: system 100 chars; history of 2 prior turns (300 chars, excluding the current user message); `read_file` result of 500 chars for 2 distinct paths; `terminal` result of 250 chars; `search` result of 50 chars. Expect `system=100`, `history=300`, `file_content=500`, `tool_output=300`, `file_ref_count=2`, `tool_names=['read_file','search','terminal']`. No path or content strings appear anywhere in the event |

## Out of Scope

- Live-browser exploratory checks (tests-e2e).
- Real JEV calls. The real fixture is used instead.
- Load and performance testing.
- Faz 1.
- Hermes core.
