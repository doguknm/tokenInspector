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

## Critical Technical Patterns

- Raw prompts, responses, and tool arguments are off by default.
- `STORE_RAW_PROMPTS=0` in production.
- Prompt payload is accepted only as an explicit opt-in and is capped at 3000 characters.
- Ingest remains fail-open for producers but validation remains strict at the backend.
- `client_event_id` is idempotent per project using a partial unique SQLite index.
- SQLite requires WAL, `busy_timeout`, foreign keys, and additive migrations.
- Cache-read, cache-creation, reasoning, prompt, and completion tokens remain distinct.
- Requested model, resolved model, and pricing model remain distinct.
- Unknown pricing is `unpriced/no_rule`; never represent it as free.
- Task prompts and JEV are limited to projects on both allowlists (backend `TASK_PROMPT_ALLOWED_PROJECTS`, plugin `task_prompt_allowed_projects`; empty = off) and to proven root tasks; `-devir` hand-off clones are path-denied in the plugin.
- Deterministic complexity uses `request-shape-v1` numeric metadata, not raw prompt semantics.
- Filesystem repository inventory and event-backed observed activity are different datasets.
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

The backend inventory defaults to direct Git children of `~/Projects`, is bounded by `TOKEN_INSPECTOR_MAX_PROJECTS`, and returns only canonical name, directory name, discovery source, and a hash-first workspace identifier.

## Debugging

Start with `systemctl --user status token-inspector.service`, the backend journal, the relevant API response, and scoped SQLite metadata. For project anomalies trace `session_id`, `trace_id`, timestamp, `project_source`, and `project_confidence`; never inspect or copy raw content as a first diagnostic step.

## Development Workflow

```bash
cd /home/dogukan/Projects/tokenInspector
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile *.py routes/*.py
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
   - **Fix/check:** `curl -s http://127.0.0.1:8100/api/meta` — `task_prompt_capture_disabled_reason == "no_allowed_projects"` or `task_prompt_allowlist == "empty"` means the backend allowlist is empty; also look at `config_errors`. Check the plugin config (both keys), the session's workspace path, and whether the session started after the last gateway restart; wait one probe interval or restart the gateway.

10. **Test run or server hangs at exit after the lifespan ends**
   - **Symptoms:** A process that ran the app lifespan (e.g. a `DB_PATH=:memory:` subprocess test, or uvicorn on shutdown) finishes its work but never exits; a faulthandler dump shows only an `aiosqlite ... _connection_worker_thread` and `threading._shutdown`.
   - **Root cause:** aiosqlite ≥ 0.22 worker threads are non-daemon. A pooled connection that was never closed keeps its thread alive, so interpreter shutdown waits forever. Whether it happens depends on GC timing, so it can pass alone and hang in the full suite.
   - **Fix/check:** The lifespan ends with `await engine.dispose()` (`main.py`). Any other code that creates an engine must dispose it too; subprocess tests pass `timeout=` so a hang fails instead of blocking CI.

11. **A secure-delete test passes on Windows but fails on hermes (Linux)**
   - **Symptoms:** A test that simulates leftover freed pages (e.g. `test_backup_physical_cleanup_is_retried_until_free_pages_are_gone`) fails its own precondition on hermes: the text is already gone from the file.
   - **Root cause:** Distro SQLite builds (Debian/Ubuntu) compile with `SQLITE_SECURE_DELETE` on by default, so freed pages are zeroed even on a connection that never set the pragma.
   - **Fix/check:** Tests that need leftover bytes set `PRAGMA secure_delete=OFF` explicitly on the connection that creates them. Run the suite on hermes too before a deploy.

12. **Dashboard URL returns `400 Invalid host header`**
   - **Symptoms:** `https://hermes.tail3a755d.ts.net` (or any other proxied name) answers `Invalid host header`, although `curl http://127.0.0.1:8100/api/meta` on hermes works.
   - **Root cause:** `TrustedHostMiddleware` (v10) only accepts `TOKEN_INSPECTOR_ALLOWED_HOSTS` (default `127.0.0.1,localhost`). `tailscale serve` forwards the tailnet name as the Host header.
   - **Fix/check:** Keep the `~/.config/systemd/user/token-inspector.service.d/remote-access.conf` drop-in (README → Run), then run `systemctl --user daemon-reload && systemctl --user restart token-inspector.service`. Check with `curl -s -o /dev/null -w "%{http_code}" https://hermes.tail3a755d.ts.net/api/meta` from a tailnet machine (expect 200).

13. **A nulled or purged prompt is still found in the `-wal` bytes**
   - **Symptoms:** A byte-level privacy test (or a manual `grep` of `<db>-wal`) still finds prompt text after a task became `child` and its prompt was NULLed, although `tasks.prompt_text` is NULL.
   - **Root cause:** `secure_delete` clears the page in place, but SQLite's WAL keeps the earlier frame that held the text until the next checkpoint. The child-transition null does not checkpoint; the retention purge does.
   - **Fix/check:** Run `PRAGMA wal_checkpoint(TRUNCATE)` (`retention.wal_checkpoint(db_path)`) before reading DB/WAL bytes; in production the retention loop or SQLite's auto-checkpoint removes it.

14. **A prompt test stores or sends nothing after O10**
   - **Symptoms:** A test that expects `tasks.prompt_text` (backend), a `task_prompt_text` on an emitted event (plugin) or a JEV provider call sees NULL, no prompt or `project_not_allowed`.
   - **Root cause:** Default-off allowlists and the root-only rule: the project must be listed, the event must carry `task_hierarchy="root"` and `prompt_eligibility="v1-allowed"`, the plugin session needs `_on_session_start` and the Sink needs `prompt_authorized=True`, and JEV rows must be proven roots.
   - **Fix/check:** Use the allowed-path fixtures (`_enable_capture` + `ROOT` in `tests/test_task_ingest.py`, the `jev` fixtures, plugin `_hooks(..., allowed=...)` + `_start`). Never relax the assertion instead.
