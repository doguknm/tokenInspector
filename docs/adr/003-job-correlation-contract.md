# ADR-003: Job Correlation Contract

**Status:** Accepted (implemented; in production since 2026-10-03)
**Date:** 2026-09-28
**Plan:** `Plans/o9-job-correlation-cc-producer/` (backend.md "Shared definitions", J0–J7; status.md Drift Log "J0 verdict", "J2 measurement")

## Context

A launcher job (`hermes.sh send`, `hermes.sh devir-baslat`) runs one agent process that makes many LLM calls over one or more tasks. Before this decision nothing tied those calls to the job, so "what did this review cost" had no answer. The job context has to travel from the launcher to the producer that builds the events, without adding a table, a new event type or any free text to telemetry.

## Decision

### 1. Env contract (one contract for both producers)

The launcher exports, the producer (Hermes plugin `job.py`, Claude Code hook `cc_config.py`) reads once per process:

| Env var | Producer rule (before sending) | Backend rule |
|---|---|---|
| `TOKEN_INSPECTOR_JOB_REF` | tag `job_ref` only if it fully matches `^(devir-)?[0-9]{8}-[0-9]{6}-[0-9]{1,10}$` (the launcher's own id shape, max 32 chars), else omitted | same regex, else dropped |
| `TOKEN_INSPECTOR_WORK_TYPE` | tag `work_type` only if it is in the closed list `WORK_TYPES` (the same list as the backend), else omitted | in `WORK_TYPES` → kept; any other present value → `other`; absent or empty → absent |
| `TOKEN_INSPECTOR_JOB_ATTEMPT` | integer tag `job_attempt` only if it fully matches `^[1-9][0-9]{0,3}$`, else omitted | an `int` ≥ 1 → kept, anything else dropped |

The `/hermes` skill passes `HERMES_WORK_TYPE` / `HERMES_JOB_ATTEMPT` to `hermes.sh`, which validates them and exports the three variables right before the agent starts. Invalid values are omitted silently (never logged). The producer adds `runtime` and `producer` itself; they never come from env.

**J0 verdict (source trace, status.md Verification Log): `hermes -z` discovers plugins and builds its agent in its own process**, so the env of the launched process is what the plugin sees. One `hermes -z` process is one job; a gateway process has no job env.

### 2. Reserved tag keys and closed lists

Reserved keys: `job_ref`, `runtime`, `work_type`, `job_attempt`, `producer`, plus the backend-only mark `attribution_invalid`.

- `WORK_TYPES = brainstorm, review, code, devir, k1, k2, other`
- `RUNTIMES = hermes-agent, claude-code@windows, claude-code@hermes, app`
- `PRODUCERS = hermes-plugin, claude-code-hook, app-provider`

`routes/events.py` `_normalize_reserved` normalizes them before `_clean_tags`. It **never rejects an event**: invalid values are dropped or mapped. A `runtime` or `producer` that is present and removed (invalid value, or a pairing mismatch, ADR-004) marks the event `attribution_invalid: true`; an incoming mark from a producer is removed first. A marked event is stored but never counted by the jobs API and never inferred as legacy `hermes-agent`. Normalization never increases the key count.

`job_attempt` (launcher retry of the same job) is unrelated to the event field `attempt` (per-call retry); they are never merged.

### 3. Tag cap

The worst-case plugin tags with the new keys measure 17 keys / 722 bytes (J2, pinned by `tests/fixtures/worst_case_plugin_tags.json`, byte-identical in both repos). The cap is `TAG_BYTES_MAX = 1024` bytes, keys ≤ 20. Measure again before adding a tag key.

### 4. One job per task

A task gets the first valid `job_ref` it sees (`tasks.job_ref`, schema v11); a later, different `job_ref` is never applied and increments `tasks.job_ref_conflicts` (a count of conflicting **calls**). The job of an event is the job of its task when it belongs to one, else its own `job_ref` tag, so every call of a task lands in the same job.

A job is an aggregate, not a table. `GET /api/jobs` counts only `llm_request` events that are not evaluator (JEV) usage and not `attribution_invalid`. Cost is priced calls only (`cost_usd` null when none is priced; `unpriced_count` shown, never zero). Conflicts are reported as conflicting calls (`conflict_count`) and affected tasks (`conflict_task_count`) from a separate `tasks` subquery. There is no job-duration event: first/last event times are not end-to-end job time.

### 5. Install order

Inside the single deploy the backend with the new cap and normalization goes live **before** any plugin that sends the new keys. Exported env vars are harmless until the new plugin is installed.

## Consequences

- Review jobs run from the AIFromScratch copy of `hermes.sh` land in project `hermes`; cost per originating project for them is out of scope.
- An old plugin or an unpatched launcher copy produces events without job tags; they are not in any job (not an error).
- Adding a runtime or producer needs a change here, in ADR-004's pairing table and in both producers' closed lists.
