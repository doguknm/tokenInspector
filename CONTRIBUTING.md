# Contributing

## Setup

```bash
cd /home/dogukan/Projects/tokenInspector
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
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
.venv/bin/python -m py_compile *.py routes/*.py
node --check static/app.js
git diff --check
```

## Safety

- Do not commit database files, backups, `.env`, credentials, private host identifiers, or raw telemetry payloads.
- Do not represent unknown prices as free.
- Do not store complete Git remotes or absolute workspace paths.
- Do not merge filesystem inventory and observed activity semantics.
- Test production-data corrections against exact trace/session provenance and take an online backup first.
- Hermes integration changes belong in the separate `token_inspector` plugin repository, not Hermes core.
