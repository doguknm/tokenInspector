# Backend — Per-project prompt/JEV allowlist with clone path-deny, and a safe producer example (O10)

**Lane**: backend
**Tool**: Claude Code — Opus 5.5, direct (no handoff). Part A = this repo, Part B = plugin repo `C:\Users\Bentego_Admin\Projects\token_inspector`
**Status**: not started
**Brief**: ./2026-09-27-summary.md
**Depends on**: ./database.md — out of scope (no schema change). This lane can start immediately.

## Goal

Implement the per-project prompt/JEV allowlist (default off; plugin, ingest and JEV gates; `-devir` path deny) and the safe producer example, as specified in the summary `TASK_SUMMARY` and ACs. This closes O10.

**Production stays untouched.** No flag (`STORE_TASK_PROMPTS`, plugin `capture_task_prompt`, `JEV_ENABLED`) is enabled anywhere in production by this lane. Prod deploy, plugin install and `hermes gateway restart` happen only after the review lane and only with the user's approval. They follow the status.md `## Upgrade / Rollback Runbook` (F16): backend first (its marker gate, A2.2, blocks old plugins and legacy spools), then plugin install + gateway restart with no agent job running; any rollback starts with every flag off.

## Repos, branches, baselines

| Part | Repo | Branch | Base commit (plan time) |
|---|---|---|---|
| A | `C:\Users\Bentego_Admin\Projects\tokenInspector` (remote `hermes` = `hermes:Projects/tokenInspector`) | `feat/task-telemetry-jev-pilot` | `3f52fe1` |
| B | `C:\Users\Bentego_Admin\Projects\token_inspector` (remote `hermes`) | `feat/task-telemetry-jev-pilot` | `f19ffd2` |

- Workflow (O5/O6): develop and test on Windows, push the branch to the `hermes` remote. GitHub is not touched. Commit on the feature branch when the user/orchestrator approves; commit messages end with the attribution line from the session.
- Git Bash ssh: use `/c/WINDOWS/System32/OpenSSH/ssh.exe -o ClearAllForwardings=yes hermes` (CLAUDE.md RP 8). Always invoke `python`, never `python3`.
- Order: **A1 → A2 → A3 → A4 → A5 → B1 → B2 → B3 → B4 → docs (maintenance-docs step)**. Tests for each step are written with it (tests-other.md) and a mutation check is run per gate before moving on.

## Shared definitions (both parts)

- **Allowlist entry normalization (strict).** Each entry is `strip().lower()`. It is kept only if it fully matches `^[a-z0-9][a-z0-9._-]{0,63}$` (exactly the fixed points of the plugin's `normalize_project_name`). Anything else is **dropped**. Never pass an entry through `normalize_project_name`: that function falls back to `hermes` on garbage, so a typo would silently allowlist the `hermes` fallback.
- **Match rule.** Exact, case-insensitive-by-normalization equality between the event's normalized project name and an entry. No globs, no prefixes, no name denylist (`pega*` is explicitly not a mechanism; default-off is).
- **Privacy boundary (F14).** Prohibited: allowlist *configuration* (the list, its size, entries as a list), deny globs, workspace paths and match results never enter events, tags, logs, `/api/meta` or error responses; logs carry neither configuration values nor counts (r2 F10). Permitted: a project name in normal telemetry (`project_name` of an event/task, as today) and membership implied by the results of the authenticated `GET /api/tasks?allowed_only=true`.
- **Decision values (plugin).** Own decision: `allowed` | `denied` (path deny, sticky for the session) | `not_allowed` (project not on the list). Lineage: `root` | `child` | `unknown` (B3.3). Only `allowed` + `root` lets a prompt through.
- **Root-only rule (r2).** Prompts are captured, stored and scored only for proven root tasks. A child (subagent) session never carries a prompt; its metadata is unchanged. There is no ancestor walk, no cached parent decision and no depth limit anywhere (r1 F3/F4 → resolved by root-only rule (r2); r2 F5/F7 → resolved the same way).
- **Proven root (backend, r2 F6).** Task row with `hierarchy_status = 'root'`, `parent_task_ref IS NULL` and `root_task_ref IS NULL OR root_task_ref = id`. Root rows store `root_task_ref` NULL today (`_upsert_task` writes it only for children); the data model and `_upsert_task` stay unchanged.
- **Attach authorization (plugin, r2 F2).** A keyword-only `prompt_authorized: bool = False` argument of `Sink.emit`, set only from the return value of `_attach_task_prompt`. It is not an event field, so no event content can grant it.
- **Eligibility marker (F1).** Constant `PROMPT_ELIGIBILITY = "v1-allowed"` in both repos, sent as top-level event field `prompt_eligibility`. It carries no path, name or config data. A prompt without the current marker is never sent (plugin) and never stored (backend). The backend accepts the field in `EventIn` as a plain `Optional[str]` (no length or value constraint, compared by equality, never stored) so an unknown value never rejects the metadata event; `extra="ignore"` means an older backend simply ignores it.

---

## Part A — tokenInspector backend

### Endpoints

| Method | Path | Auth | Request shape | Response shape | Status codes |
|---|---|---|---|---|---|
| GET | `/api/meta` | none (Host allowlist only) | — | existing keys unchanged (`schema_version` stays 10, `task_prompt_capture`, `task_prompt_capture_disabled_reason`, `jev_enabled`, `jev_disabled_reason`, `config_errors`) **plus** `"task_prompt_allowlist": "configured" \| "empty"` | 200 |
| POST | `/api/events`, `/api/events/batch` | `require_ingest_auth` (unchanged) | `EventIn` + optional `prompt_eligibility` (still accepts `error_message` and `task_prompt_text`) | unchanged; a not-eligible prompt is stripped silently, the event is still `inserted` (201 / 200); validation errors never echo input (A2.8) | unchanged |
| GET | `/api/tasks` | none; **`require_sensitive_auth` when `allowed_only=true`** | new optional query `allowed_only` (bool, default false) | unchanged shape; with `allowed_only=true`, `items`/`total` include only tasks whose project is on the backend allowlist (`projects` keeps its existing semantics and ignores this filter) | 200, 401, 403, 422 |
| POST | `/api/tasks/evaluate` | `require_sensitive_auth` (unchanged) | unchanged | unchanged; a ref can now be skipped with reason `project_not_allowed` | unchanged |
| GET | `/api/tasks/evaluator-status` | none | — | unchanged shape; `disabled_reason` may be `no_allowed_projects` | 200 |

### A1 — Allowlist config and `/api/meta` (`features.py`, `routes/meta.py`) → AC9

1. New env var `TASK_PROMPT_ALLOWED_PROJECTS` (comma-separated). Parse with the strict normalization above into a `frozenset[str]`. Unset, empty or all-invalid → empty set. If any entries were dropped, log **one** fixed WARNING at startup: `invalid allowlist entries ignored` — no count, no names (r2 F10).
2. `Features` gains `allowed_projects: frozenset[str]` and `allowlist_state: str` (`"configured"` when non-empty, else `"empty"`).
3. The one allowlist governs capture **and** JEV (user decision). When the set is empty:
   - `config_errors` gains `{"feature": "capture", "error": "no_allowed_projects"}` and `{"feature": "jev", "error": "no_allowed_projects"}`. This happens regardless of the flags, so the Activation Gate preflight (flags off) sees it.
   - The new error is appended **last** in each feature's error list, so existing precedence is kept (`ingest_token_missing`, `invalid_purge_interval` for capture; `credentials_missing`, `ingest_token_missing`, `invalid_budget_config` for JEV). `disabled_reason` stays "first error", so with a good config and an empty list, `task_prompt_capture_disabled_reason == "no_allowed_projects"` and capture is disabled (Q2 decision). JEV gets `jev_disabled_reason == "no_allowed_projects"` the same way (planner decision: one list governs both).
   - Flag on + empty list logs one ERROR at startup, same style as the others: `[SECURITY] task prompt capture disabled: no_allowed_projects` (and the JEV equivalent).
   - Flag off keeps `disabled_reason == "not_enabled"` (unchanged).
   - **Contract by flags (F15), identical in AC9 and tests:** flags off + empty list → reason `not_enabled`, `task_prompt_allowlist: "empty"`, `no_allowed_projects` in `config_errors` (preflight signal). Flags on + empty list → reason `no_allowed_projects` unless a higher-priority error precedes it in that feature's list.
4. `/api/meta` adds `"task_prompt_allowlist": state.allowlist_state`. It never returns names or counts of names.
5. The plugin probe needs no change: it already requires `task_prompt_capture is True`, which is now false while the list is empty.

### A2 — Ingest gate (`task_store.py`, `routes/events.py`) → AC6, AC7

1. `routes/events._derive_task` passes `allowed_projects=features.current().allowed_projects` to `task_store.on_event_inserted` (new keyword argument).
2. In `on_event_inserted`, when `body.task_prompt_text` is present on a task turn, the prompt is stored only if **all** hold, else it is discarded and nothing about it is written anywhere:
   - `capture_enabled` (unchanged check; outcome `discarded_disabled`);
   - `body.prompt_eligibility == PROMPT_ELIGIBILITY` (outcome `discarded_no_eligibility`; F1: an older plugin or a legacy spool cannot deliver a prompt);
   - **allowlisted proven root (AC6, AC7, r2 F6)**: `await task_store.prompt_root_eligible(db, ref, allowed_projects)` is true, evaluated on the **upserted row** (after `_upsert_task`), not on the body. Otherwise outcome `discarded_not_eligible`. This helper is the **single** allowlist and root check for both ingest and JEV (A3), so each mutation site in tests-other exists exactly once.
   `prompt_root_eligible(db, ref, allowed) -> bool` (new, in `task_store.py`, shared with A3): one `SELECT project_name, hierarchy_status, parent_task_ref, root_task_ref FROM tasks WHERE id = :ref`. True only if the row exists, `project_name in allowed`, and the row is a proven root (Shared definitions). A `child` row (parent fields, or `task_hierarchy='child'` alone) or an `unknown` row is never eligible. No parent row is read (r1 F4 → resolved by root-only rule (r2)).
3. Keep the existing order and behaviour otherwise: the metadata event is inserted and counted in `inserted` exactly as before (201 single / `inserted` in batch). The prompt is never rejected as a validation error.
4. `task_prompt_text` already never reaches `token_events`; keep it that way (no new column, no tag, no log line with its content).
5. Duplicates (`client_event_id` conflict) already skip derivation, so a replayed or duplicate delivery can never store a prompt that the first delivery did not; behaviour is identical for `/api/events` and `/api/events/batch`.
6. **Q1 decision (binding):** `parent_task_ref` is still computed from the child event's **own** `project_name` (`task_store.py` `_upsert_task`). Under the root-only rule no child stores a prompt anyway, so the defect affects hierarchy data only (R5). The pre-existing wrong `parent_task_ref` on cross-project children is **not** fixed here; it is logged as a known defect (status.md Open Items, AGENTS.md Gotchas).
7. **Becomes child → null prompt (r1 F5, r2 F1/F7).** In `on_event_inserted`, right after `_upsert_task` and for **every** task-turn event (with or without a prompt), run in the same transaction:
   `UPDATE tasks SET prompt_text = NULL, prompt_purged_at = :now, updated_at = :now WHERE id = :ref AND hierarchy_status = 'child' AND prompt_text IS NOT NULL`
   (`secure_delete` is already on for the engine, `database.py`; same columns as `retention.py`'s purge). A non-zero rowcount sets outcome `prompt: "purged_child"`. `prompt_captured_at` stays set, so `_store_prompt` (first write wins, `WHERE prompt_captured_at IS NULL`) never re-stores it, and JEV skips the task (A3, and `prompt_purged` as today).
   - Why this single statement is enough: `_upsert_task` is the only code that changes `hierarchy_status`, and it changes only the row `ref` of the current event (`child` is never downgraded). The re-root `UPDATE` rewrites `root_task_ref` only on rows that already point at `ref`, which are children, and children never hold a prompt. So no descendant walk is needed (r2 F7 → resolved by root-only rule). `_upsert_task` itself is not changed.
   - This covers the late-child ordering: a prompt stored while the task was a root, then an event of the same turn carrying parent fields or `task_hierarchy='child'`.
   - An allowlist removal alone never nulls anything (no automatic purge).
8. **Validation responses never echo input (F12).** Today pydantic/FastAPI echo `input` (e.g. an over-long `task_prompt_text`): the single endpoint via the default 422 handler, the batch via `"error": str(exc)`. Fix: the batch item `error` becomes `"; ".join(f"{'.'.join(map(str, e['loc']))}:{e['type']}" for e in exc.errors(include_input=False, include_url=False))` (a `ValueError` keeps its fixed message); `main.py` gets a `RequestValidationError` handler returning the usual `{"detail": [...]}` 422 with `input` and `ctx` removed from each error.

### A3 — JEV gate (`jev_scorer.py`, `routes/tasks.py`) → AC8

1. New helper in `jev_scorer.py`: `async def project_gate(session, ref) -> Optional[str]`. It reads `features.current().allowed_projects` **at call time** and returns `"project_not_allowed"` unless `task_store.prompt_root_eligible(session, ref, allowed)` is true (the same proven-root + allowlist check as ingest, r2 F6). A child row, an `unknown` row, a root row whose `root_task_ref` points at another id, or an unlisted project is off.
2. **Checks (r1 F6, r2 F8):**
   - **Before every provider attempt** (first try, same-provider retry and fallback) is the authoritative gate: at the existing `fresh` re-read inside the `while True` loop, after the retention check and before `spend`/`_reserve`/`post_evaluation`, `project_gate` runs. On a hit: `outcome.error_type = "project_not_allowed"`; `outcome.status = "skipped"` if `outcome.attempts` is empty (the gate tripped before the first attempt of that task), else `"error"` (an attempt already started); return. No further request is sent; the ledger and `evaluator_attempts` hold only attempts really made. This mirrors the existing `prompt_expired`/`prompt_purged` branch at the same place.
   - `evaluate_task` also calls `project_gate(session, ref)` **right after** the existing `evaluator_task` skip, before the prompt read and the `already_scored` lookup. On a hit: `return TaskOutcome("skipped", error_type="project_not_allowed")`. This entry check is a *redundant protection* (the loop gate covers the first attempt too); it only gives a not-allowed task that reason ahead of `no_prompt`/`already_scored` and avoids a cooldown wait. The existing `_record` path writes the row (no schema change).
   - Config generations: the allowlist is never cached per run (`run_evaluation`'s `state` is used only for the budget), so an in-flight run re-checks the current list before each attempt. A restart is a new generation: in-flight runs die with the process and the existing `recover_after_restart` marks them `aborted`; nothing is resumed under the old list.
3. `routes/tasks._skip_reason` (the `/evaluate` pre-filter) calls the same helper after its `evaluator_task` check, so `/evaluate` reports `{"task_ref", "reason": "project_not_allowed"}` up front. The worker check stays authoritative (a list change plus restart between queue and run is still caught).
4. `features.current()` is evaluated once per service start (tests use `features.refresh()`). Removing a project from the env and restarting blocks its stored tasks from JEV. **No purge** is triggered; stored prompts expire through the normal 30-day retention.

### A4 — Pilot select filter (`routes/tasks.py`, `jev_pilot.py`) → AC8 (Q3)

1. `GET /api/tasks` accepts `allowed_only: bool = False`. When true:
   - it first calls `require_sensitive_auth(x_ingest_token=..., origin=...)` (same pattern as `include_prompt` in `get_task_detail`), so allowlist membership cannot be probed without the token (planner decision; keeps "names never exposed" meaningful);
   - it adds `t.project_name IN (<allowed>)` to the `WHERE` clause with bound parameters. An empty list yields `items: []`, `total: 0`.
2. `jev_pilot.Api.all_root_tasks` passes `allowed_only="true"` (the client already sends `X-Ingest-Token` via `self.headers`). `is_eligible` and `select_sample` are unchanged.

### A5 — Safe producer example (`token_inspector_client.py`, `README.md`) → AC11, AC12

Tighten `token_inspector_client.py` in place (no new module or abstraction):

1. **Endpoint and token.** Posts only to `{base_url}/api/events/batch`; sends `X-Ingest-Token` when `ingest_token` is set (already true; keep).
2. **Injectable transport for tests.** Constructor gains `transport: Optional[httpx.AsyncBaseTransport] = None`, passed to `httpx.AsyncClient(timeout=..., transport=transport)`.
3. **Status and ack (F8).** `_send` inspects the response. Module function `valid_ack(body, n) -> bool`: `body` is a dict whose `inserted`, `duplicates`, `rejected` are each `type(v) is int` (bools excluded), `>= 0`, and sum to `n` (the batch size). The README uses this same function.
4. **Counters (F10).** Public ints; nothing is logged with payload content. Every event passed to `post_event` ends in exactly one counter:
   - delivered: `delivered_events += inserted + duplicates` (valid 2xx ack);
   - confirmed loss: `dropped_events` (queue full; existing name kept for `test_resilience.py`), `serialization_failed` (payload not JSON-serializable: checked with `json.dumps` at enqueue, dropped there so it cannot poison a batch), `rejected_events += rejected` (valid ack), `http_failures += n` (non-2xx), `dropped_on_close` (see close);
   - unconfirmed delivery: `transport_unconfirmed += n` (transport exception, timeout, or cancellation of an in-flight send; the server may have committed), `malformed_acks += n` (2xx with an invalid ack);
   - read-only properties `lost_events` (sum of confirmed loss) and `unconfirmed_events` (sum of the unconfirmed pair).
   - **Close/drain:** `close(timeout)` stops intake (a later `post_event` returns `False` and adds 1 to `dropped_on_close`), lets the worker drain for up to `timeout`, then cancels it. `_send` catches `CancelledError`, adds the in-flight batch to `transport_unconfirmed` and re-raises; events still queued after cancellation are added to `dropped_on_close`.
5. **Stable `client_event_id` (F9).** At enqueue time (`post_event`), if the payload has no truthy `client_event_id`, set `uuid.uuid4().hex` and write it back to the caller's object: onto a `TokenInspectorEvent` instance, or into the caller's **dict** (`event["client_event_id"] = id`; the queued payload is still a copy). Resending the same object or dict therefore reuses the id. A caller-provided id is never changed. `post_event` keeps returning `bool` (existing test contract).
6. **Bounded queue.** Keep `asyncio.Queue(maxsize=max(1, queue_max))` and `put_nowait`; never block the caller; never raise from `post_event`.
7. **Sensitive fields.** Remove `prompt_text` and `error_message` from the `TokenInspectorEvent` dataclass (constructing with them now raises `TypeError` = refusal). A single payload step applied to both dataclass and dict inputs drops the keys `prompt_text`, `task_prompt_text`, `error_message` before enqueue (covers dicts and dataclass subclasses). Keep `error_type` and `http_status`.
8. **Keep (Q5):** `prompt_hash`, `prompt_length` fields and the `sha256_prompt_hash` helper.
9. Existing `tests/test_resilience.py` must stay green unchanged.

README `push_token_event` (F11: a thin wrapper over the bounded client, never HTTP on the caller's path):

- The README block imports `TokenInspectorClient`/`valid_ack` from `token_inspector_client.py` and keeps **one** module-level client, created lazily on first use from `TOKEN_INSPECTOR_URL` (default `http://127.0.0.1:8100`), `TOKEN_INSPECTOR_PROJECT` (default `hermes`) and `TOKEN_INSPECTOR_API_KEY` (the plugin's variable; never a literal secret). `project_name`/`base_url` parameters are dropped (one client = one project header; planner decision).
- Signature has **no** `prompt_text` and **no** `error_message` parameter; keeps `error_type`/`http_status`.
- It builds the payload, sets `client_event_id = uuid.uuid4().hex` when none is given, and returns `await client.post_event(payload)` (enqueue only: `put_nowait`, returns `bool`). Status, ack (`valid_ack`) and counters are the client's. A documented `close_token_inspector()` drains the client at shutdown.
- The README "Batch ingest" snippet also sends `X-Ingest-Token`, carries no `prompt_text`, and checks its ack with `valid_ack`.

---

## Part B — Hermes producer plugin (`C:\Users\Bentego_Admin\Projects\token_inspector`)

The plugin stays bounded, synchronous on the hook path with **no network call for the decision**, fail-open, and privacy-first. All new hook-path code is wrapped in the existing `try/except: pass` pattern.

### B1 — Config (`config.py`) → AC1, AC3

1. `PluginConfig` gains:
   - `task_prompt_allowed_projects: frozenset[str] = frozenset()`;
   - `task_prompt_deny_path_globs: tuple[str, ...] = ("~/Projects/*-devir",)`.
2. `load()`:
   - `task_prompt_allowed_projects` ← `entry.get("task_prompt_allowed_projects")` via `_strings`, then strict normalization (drop invalid), as a `frozenset`. Missing/empty → empty (default off).
   - `task_prompt_deny_path_globs` ← the default glob **plus** user entries from `entry.get("task_prompt_deny_path_globs")` (via `_strings`, cap 64), de-duplicated, default first. User entries add to the default and never replace it.
3. Globs are expanded (`~`) at match time, not at load, so tests can monkeypatch `HOME`/`USERPROFILE`.

### B2 — Path deny and allowlist helpers (`project.py`) → AC3

1. `def runtime_candidates() -> tuple[Path, ...]`: the same sources as `_runtime_cwds()` (agent cwd + `os.getcwd()`), but **strict (F7)**: `_runtime_cwds` gains `strict: bool = False`; in strict mode a failing `agent.runtime_cwd` import/call, a failing `os.getcwd()` or a failing `.resolve()` raises instead of being skipped or falling back to the unresolved path. `resolve_project` keeps the lenient mode (attribution unchanged).
   - **Fail-closed contract:** the caller (B3.4) treats an exception from `runtime_candidates()` **or** from `path_denied`, **or** an empty candidate tuple, as `denied`. A missing agent-cwd source therefore means no prompt (planner decision: the agent cwd is the path most likely to be a `-devir` clone).
2. `def path_denied(candidates, globs) -> bool`:
   - For each glob: `os.path.expanduser`; split at the first path segment containing `*`, `?` or `[`; `.resolve()` the static prefix (so a symlinked `~/Projects` still matches the resolved cwd); rejoin.
   - For each candidate and **each of its ancestors** (`(c, *c.parents)`): `PurePath(p).match(pattern)`. With an absolute pattern `match` requires a whole-path match and `*` never crosses a separator, so `~/Projects/*-devir` matches the clone root and, via ancestors, any directory inside it, but not `~/Projects/a/b-devir`.
   - Empty `candidates` or any exception (glob expansion or prefix resolve included) → return `True` (fail-closed).
   - Never logs; never returns the matched path.
3. `def prompt_project_allowed(name: str, config) -> bool`: `name in config.task_prompt_allowed_projects` (the event name is already normalized by `resolve_project`).

### B3 — Root-only decision, attach, emit, late-child strip and replay gates (`__init__.py`, `mapping.py`, `sink.py`, `spool.py`) → AC1, AC2, AC3, AC4, AC5, AC14, AC16, AC17

1. **Candidate only when possibly needed.** `mapping.api_state` computes `task_prompt_candidate` only if `config.capture_task_prompt` **and** `config.task_prompt_allowed_projects` is non-empty (still scrubbed, still memory-only).
2. **Registries.** Two new module-level `BoundedTTLMap`s with the bounds of `_subagent_state` (2048 entries, 86400 s): `_session_roots` (session id → `{"session_id"}`, root evidence) and `_denied_sessions` (session id → `{"session_id"}`, sticky path denial). `_cleanup_session` (finalize/reset) pops both, so a reused session id starts `unknown` until its next `on_session_start`. No parent decision is cached anywhere (r2 F5 → resolved by root-only rule).
3. **Positive classification (r1 F2, kept)**, `_lineage(session_id) -> "root" | "child" | "unknown"`:
   - `_on_session_start` records `_session_roots[sid]` unless `_subagent_state` already has `sid` (independent of `emit_session_events`). `_subagent_start` records the child as today, pops `_session_roots[child]` (child wins, for good) and calls `_sink.revoke_prompts(child)` for every child, whether or not it already attached a prompt (step 7).
   - `child`: `sid` is in `_subagent_state` (any entry, with or without parent fields: fail-closed). `root`: `sid` in `_session_roots` and not in `_subagent_state`. Anything else (plugin restart, TTL expiry, eviction, a session whose start this process never saw) → `unknown`.
   - Only `root` can carry a prompt (root-only rule). `child` and `unknown` never do, at any depth.
   - The backend lane confirms read-only on hermes, before B3, which hook calls fire for a primary vs. a delegated session and in which order; the result goes into the status.md Verification Log. If a primary session never gets `on_session_start`, its prompts stay off (fail-closed; no fallback).
4. **Own decision** `_prompt_decision(session_id, project) -> str`, called from `_pre_api_request` each time a new api state is created (after `_project_for`), stored in `state["prompt_decision"]`:
   1. `capture_task_prompt` off → `"off"`.
   2. `session_id` in `_denied_sessions` → `denied` (sticky).
   3. `path_denied(...)` true, or the B2.1 fail-closed contract → `denied`, and `_denied_sessions[sid]` is recorded.
   4. `prompt_project_allowed(project.name, config)` false → `not_allowed`.
   5. Otherwise `allowed`.
   It covers every attribution source (`hook_metadata`, `explicit_marker`, `path_alias`, `git_remote`, `git_root`, `session_assignment`, `config_fallback` = `hermes`) because it uses only the resolved name. Only the root's own workspace is checked (r2 F4 → resolved by root-only rule): a root whose cwd moves into a denied dir keeps the prompt already attached to its current turn, and its next request state is re-checked (R9).
5. **Attach gate (root-only).** `_attach_task_prompt(state, event) -> bool` pops the candidate as today and attaches only if, besides the existing `capture_ready` and once-per-turn checks, `state.get("prompt_decision") == "allowed"` **and** `_lineage(state["session_id"]) == "root"` at attach time. The lineage and decision checks run before `_prompt_sent` is written. It returns `True` only when it attached. `_post_api_request` passes that result to `_emit(event, prompt_authorized=attached)`; every other `_emit` call passes nothing (= `False`). The `_post_api_request` fallback path (no pre-request state) has no decision → no prompt. r1 F3 → resolved by root-only rule (r2): no ancestor walk, depth limit or cycle guard.
6. **Emit authorization and marker (before queue/spool; r1 F1, r2 F2).** `Sink.emit(event, *, prompt_authorized: bool = False)` (and `_emit` forwards the keyword):
   - always pops any incoming `prompt_eligibility` from the outgoing copy (a producer field can never pre-set it);
   - if `task_prompt_text` is present and `prompt_authorized` is false → pop `task_prompt_text` and `task_prompt_captured_at`;
   - if authorized but `normalize(event["_project_name"] or config.project_name)` is not on the allowlist → pop both (*redundant* with the attach decision; kept as defence in depth);
   - after `_scrub_prompt`, if a prompt still remains → set `prompt_eligibility = PROMPT_ELIGIBILITY`. The metadata event is always queued.
7. **Late-child strip (r2 F1).** `Sink.revoke_prompts(session_id)` runs on the hook thread and does no I/O: it records the id in `self._revoked` (a `BoundedTTLMap`, 2048 / 86400 s) and sets `self._revoke_pending = True`. Enforcement runs on the worker thread, the only thread that writes spool files:
   1. `Sink._send`: while building `outgoing` copies, events whose `session_id` is in `_revoked` lose `task_prompt_text`, `task_prompt_captured_at` and `prompt_eligibility` **before grouping**, so both `_post` and `_keep` (spool write) get the stripped copy. This covers events still in the queue and the batch being assembled.
   2. `Sink._worker`, each pass before `_replay`: if `_revoke_pending`, call `spool.strip_session_prompts(frozenset(revoked ids))` and clear the flag on success (on an exception the flag stays set for the next pass).
   3. New `Spool.strip_session_prompts(session_ids) -> int`: iterates the paths in `_prompt_index` (every pending or dead-letter file holding a prompt; a prompt without capture time never reaches a file because `strip_expired_prompts` drops it at spool time), reads each, pops the three fields from events whose `session_id` is in the set, and when anything changed recomputes `prompt_captured_at_min` and rewrites through `_write` (tmp + fsync + `os.replace` + dir fsync, re-index). Counter `revoked_prompts_stripped` += events stripped. Unreadable files are left to `maintenance` as today.
   4. `Sink._replay`: before `_post`, the same per-event revoked strip (*redundant* with 7.2; covers a pass where the rewrite failed).
   - Accepted residual (triage, R7): a batch already inside `client.post` when `subagent_start` lands is sent, and a spool file not yet rewritten when the process dies keeps its prompt. The backend nulls it when the child event arrives (A2.7), and JEV never scores a non-root. The revoked set is session ids only; nothing about it enters telemetry or logs.
8. **Transmission/replay gate (AC5, r1 F1).** `Sink._post(client, project_name, events)`, per event: strip `task_prompt_text`/`task_prompt_captured_at`/`prompt_eligibility` when `project_name` is not on the **current** allowlist **or** the event lacks the current `prompt_eligibility` value (legacy pre-upgrade spool files, or any path that bypassed emit). Same place and style as the existing capture gate; covers live batches and `_replay`. The metadata events are still posted and the spool file is still deleted on 2xx.
9. No gate logs the project name, the path or the glob. No new tags.

### B4 — No free-form error text (`mapping.py`) → AC10

1. New helper `_identifier(value, default="other") -> str`: `str(value)` if it fully matches `[A-Za-z0-9_.]{1,64}`, else `default` (Q4 decision).
2. `llm_error` (line ~252): drop `error_message`. `error_type = _identifier(kwargs.get("error_type") or "APIError")`. The existing `timeout` status detection may still read the raw kwarg locally; the raw value is never emitted. `http_status` from `kwargs.get("status_code")` unchanged.
3. `tool_event` (line ~286): drop `error_message`. Emit `error_type = _identifier(kwargs["error_type"])` only when the hook passes `error_type`; `status` and `tags.tool_status` keep carrying the category.
4. `session_event` (line ~300, "any others"), **fixed categories (F13)**: the discriminator from `kwargs.get("reason") or kwargs.get("error")` is kept only if it is one of `SESSION_REASONS = ("end", "shutdown", "session_boundary", "new_session")` (= backend `task_store.SESSION_END_REASONS`); empty stays empty (`finish_reason` falls back to `kind` as today); anything else becomes `"other"`. The result is used as `finish_reason` **and** as the `session_event_id` input, so no free text or identifier-shaped value is emitted or hashed into ids. Ids change only for discriminators outside the list.
5. **Residual limitation (F13, Q4 kept):** `error_type` on `llm_error`/`tool_event` stays identifier-restricted, not categorised, so an identifier-shaped secret or name (e.g. `sk_live_abc123`) can still pass. Documented in ADR-002 and the plugin README.
6. The backend keeps accepting `error_message` from older producers (no backend change).

---

## Schema Dependencies

None. No new tables or columns. Used as-is: `tasks.project_name`, `tasks.hierarchy_status`, `tasks.parent_task_ref`, `tasks.root_task_ref`, `tasks.prompt_*`, `task_evaluations.status`/`error_type`.

## Auth and Permissions

- Ingest keeps `require_ingest_auth`. Capture still requires `INGEST_TOKEN` (≥ 16 chars).
- `require_sensitive_auth` (token + Origin check) now also guards `GET /api/tasks?allowed_only=true`, in addition to `include_prompt`, labels, evaluate and purge.
- Host allowlist / `tailscale serve` setup unchanged.

## Environment Variables (new)

| Where | Name | Default | Meaning |
|---|---|---|---|
| backend | `TASK_PROMPT_ALLOWED_PROJECTS` | unset (= empty = off) | Comma-separated exact project names allowed for prompt storage **and** JEV |
| plugin config (`plugins.entries.token_inspector`) | `task_prompt_allowed_projects` | `[]` | Projects whose prompts may be sent |
| plugin config | `task_prompt_deny_path_globs` | `["~/Projects/*-devir"]` (always included) | Extra workspace globs whose sessions never send prompts |

Both lists must allow a project for a prompt to be stored. Both stay empty in production until the Activation Gate.

## Documentation (maintenance-docs step, before the Hermes code review) → AC13

Owned by this lane, executed by Claude directly after tests are green:

1. `docs/adr/002-task-prompt-retention-and-jev.md`: the two allowlists (both must allow, default off, ambiguous → off, exact names, `hermes` fallback may be listed), the path-deny rule (default glob, extras add, wins over allowlist, matched on the root's resolved paths, never on names), the **root-only rule** (r2: prompts only for proven roots — plugin positive root classification, backend proven-root definition — children never carry one; late child classification strips queued/spooled prompts and nulls a stored one; Q1 defect affects hierarchy only), D10, JEV `project_not_allowed` with no automatic purge, and **accepted risk R1** (with `hermes` allowlisted, PEGA work in general `hermes` chat is captured and can reach JEV; D10 cannot be enforced there). Also R2 (backend cannot tell clones apart), R3 (`HERMES_DEVIR_REPO` override needs an extra glob), R4 (marker attribution weakens name-based protection), R5 (child prompts never stored), R6 (identifier-shaped `error_type`), R7 (unknown lineage = off, and the late-child residual), R9 (root cwd moving into a denied dir), the eligibility marker with the emit authorization, and the privacy boundary (F14, no counts in logs).
2. **`claude -p` hand-off-runtime rule** (written in ADR-002 and carried into O9 in `Plans/task-telemetry-jev-pilot/status.md`): any future producer for the `claude -p` hand-off runtime must send **no prompt text for hand-off runs or their subtasks**, regardless of allowlists (D10); metadata only. Until O9, that runtime is not instrumented at all.
3. `README.md`: capability table row "Store one scrubbed prompt per task" gains "backend `TASK_PROMPT_ALLOWED_PROJECTS` + plugin `task_prompt_allowed_projects`, path not denied"; JEV row gains "project on the backend allowlist"; Production env table gains `TASK_PROMPT_ALLOWED_PROJECTS`; `/api/meta` description gains `task_prompt_allowlist`; the `push_token_event` and batch snippets per A5.
4. `AGENTS.md`: module map (allowlist in `features.py`, gate in `task_store.py`/`jev_scorer.py`), Data and Privacy Contract (allowlist + root-only rule + child-transition null), API families (`allowed_only` sensitive), Gotchas: new entries for the strict normalization trap (`normalize_project_name` falls back to `hermes`), and the cross-project `parent_task_ref` known defect.
5. Project `CLAUDE.md`: Critical Technical Patterns line for the allowlist; **RP 9 rewritten to "gated four times"** (backend flag + token + interval + non-empty backend allowlist; plugin `capture_task_prompt` + plugin allowlist + path not denied; `/api/meta` probe) with the check `task_prompt_capture_disabled_reason == no_allowed_projects` / `task_prompt_allowlist`.
6. `CHANGELOG.md` `[Unreleased]`: allowlist, path deny, JEV skip reason, `/api/meta` field, `allowed_only`, safe client, plugin no longer sends `error_message`.
7. `ARCHITECTURE.md`: privacy section gains the fourth gate (data flow unchanged otherwise).
8. Plugin `README.md`: the two new config keys, their defaults, and that `error_message` is no longer sent.
9. `Plans/task-telemetry-jev-pilot/status.md`: Activation Gate phase (a) gains `- [ ] backend + plugin allowlists set, deny globs reviewed (/api/meta: task_prompt_allowlist = configured, no no_allowed_projects in config_errors)`; step (c)-1 requires `task_prompt_capture=true` with no disabled reason **and** `task_prompt_allowlist=configured`; O9 gains the hand-off rule; O10 gets "docs done, resolution pending final review" with a pointer to this plan. **O10 is marked resolved only by the review lane, after the final review passes (F17).**
10. `CONTRIBUTING.md` / `.github/workflows/ci.yml`: no change expected (new tests live in `tests/` and run in the existing job); confirm and note "no change" in status.md.

## Acceptance Criteria

The authoritative AC text (AC1–AC18) is in the summary `## ACCEPTANCE_CRITERIA`. Step map:

| AC | Steps |
|---|---|
| AC1, AC2 | B1, B3.1, B3.4–B3.6 |
| AC3 | B1, B2, B3.4 |
| AC4 | B3.2–B3.5 |
| AC5 | B3.7.4, B3.8 |
| AC6 | A2.1–A2.5, A2.8 |
| AC7 | Shared definitions (proven root), A2.2 (`prompt_root_eligible`), A2.6–A2.7 |
| AC8 | A3, A4 |
| AC9 | A1, Shared definitions (privacy boundary) |
| AC10 | B4 |
| AC11 | A5.1–A5.9 |
| AC12 | A5 README |
| AC13 | Documentation |
| AC14 | Shared definitions (marker), A2.2, B3.6, B3.8 |
| AC15 | status.md `## Upgrade / Rollback Runbook` |
| AC16 | B3.3 (revoke call), B3.7, A2.7 (backend side) |
| AC17 | Shared definitions (attach authorization), B3.5–B3.6 |
| AC18 | tests-other.md `## Mutation checks` |

## Out of Scope

- Legacy `prompt_text` / `STORE_RAW_PROMPTS` path and `complexity_scorer.py`.
- Instrumenting the `claude -p` hand-off runtime or any Claude Code producer (O9); only the rule is written down.
- Automatic purge of prompts already stored for a project removed from the list.
- Any schema change; fixing the cross-project `parent_task_ref` defect (Q1).
- Dashboard/frontend changes; name-pattern denylists.
- Enabling any flag in production, prod deploy without user approval, plugin install or gateway restart without user approval.
