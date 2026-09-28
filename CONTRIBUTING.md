# Contributing

## Setup

```bash
cd /home/dogukan/Projects/tokenInspector
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
# optional browser suite
.venv/bin/pip install -r requirements-browser.txt && .venv/bin/python -m playwright install chromium
```

Use a disposable development database:

```bash
DB_PATH=/tmp/token-inspector-dev.db STORE_RAW_PROMPTS=0 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8100
```

Never run tests or local experiments against the production SQLite path.

## Workflow

1. Read `CLAUDE.md`, `AGENTS.md`, `ARCHITECTURE.md`, and `PROJECT_MEMORY.md`.
2. Add or update a regression test before behavior changes.
3. Keep migrations additive and privacy defaults unchanged.
4. Run the full verification gate.
5. Update authoritative docs and `CHANGELOG.md` when behavior or operations change.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile *.py routes/*.py scripts/*.py producers/claude_code/*.py
node --check static/app.js
git diff --check
```

On Windows use `python`, never `python3`.

`tests/browser/` (marker `browser`) runs only when Playwright is installed; it starts a live server on a seeded demo DB (`scripts/seed_tasks_demo.py`). Plugin tests live in the separate `token_inspector` repository.

Focused suites (all run in the default `pytest -q` and in CI's `test` job):

```bash
python -m pytest -q tests/test_job_correlation.py tests/test_jobs_api.py tests/test_migration_v11.py \
  tests/test_cross_project_parent.py tests/test_repair_task_parents.py tests/test_launcher_env_contract.py   # jobs (O9 phase 1)
python -m pytest -q tests/test_cc_*.py tests/test_dedup_authority.py                                        # Claude Code producer (O9 phase 2)
python -m pytest -q -m browser tests/browser/test_jobs_view.py                                              # Jobs view (Playwright)
```

- `test_launcher_env_contract.py` runs the real `hermes.sh` copies and the `/hermes` skill file from the Windows workspace with stubbed `ssh`/`scp`/agent; it skips any copy that is not on the machine (so CI skips it).
- The Claude Code producer must stay stdlib-only (`tests/test_cc_stdlib.py`); its tests run the hook as a subprocess with temp state and settings files and never touch the real `~/.claude/settings.json`.
- `tests/fixtures/attribution_vectors.json` and `tests/fixtures/worst_case_plugin_tags.json` are byte-identical copies of the plugin repo's files; a backend test compares them when the sibling plugin checkout exists (it is skipped otherwise). Change both repos together.
- Before a deploy, run both suites on hermes too, in temporary worktrees of the pushed branch; the plugin worktree directory must be named `token_inspector` (CLAUDE.md Recurring Problems).
- Mutation checks (break a control → the named test goes red → revert) use throwaway runner scripts with a `__main__` guard and `subprocess.run(..., encoding="utf-8", errors="replace")`; results go to the plan's `status.md` Verification Log.

## Safety

- Do not commit database files, backups, `.env`, credentials, private host identifiers, or raw telemetry payloads.
- Do not represent unknown prices as free.
- Do not store complete Git remotes or absolute workspace paths.
- Do not merge filesystem inventory and observed activity semantics.
- Test production-data corrections against exact trace/session provenance and take an online backup first.
- Hermes integration changes belong in the separate `token_inspector` plugin repository, not Hermes core.
