# Project Memory

## Current State

Token Inspector runs as an enabled systemd user service on loopback and persists production data outside the Git repository. The standalone Hermes plugin is installed as version `0.2.1` and loaded by the restarted gateway. Backend and plugin repositories have clean working trees after the implementation commits described below.

The dashboard separates bounded filesystem repository inventory from event-backed observed activity. The configured default inventory root currently discovers 22 direct-child Git repositories. `docrag-guardrails` is the canonical Git remote identity for local directory `PEGADocRag` and has no telemetry activity.

## Durable Decisions

- Keep Token Inspector independent from Hermes core.
- Use lifecycle hooks and a bounded fail-open plugin queue.
- Store structured metadata and token counts; raw content remains opt-in and off in production.
- Use deterministic `request-shape-v1` complexity from numeric metadata.
- Keep request complexity separate from future execution-intensity metrics.
- Prefer configured project alias, then sanitized Git origin slug, then Git root directory name.
- Disable prompt marker attribution by default.
- Present Known Repositories and Observed Activity as separate datasets.
- Unknown pricing remains `unpriced/no_rule`, never zero/free.
- Keep production SQLite outside the repository with additive migration, backup, WAL, and atomic idempotency.

## Security and Privacy Boundaries

- Production uses `STORE_RAW_PROMPTS=0`.
- Producer defaults keep content and tool-argument capture disabled.
- Absolute project paths, full Git remotes, credentials, private network identifiers, and authenticated payloads are excluded from telemetry and external memory.
- Inventory exposes a hash-first workspace identifier rather than an absolute path.
- Backend binds to loopback by default; public exposure is not allowed.

## Verified Operations

- Backend suite: 16 tests passed; Python compile and JavaScript syntax checks passed.
- Plugin suite: 25 tests passed; Python compile passed.
- Backend service is active and dashboard returns HTTP 200.
- Gateway restarted after plugin `0.2.1` installation and emitted post-restart lifecycle events.
- Production inventory returned 22 repositories.
- Production observed activity returned only `hermes` after scoped cleanup.
- Two assistant-created E2E traces were identified by provenance and 8 associated events were deleted after an online SQLite backup; no `pegadocrag` events remain.
- `docrag-guardrails` appears as inventory-only with zero events/tokens.
- 2026-10-03: O9 deployed on hermes (schema 12, plugin `7a6d094`, Claude Code hook on Windows and hermes, export v1); live AC1.2 and AC2.2 observed. Details: `Plans/o9-job-correlation-cc-producer/status.md`.

## Open Items

- Backend repository is ahead of its remote; pushing is not part of this closeout without explicit instruction.
- Plugin repository has no configured remote status shown in this session; publishing is not part of closeout.
- `gpt-5.4-mini` remains unpriced until a verified pricing rule is added.
- Legacy AI prompt scorer remains available but is not the primary privacy-first path.
- Optional private-network dashboard exposure remains unconfigured.
- OpenAI subscription forecast is based on sparse percentage samples and must not be calibrated directly from Token Inspector token totals.

## Next Safe Action

Collect timestamped provider quota percentage observations during future work. For additional repository attribution, rely on canonical Git remote identity or configured aliases; do not re-enable free-form markers globally. Add missing model pricing only from a verified source, then run recost dry-run before applying.

## Authoritative Sources

- `README.md`
- `CLAUDE.md`
- `AGENTS.md`
- `ARCHITECTURE.md`
- `routes/events.py`
- `routes/analytics.py`
- `project_inventory.py`
- `/home/dogukan/Projects/token_inspector/README.md`
- `/home/dogukan/Projects/token_inspector/project.py`
- `/home/dogukan/Projects/token_inspector/complexity.py`
