# Tests (Non-UI + automated browser) — O9: job correlation, cross-project child fix, Claude Code producer and a versioned read-only export

**Lane**: tests-other
**Tool**: Claude Code — Opus 5.5, direct (no handoff) — pytest for unit/integration in both repos. The Playwright browser suite for this feature is specified in tests-e2e.md (execution plan: tests-e2e extends the existing `-m browser` suite); this file holds everything that runs without a browser.
**Status**: not started
**Brief**: ./2026-09-28-summary.md

## Goal

Verify, without a browser and without real network, every Phase 1–3 control: reserved-tag normalization and the tag cap, plugin job tags and parent project, one-job-per-task, the jobs API cost semantics, the cross-project parent fix and the repair script, the Claude Code producer (dedup, privacy allowlist, fail-open, cursor, installer, stdlib-only, attribution parity), the dedup-authority pairing, the migrations, and the export contract (allowlist, range, snapshot pagination, envelope, zero/unknown/absent) — each control with a mutation check.

## Environment and Conventions

- **Backend** (`C:\Users\Bentego_Admin\Projects\tokenInspector`): pytest (`asyncio_mode = auto`), existing `tests/conftest.py` `client` fixture (temp `DB_PATH`, lifespan, ASGI transport, engine disposed per test — RP 6). `INGEST_TOKEN`, `STORE_TASK_PROMPTS`, `JEV_ENABLED`, `TASK_PROMPT_ALLOWED_PROJECTS` unset by default. Commands: `python -m pytest -q -m "not browser"`, `python -m py_compile *.py routes/*.py scripts/*.py producers/claude_code/*.py`, `node --check static/app.js`, `git diff --check`.
- **Plugin** (`C:\Users\Bentego_Admin\Projects\token_inspector`): pytest in the plugin `tests/`; reuse `_hooks`, `_start`, `_child`, `CapturingSink` from `tests/test_capture_0c.py` / `tests/test_prompt_allowlist.py`. Env vars for the job contract are set with `monkeypatch.setenv` and the `job.py` cache is reset per test (a `job._reset_cache()` test hook or `monkeypatch.setattr` on the cached value).
- **CC producer** tests live in the backend repo (`tests/test_cc_*.py`). They put `producers/claude_code` on `sys.path` (per-module fixture), build **synthetic** transcripts in `tmp_path` with a small builder (never a real transcript, never copied content), stub the network with a local `http.server` thread or by monkeypatching `urllib.request.urlopen`, and point the state dir and config file into `tmp_path` (`LOCALAPPDATA`, `HOME`, `USERPROFILE` monkeypatched).
- **Hermes runs** (every phase checkpoint): push the branch to the `hermes` remote; on hermes create a temp worktree of that branch, run the backend suite with the existing `~/Projects/tokenInspector/.venv` python and the plugin suite with the same venv **and** the Hermes runtime venv (as in O10); remove the worktree. Prod checkouts and the installed plugin are never touched. The browser module skips on hermes (no Playwright). Tests that need leftover bytes set `PRAGMA secure_delete=OFF` explicitly (RP 11).
- **Privacy sentinels.** Canary values: path `C:\Users\ZZ-CANARY-USER\secret\ws` and `/home/zz-canary/secret/ws`, hostname = `socket.gethostname()` of the test machine and a fixed `zz-canary-host.internal`, transcript text `ZZ-O9-CANARY-TEXT sk-ant-CANARYSECRET0123456789`, git branch `zz-canary-branch`, tool input `ZZ-CANARY-TOOLARG`, agent/skill name `zz-canary-agent`. Sentinel tests assert the **bytes** of every emitted payload (JSON-serialized batch body) contain none of them.
- **Mutation checks.** Apply exactly one mutation at the named site, run the named test(s), record *caught* / *survived* in status.md `## Verification Log` (mutation table), revert. A survivor is a missing test and is fixed before the phase checkpoint, unless the row is marked **redundant** (then record "redundant, covered by <row>").
- **Existing tests.** O10 tests must pass unchanged (AC1.7). If an existing test's expectation must change (e.g. `schema_version` 10 → 11/12 in `/api/meta` tests, the tag cap test), list it in the Verification Log with the reason; never relax a privacy assertion.

---

## Phase 1 — job correlation and cross-project child fix

New files: backend `tests/test_job_correlation.py`, `tests/test_jobs_api.py`, `tests/test_cross_project_parent.py`, `tests/test_repair_task_parents.py`, `tests/test_migration_v11.py`, `tests/fixtures/worst_case_plugin_tags.json`; plugin `tests/test_job_tags.py`, `tests/fixtures/worst_case_plugin_tags.json` (byte-identical copy).

### Test Plan

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC1.1 | backend | manual-scripted | read-only on hermes | J0 source trace recorded in the Verification Log (commit, repo-relative files, verdict). Gate: no Phase 1 code test is run against an assumption the gate did not confirm |
| AC1.2 | backend (P) | unit | pytest | `test_llm_event_carries_job_tags` — env `TOKEN_INSPECTOR_JOB_REF=20260928-141024-960`, `..._WORK_TYPE=review`, `..._JOB_ATTEMPT=2`; drive `_start` → `_pre_api_request` → `_post_api_request` into `CapturingSink` → llm event tags hold `job_ref`, `runtime="hermes-agent"`, `producer="hermes-plugin"`, `work_type="review"`, `job_attempt=2` (int); tool events carry them too |
| AC1.2 | backend (P) | unit | pytest | `test_job_tags_absent_without_env` — no env → only `runtime` + `producer`; `work_type`/`job_ref`/`job_attempt` keys absent |
| AC1.2 / AC1.9 | backend (P) | unit | pytest | `test_job_env_invalid_values_dropped` — parametrized: `job_ref` with a path canary, a space, 65 chars, empty, non-ASCII; `work_type` `"Review!"`, `"a b"`, 17 chars; `job_attempt` `"0"`, `"-1"`, `"1.5"`, `"abc"`, `"10000"` → the key is absent; no log record contains the value (`caplog` DEBUG) |
| AC1.2 | backend (P) | unit | pytest | `test_job_env_read_once_per_process` — env changed after the first event → later events keep the first values (process = job) |
| AC1.3 | backend | unit | pytest | `test_reserved_keys_normalization` — `_normalize_reserved` table: `work_type` each of the 7 kept; `"REVIEW "` → `review`; `"brainstorming"`, `7`, `True` → `other`; `None`/`""`/missing → absent; `job_ref` bad → absent; `runtime` outside the list → absent; `producer` outside the list → absent; `job_attempt` `0`, `-1`, `True`, `1.0`, `"2"` → absent, `3` → 3; other keys untouched |
| AC1.3 | backend | integration | pytest | `test_reserved_keys_never_reject` — every invalid reserved value above, via `/api/events` (201) and `/api/events/batch` (`inserted == n`, `rejected == 0`); stored `tags_json` holds the normalized form |
| AC1.3 | backend | integration | pytest | `test_job_attempt_separate_from_attempt` — event with `attempt=3` and tag `job_attempt=1` → column `attempt == 3`, tag `job_attempt == 1` |
| AC1.4 | backend (P) | unit | pytest | `test_worst_case_tags_fit_backend_cap` — builds the worst-case `_base` tags (backend.md J2.1); asserts bytes ≤ `BACKEND_TAG_BYTES_MAX`, keys ≤ 20, pins the measured size, and equals the fixture file content |
| AC1.4 | backend | integration | pytest | `test_worst_case_plugin_tags_accepted` — posts the fixture tags → `inserted == 1`; a tags dict 1 byte over `TAG_BYTES_MAX` → rejected (existing behaviour kept for real overflow) |
| AC1.4 | backend | unit | pytest | `test_fixture_files_identical` — when the plugin repo exists as a sibling, its `tests/fixtures/worst_case_plugin_tags.json` bytes equal the backend copy (skip otherwise) |
| AC1.5 | backend | integration | pytest | `test_task_gets_first_job` — two events of one turn with `job_ref` A then B → `tasks.job_ref == A`, `job_ref_conflicts == 1`; a third event with A → still 1; an event without `job_ref` → unchanged |
| AC1.5 | backend | integration | pytest | `test_duplicate_delivery_does_not_count_conflict` — resend of the B event (same `client_event_id`) → `job_ref_conflicts` stays 1 |
| AC1.5 | backend | integration | pytest | `test_jobs_api_one_row_per_job` — job A with 3 tasks (root + child + another root), job B with 1 task, events without job → 2 rows; `task_count`, `llm_request_count`, four token sums, `runtimes`, `work_type`, `attempts`, `projects`, first/last event time as expected; ordering `last_event_at DESC, job_ref` |
| AC1.5 | backend | integration | pytest | `test_job_cost_semantics` — (a) all priced → `cost_usd` = sum, `cost_complete` true; (b) mixed priced + unpriced → `cost_usd` = priced sum, `unpriced_count > 0`, `cost_complete` false; (c) all unpriced → `cost_usd is None`, never 0; (d) a `partial`/`estimated` call → in `estimated_cost_usd`, `cost_complete` false |
| AC1.5 | backend | integration | pytest | `test_task_belongs_to_one_job` — task with `job_ref` A whose later call is tagged B → that call counts under A only; B has no row (unless it owns other tasks); A's `conflict_count == 1`; `anomalies.job_ref_conflicts == 1` |
| AC1.5 | backend | integration | pytest | `test_jobs_exclude_evaluator` — an evaluator event (`project_name='token-inspector'`, `role='evaluator'`) crafted with a `job_ref` tag of job A → not counted in A's sums or counts |
| AC1.5 | backend | integration | pytest | `test_jobs_filters_and_window` — `runtime`, `work_type` filters; `days` window on `last_event_at`; invalid filter → 400 static `{"error":"invalid_filter"}` without echo; `filters` lists ignore their own filter |
| AC1.5 | database | integration | pytest | `test_m11_fresh_equals_migrated`, `test_m11_on_v10_db` (existing tasks `job_ref` NULL, conflicts 0, backup verified), `test_m11_idempotent`, `test_rollback_v11_sql` (column set back to v10; v10-shaped upsert still works on v11) — database.md AC-DB1.1–1.4 |
| AC1.7 | backend | integration | pytest | `test_cross_project_child_parent_ref` — parent turn in project A; child event in project B with `parent_project_name="A"` (and `" A "` → stripped/lowercased) → child `parent_task_ref == task_ref("a", ps, pt)`, `root_task_ref` = parent id, `hierarchy_status='child'` |
| AC1.7 | backend | integration | pytest | `test_cross_project_child_either_order` — (i) parent first, (ii) child first then parent (which is itself a child of a root in project A, so re-root is exercised) → identical final `parent_task_ref` and `root_task_ref` in both orders |
| AC1.7 | backend | integration | pytest | `test_invalid_parent_project_name_keeps_today_behaviour` — absent, `""`, `"Bad Name!"`, `"../x"`, `123`, `{"a":1}` → event inserted (never 422), `parent_task_ref == task_ref(child_project, ps, pt)` exactly as before |
| AC1.7 | backend | integration | pytest | `test_child_never_downgraded_cross_project` — later root-flagged event of the child turn keeps `child`; no child row stores a prompt (O10 `prompt_root_eligible` still false) |
| AC1.7 | backend | regression | pytest | full O10 suite (`test_prompt_allowlist.py`, `test_task_ingest.py`, `test_review_r1.py`, …) unchanged and green |
| AC1.7 | backend (P) | unit | pytest | `test_child_event_carries_parent_project_name` — parent session resolves to project `x`; `_child(C, P)`; C's llm event has `parent_project_name == "x"` with parent fields; a root event has no such key |
| AC1.7 / AC1.9 | backend (P) | unit | pytest | `test_invalid_parent_project_not_sent` — parent resolved name that fails the strict rule (forced via stub) → key absent; never the `hermes` fallback |
| AC1.8 | backend | integration | pytest | `test_repair_dry_run_by_default` — DB with 3 dangling cross-project children (built with the pre-fix formula), 1 ambiguous, 1 unmatched → dry-run prints exactly the counts line; DB bytes unchanged (hash before/after) |
| AC1.8 | backend | integration | pytest | `test_repair_apply_relinks_and_reroots` — `--apply` → relinkable rows get the right parent and root, descendants re-rooted, `updated_at` set; ambiguous/unmatched untouched; output has no ref, name or path |
| AC1.8 | backend | integration | pytest | `test_repair_backs_up_before_apply` — backup file exists, `integrity_check == ok`, counts equal; a failing backup (monkeypatched) → exit non-zero and no write |
| AC1.8 | backend | integration | pytest | `test_repair_idempotent` — second `--apply` → `dangling` only ambiguous/unmatched, zero changes |
| AC1.9 | backend (P) | unit | pytest | `test_plugin_payload_sentinel` — env job vars set to canaries (path, hostname, text), `parent_project_name` forced to a canary → serialized batch body bytes (fake client) contain no canary, no `socket.gethostname()`, no `error_message` key |

### Mutation checks (Phase 1)

Plugin (`C:\Users\Bentego_Admin\Projects\token_inspector`):

| # | Control | Mutation site | Isolated fixture / assertion point | Expected failing test(s) |
|---|---|---|---|---|
| PJ1 | job_ref regex | `job.py`: accept any non-empty `TOKEN_INSPECTOR_JOB_REF` | canary path value; assert tags at `CapturingSink` | `test_job_env_invalid_values_dropped`, `test_plugin_payload_sentinel` |
| PJ2 | work_type shape | `job.py`: pass `work_type` raw | `"a b"` value | `test_job_env_invalid_values_dropped` |
| PJ3 | job_attempt int | `job.py`: send the env string instead of `int` | `"2"` → expects int 2 | `test_llm_event_carries_job_tags` |
| PJ4 | tags merged | `mapping._base`: drop the `job_tags()` merge | env set | `test_llm_event_carries_job_tags` |
| PJ5 | read once | `job.py`: re-read env on every call | env changed after first event | `test_job_env_read_once_per_process` |
| PJ6 | parent project recorded | `_subagent_start`: do not store `parent_project_name` | cross-project child | `test_child_event_carries_parent_project_name` |
| PJ7 | strict name rule | `_subagent_start`: pass the name through `normalize_project_name` | invalid name stub | `test_invalid_parent_project_not_sent` |

Backend (`C:\Users\Bentego_Admin\Projects\tokenInspector`):

| # | Control | Mutation site | Isolated fixture / assertion point | Expected failing test(s) |
|---|---|---|---|---|
| BJ1 | work_type → other | `_normalize_reserved`: keep unknown values raw | `"brainstorming"` | `test_reserved_keys_normalization` |
| BJ2 | job_ref regex | `_normalize_reserved`: keep any string | bad job_ref | `test_reserved_keys_normalization` |
| BJ3 | job_attempt type | `_normalize_reserved`: `isinstance(v, int)` (accepts `True`) | `True` | `test_reserved_keys_normalization` |
| BJ4 | runtime/producer lists | `_normalize_reserved`: skip the list checks | outside values | `test_reserved_keys_normalization` |
| BJ5 | never reject | `_normalize_reserved`: `raise ValueError` on a bad `job_ref` | single + batch | `test_reserved_keys_never_reject` |
| BJ6 | tag cap | `TAG_BYTES_MAX = 512` | worst-case fixture | `test_worst_case_plugin_tags_accepted` |
| BJ7 | first job wins | `_upsert_task`: `job_ref = COALESCE(excluded.job_ref, tasks.job_ref)` | A then B | `test_task_gets_first_job`, `test_task_belongs_to_one_job` |
| BJ8 | conflict counter | `_upsert_task`: counter `+ 0` | A then B | `test_task_gets_first_job` |
| BJ9 | unpriced never zero | jobs SQL: `cost_usd` = `COALESCE(priced_sum, 0)` | all-unpriced job | `test_job_cost_semantics` (c) |
| BJ10 | cost_complete | jobs: `cost_complete = priced_count > 0` | mixed job | `test_job_cost_semantics` (b) |
| BJ11 | evaluator excluded | jobs SQL: drop the evaluator predicate | crafted evaluator event with job tag | `test_jobs_exclude_evaluator` |
| BJ12 | job of an event | jobs SQL: use the event tag before the task's job | conflicting tag | `test_task_belongs_to_one_job` |
| BJ13 | parent project used | `_upsert_task`: `parent_ref` from `project` again | cross-project child | `test_cross_project_child_parent_ref` |
| BJ14 | re-root global | re-root UPDATE: restore `AND project_name = :p` | child-first order | `test_cross_project_child_either_order` (ii) |
| BJ15 | invalid name dropped | `EventIn` validator: return the raw string | `"Bad Name!"` | `test_invalid_parent_project_name_keeps_today_behaviour` |
| BJ16 | repair dry-run default | `repair_task_parents.py`: default `apply=True` | no flag | `test_repair_dry_run_by_default` |
| BJ17 | repair backup first | skip the backup call | `--apply` | `test_repair_backs_up_before_apply` |
| BJ18 | repair ambiguity | relink the first of several matches | ambiguous row | `test_repair_apply_relinks_and_reroots` |

---

## Phase 2 — Claude Code producer and dedup authority

New backend files: `tests/test_cc_transcript.py`, `tests/test_cc_events.py`, `tests/test_cc_hook.py`, `tests/test_cc_state_client.py`, `tests/test_cc_install.py`, `tests/test_cc_attribution.py`, `tests/test_cc_stdlib.py`, `tests/test_dedup_authority.py`, `tests/fixtures/attribution_vectors.json` (plus a byte-identical copy and a runner test in the plugin repo: `tests/test_attribution_vectors.py`, `tests/fixtures/attribution_vectors.json`).

The synthetic transcript builder follows the C0 rules recorded in the Drift Log (line shapes, streaming duplicates, subagent layout, resumed copies, `<synthetic>` lines). If C0 changed a rule, the builder follows C0.

### Test Plan

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC2.1 | backend | manual-scripted | read-only | C0 facts recorded in the Verification Log (field names, counts, relations only) and each rule in the Drift Log; the builder's docstring cites the C0 entry |
| AC2.2 | backend | integration | pytest | `test_stop_hook_sends_turn_calls` — synthetic session: 2 user turns, 3 assistant calls with usage; run `cc_hook.main()` with Stop stdin (as bytes, UTF-8, non-ASCII path) against a local test server → batch body has 3 `llm_request` events, `runtime` per platform (`claude-code@windows` on win32, `claude-code@hermes` on Linux, asserted per platform), `producer="claude-code-hook"`, model, token mapping, 2 distinct `turn_id`s (one task per user turn) |
| AC2.2 | backend | unit | pytest | `test_token_mapping` — `input_tokens` → `prompt_tokens` (cache excluded), `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens` → distinct fields; missing cache fields → 0; `input_tokens_include_cache is False` |
| AC2.2 | backend | unit | pytest | `test_attribution_order` — alias match wins over origin; origin slug over git root; git root over fallback; no git → `claude-code`; `.git` file with `gitdir:`; invalid alias name dropped; the function returns only a name (no path in any return value or log) |
| AC2.2 | backend | unit | pytest | `test_attribution_vectors` — runs `cc_attribution` over `tests/fixtures/attribution_vectors.json`; plugin repo runs its `project.py` over its copy; `test_attribution_vectors_identical` compares the two files when the sibling repo exists |
| AC2.3 | backend | integration | pytest | `test_devir_run_tags` — env `TOKEN_INSPECTOR_JOB_REF=devir-20260928-101010-4242`, `TOKEN_INSPECTOR_WORK_TYPE=devir`, platform forced to Linux, cwd a tmp repo named `PEGADocRagAgent-devir` with origin `…/PEGADocRag.git` → events carry `runtime="claude-code@hermes"`, `work_type="devir"`, that `job_ref`, `X-Project-Name: pegadocrag`; no prompt field anywhere |
| AC2.4 | backend | unit | pytest | `test_client_event_id_from_message_and_request` — id = `"cc-" + sha256(mid \x1f rid)[:32]`; same (mid, rid) in two sessions → same id; different rid → different id; no raw `mid`/`rid` in the body |
| AC2.4 | backend | integration | pytest | `test_streaming_lines_counted_once_with_final_usage` — 4 lines with one `message.id` and increasing `output_tokens` → one event with the C0-chosen line's usage |
| AC2.4 | backend | integration | pytest | `test_rerun_and_resume_count_once` — run the hook twice on the same transcript (second run with the cursor deleted) and on a resumed-session transcript that copies earlier messages → backend (real ASGI app via a thread-bound `uvicorn`/test server or `httpx` ASGI adapter behind `urlopen` monkeypatch) stores each call once; second ack `duplicates` equals the resent count |
| AC2.4 | backend | unit | pytest | `test_synthetic_model_lines_skipped` — `<synthetic>` lines produce no event |
| AC2.5 | backend | integration | pytest | `test_subagent_is_child_task` — synthetic subagent run per C0 layout → events with the subagent's own `session_id`, `task_hierarchy="child"`, `parent_session_id` = main session, `parent_turn_id` = the parent turn, `parent_project_name`, `role="subagent"`; backend task row is `child` with `parent_task_ref == task_ref(project, main, parent_turn)` |
| AC2.6 | backend | integration | pytest | `test_payload_sentinel_bytes` — transcript lines contain every canary (text, tool input, agent name, branch in `gitBranch`, `cwd`, hostname) and stdin carries canary `cwd`/`transcript_path` → the **bytes** of every POST body contain none; only keys from `ALLOWED_FIELDS` appear; `request_tool_names` absent; `tool_call_count` present |
| AC2.6 | backend | unit | pytest | `test_events_built_only_from_allowlist` — `cc_events` output keys ⊆ `ALLOWED_FIELDS`; tags keys ⊆ the C3 tag list; a transcript line with an unexpected new field (`"secret_field"`) is not forwarded |
| AC2.7 | backend | integration | pytest | `test_hook_exits_zero_on_every_failure` — parametrized: backend down (connection refused), 401, 422 whole-request, 500, garbage ack, malformed transcript line, missing transcript file, invalid stdin JSON, stdin not UTF-8 → subprocess `python cc_hook.py` exits 0, stdout and stderr empty |
| AC2.7 | backend | integration | pytest | `test_hook_bounded_on_hanging_backend` — test server that never answers → hook subprocess finishes within `HOOK_BOUND_S + 1` s, exit 0 (`subprocess.run(timeout=…)` so a hang fails) |
| AC2.7 | backend | unit | pytest | `test_cursor_advances_only_on_valid_ack` — failure cases above leave the offset unchanged; valid ack (incl. some `rejected`) advances it; a partial line at EOF is not consumed |
| AC2.7 | backend | unit | pytest | `test_state_holds_only_offsets_and_ids` — state files contain only `offset`, `turn_uuid`, `updated_at`; filename is a 32-hex hash; no path, token or text bytes in the state dir; 600 transcripts → ≤ 512 files; an oversized/corrupt state file is ignored |
| AC2.7 | backend | unit | pytest | `test_ack_validation` — same vectors as `valid_ack` in `token_inspector_client.py` (bools, negatives, floats, strings, missing key, wrong sum) → both functions agree |
| AC2.8 | backend | integration | pytest | `test_token_optional_and_never_logged` — no token → no header, events accepted with `INGEST_TOKEN` unset; token from env and from the secret file → header sent, backend with `INGEST_TOKEN` set accepts; wrong token → 401 → exit 0, no advance; token bytes absent from state dir, counters file, stdout/stderr, `caplog` |
| AC2.9 | backend | unit | pytest | `test_installer_dry_run_changes_nothing` — prints a diff, file bytes unchanged |
| AC2.9 | backend | unit | pytest | `test_installer_preserves_foreign_hooks` — settings with a PreToolUse coordination hook, another Stop hook, `permissions`, `statusLine` → after `--apply` all preserved byte-for-byte in value, key order kept, our three entries added with `timeout: 10`, `python` on win32 / `python3` on Linux |
| AC2.9 | backend | unit | pytest | `test_installer_idempotent_backup_uninstall` — second `--apply` is a no-op; each `--apply` writes `settings.json.bak-ti-*` first; `--uninstall --apply` removes only ours; invalid JSON → exit 1, nothing written, no content echoed; missing file → created with only our hooks |
| AC2.10 | backend | integration | pytest | `test_runtime_producer_pairing` — (`claude-code@windows`, `hermes-plugin`) → `runtime` dropped, event inserted; (`hermes-agent`, `claude-code-hook`) → dropped; valid pairs kept; missing producer → runtime dropped; never rejected |
| AC2.10 | backend | unit | pytest | `test_no_event_from_hook_stdin` — stdin with fake usage-like fields and an empty transcript → zero events (transcript is the only usage source) |
| AC2.11 | backend | unit | pytest | `test_cc_producer_is_stdlib_only` — AST-parse every `producers/claude_code/*.py`; every absolute import's top-level name ∈ `sys.stdlib_module_names` or a sibling `cc_*` module |
| AC2.11 | backend | regression | CLI | suites green on Windows **and** hermes (producer tests included; platform-specific runtime assertions run on both) |

### Mutation checks (Phase 2)

| # | Control | Mutation site | Isolated fixture / assertion point | Expected failing test(s) |
|---|---|---|---|---|
| CM1 | id from message+request | `cc_events`: id from `session_id` + `message.id` | same (mid, rid) in two sessions | `test_client_event_id_from_message_and_request`, `test_rerun_and_resume_count_once` |
| CM2 | final usage line | `cc_transcript`: keep the first line of a group | 4 streaming lines | `test_streaming_lines_counted_once_with_final_usage` |
| CM3 | synthetic skip | `cc_transcript`: do not skip `<synthetic>` | synthetic lines | `test_synthetic_model_lines_skipped` |
| CM4 | cache excluded | `cc_events`: `prompt_tokens = input + cache_read + cache_creation` | usage with cache | `test_token_mapping` |
| CM5 | final allowlist filter | `cc_events`: drop the `ALLOWED_FIELDS` filter and copy line keys | unexpected field + canaries | `test_events_built_only_from_allowlist`, `test_payload_sentinel_bytes` |
| CM6 | cursor after ack | `cc_hook`: advance the offset before posting | backend down | `test_cursor_advances_only_on_valid_ack` |
| CM7 | ack validation | `cc_client.valid_ack`: accept any dict | garbage ack | `test_ack_validation`, `test_cursor_advances_only_on_valid_ack` |
| CM8 | fail-open | `cc_hook`: catch `Exception` only and re-raise in `main` | invalid stdin | `test_hook_exits_zero_on_every_failure` |
| CM9 | watchdog | `cc_hook`: do not start the timer, and `urlopen` timeout 30 | hanging server | `test_hook_bounded_on_hanging_backend` |
| CM10 | silent output | `cc_hook`: print a status line on success | any case | `test_hook_exits_zero_on_every_failure` (stdout empty) |
| CM11 | state privacy | `cc_state`: store the transcript path in the state JSON | normal run | `test_state_holds_only_offsets_and_ids` |
| CM12 | state bound | `cc_state`: skip pruning | 600 transcripts | `test_state_holds_only_offsets_and_ids` |
| CM13 | token never logged | `cc_client`: include the token in the error message on 401 | wrong token | `test_token_optional_and_never_logged` |
| CM14 | child link | `cc_events`: omit parent fields for subagents | subagent run | `test_subagent_is_child_task` |
| CM15 | attribution order | `cc_attribution`: git root before origin slug | repo whose folder ≠ slug | `test_attribution_order`, `test_devir_run_tags` |
| CM16 | installer preserves | `install.py`: replace the `hooks` object instead of merging | foreign hooks | `test_installer_preserves_foreign_hooks` |
| CM17 | installer idempotent | `install.py`: append without the existing-entry check | second apply | `test_installer_idempotent_backup_uninstall` |
| CM18 | installer backup | `install.py`: skip the backup | apply | `test_installer_idempotent_backup_uninstall` |
| CM19 | pairing enforced | `_normalize_reserved`: skip the runtime↔producer check | mismatched pair | `test_runtime_producer_pairing` |
| CM20 | stdlib only | add `import httpx` to `cc_client.py` | — | `test_cc_producer_is_stdlib_only` |

---

## Phase 3 — versioned read-only export

New backend files: `tests/test_export_contract.py`, `tests/test_export_pagination.py`, `tests/test_migration_v12.py`, `tests/test_docs_export.py`.

### Test Plan

| AC | Lane | Tier | Tool | Description |
|---|---|---|---|---|
| AC3.1 | backend | integration | pytest | `test_export_endpoints_read_only_no_auth` — all three datasets return 200 with `INGEST_TOKEN` unset and set (no token header sent); `POST`/`PUT`/`DELETE` → 405; row counts in `token_events`/`tasks` unchanged by any export call |
| AC3.2 | backend | integration | pytest | `test_export_field_allowlist_contract` — seeded DB with every column filled (incl. `error_message`, `prompt_text`, `user_id_hash`, `prompt_hash`, extra tags, `request_tool_names`, a task prompt and a label note): every item's keys ⊆ `EXPORT_FIELDS[dataset]`; `fields` equals the pinned literal list in the test; no excluded value's bytes appear in the response |
| AC3.2 | backend | integration | pytest | `test_export_tag_allowlist` — event tags with 12 keys → only the 5 `EXPORT_TAG_KEYS` surface (as top-level fields); no `tags` key |
| AC3.2 | backend | unit | pytest | `test_export_not_reusing_events_listing` — `routes/export.py` does not import or call `routes.events.list_events` (AST check) |
| AC3.3 | backend | integration | pytest | `test_range_bounds_inclusive_exclusive` — events at exactly `from` (included) and exactly `to` (excluded); `+03:00` input normalized to `Z` in the echoed `period` |
| AC3.3 | backend | integration | pytest | `test_export_errors_static_no_echo` — missing `from`, garbage `from` with a canary, no timezone, `from >= to`, span 93 days, `limit=0`/`1001`/`abc`, cursor garbage / other dataset / other range → exactly the documented body and status; canary absent from the response and `caplog` |
| AC3.4 | backend | integration | pytest | `test_pages_concatenate_to_full_result` — 1 234 events, `limit` 100 → concatenated `event_id`s equal a single `limit=1000`×2 walk and the direct SQL result; no duplicates; `complete` false on every page but the last; same for tasks and jobs |
| AC3.4 | backend | integration | pytest | `test_pagination_under_concurrent_ingest` — after page 1, insert (a) new events inside the period, (b) a late event with an old `occurred_at`, (c) new events of an already-exported task → remaining pages of the **same** cursor chain neither include them nor change sums; a fresh export includes them |
| AC3.4 | database | integration | pytest | `test_ingest_seq_monotonic_in_commit_order` — 20 concurrent batch posts (`asyncio.gather`) → `ingest_seq` unique, 1..N without gaps, and in commit order (each batch's rows contiguous); duplicates consume no number |
| AC3.4 | database | integration | pytest | `test_m12_fresh_equals_migrated`, `test_m12_backfill_order`, `test_null_seq_self_heal_on_startup`, `test_rollback_v12_sql` — database.md AC-DB3.1–3.5 |
| AC3.5 | backend | integration | pytest | `test_envelope_staleness_coverage` — events for `hermes-agent` and `claude-code@windows` only → `staleness` has their latest `recorded_at` and `null` for the others; `coverage.measured`/`not_measured` partition `RUNTIMES`; `generated_at` is UTC `Z`; `schema_version == 1` |
| AC3.5 / AC3.6 | backend | integration | pytest | `test_legacy_events_inferred_runtime` — event without `runtime` tag → `runtime="hermes-agent"`, `runtime_inferred=true`, no `producer` key; counts toward `hermes-agent` staleness and coverage |
| AC3.6 | backend | integration | pytest | `test_zero_unknown_absent_distinct` — measured `0` tokens stay `0`; unpriced call → `cost_usd: null` with `cost_status: "unpriced"`; CC event → no `reasoning_tokens`/`ttft_ms` keys; event without job → no `job_ref` key; non-task event → no `task_ref` key; integer types for tokens |
| AC3.6 | backend | integration | pytest | `test_export_excludes_evaluator` — evaluator events never appear in events, tasks, jobs, staleness or coverage |
| AC3.7 | backend | integration | pytest | `test_stable_ids_and_updated_at` — the same export run twice yields the same `event_id`, `task_ref`, `job_ref`; a later event on an open task changes its `updated_at`; CC events carry the `cc-` `client_event_id` |
| AC3.8 | backend | unit | pytest | `test_export_contract_documented` — `docs/export-contract-v1.md` exists and names every field of every dataset, every error code, `92`, `1000`, `schema_version`, "ignore unknown fields", `from` inclusive / `to` exclusive; `docs/adr/005-export-contract.md` exists |
| AC3.8 | backend | integration | pytest | `test_export_busy_returns_503` — monkeypatched `OperationalError("database is locked")` → 503, `Retry-After: 5`, `{"error":"busy","schema_version":1}` |
| Every phase | backend | regression | CLI | full suite + py_compile + `node --check` + `git diff --check`, locally and on hermes |

### Mutation checks (Phase 3)

| # | Control | Mutation site | Isolated fixture / assertion point | Expected failing test(s) |
|---|---|---|---|---|
| XM1 | field allowlist | `routes/export.py`: add `error_message` to event items | full-column seed | `test_export_field_allowlist_contract` |
| XM2 | tag allowlist | add the full `tags` dict to event items | 12-key tags | `test_export_tag_allowlist`, `test_export_field_allowlist_contract` |
| XM3 | `to` exclusive | `occurred < :to` → `<=` | event at `to` | `test_range_bounds_inclusive_exclusive` |
| XM4 | no echo | `invalid_range` body includes the raw `from` | canary `from` | `test_export_errors_static_no_echo` |
| XM5 | snapshot bound | drop `ingest_seq <= :as_of` | concurrent inserts | `test_pagination_under_concurrent_ingest` |
| XM6 | strict keyset | `key > :k` → `key >= :k` | multi-page walk | `test_pages_concatenate_to_full_result` |
| XM7 | cursor binding | cursor accepted for another range | range-mismatch cursor | `test_export_errors_static_no_echo` |
| XM8 | unknown ≠ zero | `cost_usd` = `COALESCE(cost, 0)` | unpriced call | `test_zero_unknown_absent_distinct` |
| XM9 | absent ≠ zero | emit `reasoning_tokens: 0` for CC events | CC event | `test_zero_unknown_absent_distinct` |
| XM10 | evaluator excluded | drop the evaluator predicate | evaluator events | `test_export_excludes_evaluator` |
| XM11 | coverage | `not_measured` = `[]` | two runtimes only | `test_envelope_staleness_coverage` |
| XM12 | legacy inference | legacy `runtime_inferred` false | untagged event | `test_legacy_events_inferred_runtime` |
| XM13 | no auth | add `Depends(require_sensitive_auth)` to the router | token unset | `test_export_endpoints_read_only_no_auth` |
| XM14 | seq in commit order | `_insert_event`: `ingest_seq = random` / NULL | concurrent batches | `test_ingest_seq_monotonic_in_commit_order` |
| XM15 | self-heal | skip the startup NULL backfill | NULL rows | `test_null_seq_self_heal_on_startup` |

The CSV formula guard mutation (FM2) is in tests-e2e.md (browser).

## Out of Scope

- Live production checks (they are Deploy Runbook steps, user-approved).
- Real transcripts or copied transcript content in tests or fixtures.
- Load/performance testing beyond the 1 234-event pagination test.
- The PA proposals view.
