# Feature Plan: prompt-allowlist-safe-producer

**Date:** 2026-09-27
**Status:** planned (locked after Hermes plan review r2)

## Summary
A per-project allowlist for task prompt capture and JEV scoring, and a safe producer example. The allowlist is off by default; unknown or ambiguous projects are off. It is enforced in the Hermes plugin before queue/spool (with a `-devir` clone path-deny), at backend ingest (spool replay included) and before every JEV provider attempt. Prompts exist only for proven root tasks. This closes status O10 of `task-telemetry-jev-pilot`, which blocks the Activation Gate.

## Problem
Task prompt capture and JEV had no per-project control. Partner decision D10 requires that PEGA projects, hand-off clones and their subtasks never have prompts stored or sent to JEV. A name-based denylist is unsafe, because `PEGADocRag` resolves to `docrag-guardrails`. The README producer example and `token_inspector_client.py` sent no token, ignored HTTP status and acks, and swallowed losses.

## Architecture decisions
- **Two independent allowlists, both must allow:**
  - plugin config `task_prompt_allowed_projects` (plus `task_prompt_deny_path_globs`, default `~/Projects/*-devir`; path deny beats the allowlist);
  - backend env `TASK_PROMPT_ALLOWED_PROJECTS`, which also gates JEV.
  - Exact names after strict normalization; every attribution source counts. The `hermes` fallback may be allowlisted (accepted risk R1).
- **Root-only prompts (r2 simplification):**
  - A prompt is attached only to a session positively classified as root (its start was seen in this process, and it has no `subagent_start`), whose project is allowed and whose resolved workspace is not denied.
  - Child sessions never carry a prompt.
  - Late child classification strips pending and spooled prompts in the plugin, and the backend nulls the stored prompt.
  - Proven root in the backend: `hierarchy_status='root'`, `parent_task_ref IS NULL`, `root_task_ref IS NULL OR = id`.
- **Prompt eligibility marker:**
  - `Sink.emit` keeps a prompt and stamps `prompt_eligibility: "v1-allowed"` only with the attach authorization.
  - Replay and the backend treat an unmarked prompt as ineligible (legacy spool).
- **`/api/meta`:**
  - reports `task_prompt_allowlist: configured|empty`, never names;
  - with the flags on, an empty list gives the disabled reason `no_allowed_projects`;
  - with the flags off it stays `not_enabled`, and the empty list is reported as a preflight `config_errors` entry.
- **JEV:**
  - the gate runs before every provider attempt: before the first attempt it records `skipped`/`project_not_allowed`, after that `error`;
  - removing a project blocks JEV without an automatic purge;
  - `jev_pilot select` uses the authenticated `allowed_only=true`.
- **Plugin error text:** the plugin sends `error_type` (`[A-Za-z0-9_.]{1,64}` or `other`) and `http_status`, never `error_message`. The session-event reason uses fixed categories.
- **Safe client:**
  - posts to batch only, with `X-Ingest-Token`;
  - strict ack validation (non-bool, non-negative ints summing to the batch size);
  - a stable uuid4 `client_event_id`, written back into dict and dataclass inputs;
  - split confirmed/unconfirmed loss counters and a bounded queue;
  - no prompt/error text;
  - the README helper delegates to it.
- **No schema change.** An upgrade/rollback runbook covers deploying the backend first and turning the flags off before any rollback.

## Planned file changes
- Backend: `features.py`, `routes/meta.py`, `routes/events.py`, `task_store.py`, `jev_scorer.py`, `jev_pilot.py`, `routes/tasks.py` (`allowed_only`), `main.py` (422 handler without input echo), `token_inspector_client.py`, `README.md`, `AGENTS.md`, `CLAUDE.md` (RP 9), `CHANGELOG.md`, `docs/adr/002-task-prompt-retention-and-jev.md`, and new tests `tests/test_prompt_allowlist.py`, `tests/test_safe_client.py`, `tests/test_docs_allowlist.py`.
- Plugin (`token_inspector`): `config.py`, `project.py`, `mapping.py`, `__init__.py`, `sink.py`, `spool.py`, and tests `tests/test_prompt_allowlist.py`, `tests/test_error_text.py`.

## Open questions
None blocking. Known items:
- A cross-project child never finds its parent row. This is a pre-existing hierarchy defect, logged separately.
- The `claude -p` hand-off runtime is not observed by the plugin; the rule is carried into O9.
- The real allowlist values are entered by the user at the Activation Gate.
