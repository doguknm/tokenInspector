# CLAUDE.md

Guidance for AI coding assistants working on Token Inspector.

## AI Tools

NOTEBOOKLM_NOTEBOOK_ID: 873a6470-05c2-40e1-be01-54b697f283aa

## Project Overview

Token Inspector is a standalone, privacy-first FastAPI service for LLM token observability. Connected producers send structured lifecycle events; the service performs idempotent ingest, pricing, SQLite persistence, and analytics for a vanilla JavaScript dashboard.

The Hermes integration is intentionally separate at `/home/dogukan/Projects/token_inspector`. Do not embed it into Hermes core.

## Read First

1. `AGENTS.md` — operational contract and module map.
2. `ARCHITECTURE.md` — boundaries, data flow, identity, privacy, and state.
3. `PROJECT_MEMORY.md` — current verified state, durable decisions, and open items.

## Production Runtime

```text
Service: systemd user unit token-inspector.service
Bind: 127.0.0.1:8100
Database: /home/dogukan/.local/share/token-inspector/token-inspector.db
Dashboard: http://127.0.0.1:8100/
```

Production uses `DB_PATH` explicitly. Never assume the repository-local default database is production. Do not commit, mirror, or upload SQLite files or backups.

Production runs the O10 release (backend `ddc7552`, schema 10). O9 (schema v11 + v12: jobs, cross-project child fix, Claude Code producer, versioned read-only export) is on `feat/task-telemetry-jev-pilot` only until the single O9 deploy (`Plans/o9-job-correlation-cc-producer/status.md` → Deploy Runbook); the Claude Code hook is not installed anywhere yet.

## Critical Technical Patterns

- Raw prompts, responses, and tool arguments are off by default.
- `STORE_RAW_PROMPTS=0` in production.
- Prompt payload is accepted only as an explicit opt-in and is capped at 3000 characters.
- Ingest remains fail-open for producers but validation remains strict at the backend.
- `client_event_id` is idempotent per project using a partial unique SQLite index; `cc-` ids (Claude Code producer) are unique across projects (v11, ADR-004).
- Reserved tag keys `job_ref`, `runtime`, `work_type`, `job_attempt`, `producer` are normalized, never rejected; an invalid or unpaired `runtime`/`producer` marks the event `attribution_invalid` (stored, never counted). Tags cap: 1024 bytes, 20 keys — measure before adding a key (ADR-003).
- SQLite requires WAL, `busy_timeout`, foreign keys, and additive migrations.
- Cache-read, cache-creation, reasoning, prompt, and completion tokens remain distinct.
- Requested model, resolved model, and pricing model remain distinct.
- Unknown pricing is `unpriced/no_rule`; never represent it as free.
- Task prompts and JEV are limited to projects on both allowlists (backend `TASK_PROMPT_ALLOWED_PROJECTS`, plugin `task_prompt_allowed_projects`; empty = off) and to proven root tasks; `-devir` hand-off clones are path-denied in the plugin.
- Deterministic complexity uses `request-shape-v1` numeric metadata, not raw prompt semantics.
- Filesystem repository inventory and event-backed observed activity are different datasets.
- The versioned export (`/api/export/v1/*`, schema v12) reads a snapshot by `token_events.ingest_seq`; any write that deletes or rewrites stored `token_events` rows must bump `export_state.revision` in the same transaction (AGENTS.md Gotchas, ADR-005).
- Absolute workspace paths and complete Git remote URLs must not enter telemetry storage.

## Project Identity

The dashboard exposes:

1. **Known Repositories** — bounded discovery under configured roots, including repositories with zero events.
2. **Observed Activity** — aggregation of `TokenEvent.project_name` for actual LLM events.

Canonical producer attribution order is maintained in the standalone Hermes plugin:

```text
hook metadata
→ optional explicit marker (disabled by default)
→ configured path alias
→ sanitized Git origin repository slug
→ Git root directory name
→ hermes fallback
```

The Claude Code producer (`producers/claude_code/cc_attribution.py`) uses: configured alias → sanitized Git origin slug → Git root name → `claude-code`, skipping names equal to the hostname or shaped like an IPv4 address; `tests/fixtures/attribution_vectors.json` is byte-identical in both repos and pins parity with the plugin.

The backend inventory defaults to direct Git children of `~/Projects`, is bounded by `TOKEN_INSPECTOR_MAX_PROJECTS`, and returns only canonical name, directory name, discovery source, and a hash-first workspace identifier.

## Debugging

Start with `systemctl --user status token-inspector.service`, the backend journal, the relevant API response, and scoped SQLite metadata. For project anomalies trace `session_id`, `trace_id`, timestamp, `project_source`, and `project_confidence`; never inspect or copy raw content as a first diagnostic step.

## Development Workflow

```bash
cd /home/dogukan/Projects/tokenInspector
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile *.py routes/*.py scripts/*.py producers/claude_code/*.py
node --check static/app.js

DB_PATH=/tmp/token-inspector-dev.db STORE_RAW_PROMPTS=0 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8100
```

Never delete the production database to re-seed pricing. Use settings APIs, model aliases, and recost dry-run/apply operations.

## Key File Locations

| Concern | Path |
|---|---|
| App startup and routers | `main.py` |
| SQLite engine/migrations | `database.py`, `migrations.py` |
| Event/pricing schema | `models.py` |
| Ingest/privacy/idempotency | `routes/events.py` |
| Analytics/inventory join | `routes/analytics.py`, `project_inventory.py` |
| Pricing aliases/recost | `routes/settings.py` |
| Legacy opt-in AI scorer | `complexity_scorer.py` |
| Dashboard | `static/index.html`, `static/app.js`, `static/style.css` |
| Feature gates / sensitive auth | `features.py`, `auth.py`, `routes/meta.py` |
| Task derivation (task = Hermes turn) | `task_store.py` |
| Tasks API, labels, evaluate, retention endpoints | `routes/tasks.py` |
| Prompt scrubber (`task-redact-v1`) | `redaction.py` (= plugin `task_redact.py`) |
| Retention / purge | `retention.py`, `scripts/purge_task_prompts.py` |
| JEV worker | `jev_scorer.py` |
| Pilot CLI | `jev_pilot.py`, `pilot_metrics.py` |
| Demo seed / rollback | `scripts/seed_tasks_demo.py`, `scripts/rollback_v10.sql`, `scripts/check_v9_app_on_v10.py` |
| Cost per job API (v11) | `routes/jobs.py`; job tag normalization in `routes/events.py` (`_normalize_reserved`); task → job in `task_store.py` |
| Cross-project parent repair | `scripts/repair_task_parents.py` (dry-run default) |
| Schema rollback runner | `scripts/rollback_schema.py` (+ `scripts/rollback_v12.sql`, `scripts/rollback_v11.sql`) |
| Versioned read-only export (v12) | `routes/export.py`; contract `docs/export-contract-v1.md`, `docs/adr/005-export-contract.md`; `ingest_seq` in `routes/events.py` (`_insert_event`), self-heal in `migrations.assign_missing_ingest_seq` |
| Claude Code producer (hook, installer) | `producers/claude_code/` (`cc_hook.py`, `install.py`, README) |
| Job contract / dedup authority | `docs/adr/003-job-correlation-contract.md`, `docs/adr/004-cross-source-dedup-authority.md` |
| Task prompt policy | `docs/adr/002-task-prompt-retention-and-jev.md` |
| Tests | `tests/` (browser suite: `tests/browser/`, marker `browser`) |
| Hermes producer plugin | `/home/dogukan/Projects/token_inspector` |

## Recurring Problems

1. **Phantom project attribution**
   - Check event `session_id`, `trace_id`, timestamp, and tags `project_source`/`project_confidence`.
   - Do not infer activity from filesystem presence.
   - Prompt markers must remain disabled unless explicitly opted in.

2. **Repository folder differs from canonical repository name**
   - Prefer configured alias, then sanitized Git `origin` slug, then root folder name.
   - Never store or display the full remote URL.

3. **Unknown model appears as zero cost**
   - Check `cost_status`, `cost_status_reason`, and `pricing_model`.
   - Add a pricing rule or alias and use recost with dry-run first.

4. **Unexpected or empty database**
   - Inspect the service `DB_PATH`; repository-local `token_inspector.db` is not production.

5. **Backend unavailable**
   - Producer plugin must remain bounded and fail-open; inspect its queue/flush behavior without blocking Hermes.

6. **`RuntimeError: ... is bound to a different event loop` in concurrent tests**
   - **Symptoms:** A test that fires many requests at once (e.g. `asyncio.gather` of 20 POSTs) passes alone but fails in the full suite.
   - **Root cause:** Each pytest-asyncio test runs in its own event loop; the async engine's pooled-connection queue stays bound to the loop of an earlier test and breaks once concurrency exceeds the pool size.
   - **Fix/check:** `tests/conftest.py` calls `await engine.dispose()` at the start of the `client` fixture. Keep that line; run `python -m pytest -q` twice to confirm.

7. **`Runner.run() cannot be called from a running event loop` after the browser tests**
   - **Symptoms:** Every async test after `tests/browser/` errors at setup.
   - **Root cause:** The sync Playwright API runs an event loop on the main thread while a `sync_playwright()` context is open; a session-scoped browser fixture keeps it open for the rest of the run.
   - **Fix/check:** Keep the `browser` fixture in `tests/browser/conftest.py` module-scoped. CI runs the browser suite as a separate job (`-m browser`).

8. **`Host key verification failed` when reaching hermes from Git Bash**
   - **Symptoms:** `ssh hermes …` or `git push hermes` from Git Bash fails although Windows PowerShell `ssh hermes` works.
   - **Root cause:** Git Bash's own `ssh.exe` mis-encodes the non-ASCII Windows home path and misses `~/.ssh/config` and `known_hosts`.
   - **Fix/check:** Use `/c/WINDOWS/System32/OpenSSH/ssh.exe` (the repo sets `core.sshCommand` to it); add `-o ClearAllForwardings=yes` because the ssh config forwards ports.

9. **Task prompts never arrive although `STORE_TASK_PROMPTS=1`**
   - **Symptoms:** `tasks.prompt_text` stays NULL for new Hermes turns.
   - **Root cause:** Capture is gated four times: (1) backend flag + `INGEST_TOKEN` (≥ 16 chars) + valid purge interval + a non-empty allowlist `TASK_PROMPT_ALLOWED_PROJECTS` that names the project; (2) plugin `capture_task_prompt: true` + the project on plugin `task_prompt_allowed_projects` + a workspace that is not path-denied (`~/Projects/*-devir` or an extra `task_prompt_deny_path_globs` entry); (3) the plugin's `/api/meta` probe (every 10 min) must see `schema_version ≥ 10` and `task_prompt_capture: true`; (4) the turn must be a root task: the plugin saw the session's `on_session_start` in this process (a session that continued across a gateway restart is `unknown` and sends no prompt) and no `subagent_start` names it.
   - **Fix/check:** `curl -s http://127.0.0.1:8100/api/meta` — `task_prompt_capture_disabled_reason == "no_allowed_projects"` or `task_prompt_allowlist == "empty"` means the backend allowlist is empty; also look at `config_errors`. Check the plugin config (both keys), the session's workspace path, and whether the session started after the last gateway restart; wait one probe interval or restart the gateway. If the plugin logged `late-child revocation limit reached` (counter `revocation_overflows` in its `counters.json`), every prompt is stripped until the gateway restarts: only late children that could have sent a prompt count toward the 2048 limit; restart the gateway to resume capture.

10. **Test run or server hangs at exit after the lifespan ends**
   - **Symptoms:** A process that ran the app lifespan (e.g. a `DB_PATH=:memory:` subprocess test, or uvicorn on shutdown) finishes its work but never exits; a faulthandler dump shows only an `aiosqlite ... _connection_worker_thread` and `threading._shutdown`.
   - **Root cause:** aiosqlite ≥ 0.22 worker threads are non-daemon. A pooled connection that was never closed keeps its thread alive, so interpreter shutdown waits forever. Whether it happens depends on GC timing, so it can pass alone and hang in the full suite.
   - **Fix/check:** The lifespan ends with `await engine.dispose()` (`main.py`). Any other code that creates an engine must dispose it too; subprocess tests pass `timeout=` so a hang fails instead of blocking CI.

11. **A secure-delete test passes on Windows but fails on hermes (Linux)**
   - **Symptoms:** A test that simulates leftover freed pages (e.g. `test_backup_physical_cleanup_is_retried_until_free_pages_are_gone`) fails its own precondition on hermes: the text is already gone from the file.
   - **Root cause:** Distro SQLite builds (Debian/Ubuntu) compile with `SQLITE_SECURE_DELETE` on by default, so freed pages are zeroed even on a connection that never set the pragma.
   - **Fix/check:** Tests that need leftover bytes set `PRAGMA secure_delete=OFF` explicitly on the connection that creates them. Run the suite on hermes too before a deploy.

12. **Dashboard URL returns `400 Invalid host header`**
   - **Symptoms:** `https://<tailnet-host>` (or any other proxied name) answers `Invalid host header`, although `curl http://127.0.0.1:8100/api/meta` on hermes works. The real tailnet name lives only in the systemd drop-in, never in repository docs.
   - **Root cause:** `TrustedHostMiddleware` (v10) only accepts `TOKEN_INSPECTOR_ALLOWED_HOSTS` (default `127.0.0.1,localhost`). `tailscale serve` forwards the tailnet name as the Host header.
   - **Fix/check:** Keep the `~/.config/systemd/user/token-inspector.service.d/remote-access.conf` drop-in (README → Run), then run `systemctl --user daemon-reload && systemctl --user restart token-inspector.service`. Check with `curl -s -o /dev/null -w "%{http_code}" https://<tailnet-host>/api/meta` from a tailnet machine (expect 200).

13. **A nulled or purged prompt is still found in the `-wal` bytes**
   - **Symptoms:** A byte-level privacy test (or a manual `grep` of `<db>-wal`) still finds prompt text after a task became `child` and its prompt was NULLed, although `tasks.prompt_text` is NULL.
   - **Root cause:** `secure_delete` clears the page in place, but SQLite's WAL keeps the earlier frame that held the text until the next checkpoint. The child-transition null does not checkpoint; the retention purge does.
   - **Fix/check:** Run `PRAGMA wal_checkpoint(TRUNCATE)` (`retention.wal_checkpoint(db_path)`) before reading DB/WAL bytes; in production the retention loop or SQLite's auto-checkpoint removes it.

14. **A prompt test stores or sends nothing after O10**
   - **Symptoms:** A test that expects `tasks.prompt_text` (backend), a `task_prompt_text` on an emitted event (plugin) or a JEV provider call sees NULL, no prompt or `project_not_allowed`.
   - **Root cause:** Default-off allowlists and the root-only rule: the project must be listed, the event must carry `task_hierarchy="root"` and `prompt_eligibility="v1-allowed"`, the plugin session needs `_on_session_start` and the Sink needs `prompt_authorized=True`, and JEV rows must be proven roots.
   - **Fix/check:** Use the allowed-path fixtures (`_enable_capture` + `ROOT` in `tests/test_task_ingest.py`, the `jev` fixtures, plugin `_hooks(..., allowed=...)` + `_start`). Never relax the assertion instead.

15. **Job tags missing on events (`/api/jobs` empty after a `hermes.sh` run)**
   - **Symptoms:** A job launched through `hermes.sh send` or `devir-baslat` finished, but `GET /api/jobs?days=1` does not list it; its events have no `job_ref` / `work_type` tag.
   - **Root cause:** One link of the chain is missing: the backend is below v11 (its 512-byte tag cap can reject the new plugin's events — plugin `counters.json` shows rejects; hence backend before plugin); the installed plugin predates O9 or the gateway was not restarted after install; the launcher copy used was not patched (S1–S3) so it exports no `TOKEN_INSPECTOR_JOB_*`; or a value failed its shape rule and was omitted silently (ADR-003).
   - **Fix/check:** `curl -s http://127.0.0.1:8100/api/meta` → `schema_version ≥ 11`; check the plugin commit of the installed copy and restart with `hermes gateway restart` (no job running); inspect only the tag keys of the session's events (`json_extract(tags_json, '$.job_ref')`, `$.runtime`), never content; confirm the launcher copy exports the three variables right before `$AGENT -z`. `test_launcher_env_contract` checks the launcher copies synthetically.

16. **Claude Code events missing**
   - **Symptoms:** Claude Code sessions produce no `runtime=claude-code@windows` / `claude-code@hermes` events, or they stop arriving.
   - **Root cause:** The hook is not installed (it is not, until the O9 deploy); the producer URL is wrong or its Host is not in `TOKEN_INSPECTOR_ALLOWED_HOSTS` (Windows reaches the backend through the tailnet name); an ingest token is required but not configured; or the cursor state is stuck behind a lock or a pending backlog.
   - **Fix/check:** `python producers/claude_code/install.py --dry-run` → `already_present=3` means installed. Check reachability with `curl -s -o /dev/null -w "%{http_code}" "$TOKEN_INSPECTOR_URL/api/meta"` (expect 200, not 400). Check a token exists without printing it: `test -s ~/.config/token-inspector/ingest-token && echo present`. Read `counters.json` in the state dir (`%LOCALAPPDATA%\token_inspector_cc\` or `~/.local/state/token_inspector_cc/`): rising `failed_posts` = transport/allowlist, `rejected` = backend validation, `lock_skips` = the transcript was locked by another hook (a lock older than 10 s is taken over, e.g. after a watchdog kill). The hook is silent and always exits 0 by design.

17. **Plugin tests fail to import on hermes from a temp worktree**
   - **Symptoms:** `python -m pytest` in a hermes worktree of the plugin repo fails at collection with an import error for the plugin package.
   - **Root cause:** The plugin's test `conftest.py` imports the package by the name `token_inspector`, so the directory holding the checkout must have exactly that name.
   - **Fix/check:** Create the worktree as `.../<tmp>/token_inspector` (e.g. `git worktree add /tmp/<run>/token_inspector <branch>`), run the suite from there with the venv python and the Hermes runtime python, then remove the worktree.

18. **A mutation runner runs on import or crashes on Windows test output**
   - **Symptoms:** Importing a mutation-runner script (to reuse a helper, or by pytest collection) starts applying mutations; or the runner dies with `UnicodeDecodeError` while reading a pytest subprocess's output on Windows.
   - **Root cause:** The runner had no `if __name__ == "__main__":` guard, so its module body executed on import; and `subprocess.run(..., text=True)` decodes with the Windows code page, which fails on non-UTF-8 bytes in test output.
   - **Fix/check:** Every runner has a `__main__` guard and calls `subprocess.run(..., encoding="utf-8", errors="replace")`. After any interrupted run, verify every mutation's original (`old`) string is present in the target file before committing.

19. **`test_repair_backup_accepts_real_concurrent_writer` fails intermittently on hermes**
   - **Symptoms:** The repair test fails with `sqlite error; nothing was changed` about 1 run in 5 on hermes; a rerun passes. Windows is green.
   - **Root cause:** The test's tight concurrent writer loop can starve the repair's `BEGIN IMMEDIATE` lock beyond `busy_timeout` (test timing, not the repair control; the script correctly refuses and changes nothing).
   - **Fix/check:** Fixed in O9 Phase 3 (`fdbb1e5`): the writer pauses 1 ms after each commit; the repair script and the assertion are unchanged, and the test passed 10/10 standalone on hermes. If it fails again, rerun it alone; a failure that repeats is a regression in the repair's lock or backup verification, not this flake. Never fix it by weakening the assertion.

20. **Browser test fails with `'builtin_function_or_method' object has no attribute '_pw_impl_instance_'`, or a CSV cell with `\r` reads back as `\n`**
   - **Symptoms:** A Playwright test errors inside `_impl_to_api_mapping.py` as soon as an event fires; or a downloaded CSV cell that holds a carriage return compares unequal after `csv.reader`.
   - **Root cause:** Playwright's sync API attaches bookkeeping to the handler object, which a builtin bound method such as `list.append` cannot hold; and `open()` without `newline=""` translates `\r` to `\n` before `csv.reader` sees it.
   - **Fix/check:** Pass `lambda d: downloads.append(d)` (a Python function) to `page.on(...)`; read downloaded CSV files with `open(path, encoding="utf-8", newline="")` (`tests/browser/test_export_download.py`).
