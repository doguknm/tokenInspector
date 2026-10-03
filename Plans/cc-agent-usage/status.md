# cc-agent-usage — status

Goal: agent identity and per-call complexity for Claude Code usage in Token Inspector — the DATA
layer (producer, ingest, storage, task derivation, read API fields, export). See
`goal-1-agent-complexity.txt` (goal 1), `goal-2-agents-tab.txt` (dashboard tab, later),
`goal-3-deploy.txt` (deploy, later). Branch `feat/cc-agent-usage` in both repos.

Orchestrator: an alternative-model (FCC) session; see `HANDOFF-orchestrator.md`.

## Phase Checkpoints

| CP | Scope | Status |
|---|---|---|
| CP1 | Analysis and plan on hermes (read-only, `gpt-6-astra`), open decisions answered | **done** — report `hermes/cc-agent-complexity-plan.md`; decisions below |
| CP2 | Integration in the Windows worktree (hermes default model, diff pulled and applied) | **done** — commit `1d6bcbd`; delta fix commit applied; suites green (see Verification Log) |
| CP3 | Docs and handover (Contract for goal 2, deploy steps) | **done** — this section; ADR-006 plugin note added |

## CP1 — Analysis and plan

- Run: `hermes chat --provider openai-codex --model gpt-6-astra`, read-only, one run, session
  `20261003_100214_0667b2`, ~18.5 min, 59.7 KB. Read-only detached worktrees
  `/tmp/ccag-…/tokenInspector` @`9bdc8b5` and `/tmp/ccag-…/token_inspector` @`7a6d094`; removed after.
- Report: `hermes/cc-agent-complexity-plan.md` (raw output kept at `hermes/cp1-out.log`).
- Citation check (orchestrator, before accepting): 12/12 backend citations and 4/4 plugin citations
  verified byte-for-byte against the pinned worktrees.

### Verified additions from the decision round (metadata-only, no content)

The plan left the sidecar schema "unknown from code" and the `plugin:<name>` shape as a guess. A
metadata-only read of the local Claude Code sidecar corpus (`subagents/agent-<id>.meta.json`,
**key names and type values only — no paths, no `description`, no transcript content**) resolved both:

- **`agentType` is present in 137/137 sidecars.** Key set: `agentType` (137), `spawnDepth` (137),
  `description` (136, free text — never sent), `toolUseId` (135), plus `requestShape`/`requestNonInteractive`
  (129), `model` (52), `isFork` (8), `name` (3).
- **Value shapes:** 130/137 match the safe-id shape `^[A-Za-z][A-Za-z0-9_-]*$`, length 4–25;
  7/137 are `caveman:<agent>` — so the **real plugin-agent shape is `<plugin>:<agent>`**, not the
  report's guessed `plugin:<name>`. The safe-id regex must accept `<plugin>:<agent>`.
- **`spawnDepth` is 1 for all 137** and `isFork:true` for 8 → depth-based nesting and forks exist in
  practice; a forked run still carries its own sidecar with an `agentType` (`fork` or its base type).
- Consequence: the sidecar adapter is now **evidence-backed for the tested layout** (one sidecar per
  subagent run, `agentType` always present). Coverage caveats that remain: sidecar durability across
  `--resume` copies and backlog drains is not proven by the corpus; those runs still fall back to the
  binding / `unknown`.

## Decisions (user, 2026-10-03)

Recorded from the CP1 report's D5 open decisions; the user answered all seven.

1. **Agent identity source — verify the sidecar schema locally, then gate the sidecar adapter.**
   Decision: verify the sidecar schema (done above, metadata-only). Use the run-local resolver with
   precedence: stored binding → matching SubagentStop metadata → **verified sidecar `agentType`** →
   `unknown`. Do not claim a named label for every run; `unknown` is an honest value.
2. **Custom-name privacy — default deny, opt-in exact allowlist, pseudonym on rejection.**
   Producer `agent_name_mode = "deny"` and `agent_name_allowlist = []`; backend
   `TOKEN_INSPECTOR_AGENT_NAME_MODE` / `TOKEN_INSPECTOR_AGENT_NAME_ALLOWLIST`, same defaults.
   A custom name passes only when mode is `allowlist` **and** it matches the safe-id shape **and**
   it is an exact allowlist member **and** it is not reserved **and** it is not a detected local
   identifier. Otherwise emit a **stable pseudonym `a-<hash>`** (user chose the pseudonym option over
   the plain literal `custom`), except when the value is not a custom name at all (unavailable →
   `unknown`). No slugification, no basename extraction, no substring salvage. Never echo a rejected
   value in errors, logs, counters or state.
3. **Complexity — `cc-input-size-v1`, provisional thresholds.** Tier 1–5 from
   `I = input_tokens + cache_read_input_tokens + cache_creation_input_tokens`:
   `<4 000 → 1`, `4 000–15 999 → 2`, `16 000–63 999 → 3`, `64 000–127 999 → 4`, `≥128 000 → 5`.
   A separate method name from Hermes `request-shape-v1`; **not** cross-producer comparable. Output
   usage, tool args, text, price and elapsed time do not enter the score. Use the group's final usage,
   keep synthetic exclusion, keep zero-missing-cache semantics, and leave complexity unscored (not
   tier 1) when required input usage is malformed.
4. **Storage — schema v13 event column + derived task fields.** Nullable `token_events.agent`
   (max 64) + index `ix_token_events_call_time_agent`; `rollback_v13.sql` and `rollback_schema.py`
   `STEPS[13]`; conservative `export_state.revision` bump in the migration. No `tasks.agent` column:
   derive the task's agent from its counted events (one value → that value; several → `null` +
   `agent_conflict: true`; none → unavailable + `agent_unavailable_calls`).
5. **Historical coverage — deploy-onward, no backfill.** New fields attach to calls first inserted by
   the upgraded producer/backend (including uninserted backlog). No replay enrichment (duplicate
   `cc-` ids do not rerun task derivation). No transcript reconstruction. Any metadata-only historical
   operation is a separate, explicitly approved backend job.
6. **Export — additive v1.** Events gain `agent` and the extended `complexity_method` set; tasks gain
   `agent`, `agent_conflict`, `agent_unavailable_calls`, `start_complexity`, `start_complexity_method`.
   Keep the documented "adding a field is not a breaking change" rule. If a required strict consumer
   cannot tolerate the new method value, switch to v2 rather than silently break it.
7. **Aggregation — event-time cells, distinct contributing tasks.** Time basis
   `COALESCE(occurred_at, recorded_at)`, UTC day; cell key runtime × agent × method × tier × model × day.
   `tasks` per cell = distinct contributing tasks; the top-level `tasks` is a global distinct count,
   never the sum of cells. Unpriced cost is `null`, never zero; latency is `null` with a denominator.

## CP2 — Integration (commit `1d6bcbd`)

One hermes code job (hermes default model, `hermes.sh send`, no model/provider flag) produced the data
layer; a second delta job fixed two plan deviations found in the applied diff (A4 step 7 state-load
policy re-application; sidecar real layout `session/subagents/agent-<id>.meta.json` with a bounded
≤8-level walk). The delta diff was pulled over ssh, applied here, and verified locally.

Delivered: producer sanitized agent label per run + `cc-input-size-v1` tier; schema v13
`token_events.agent` + index + `rollback_v13.sql` + `rollback_schema.STEPS[13]`; fail-open ingest
validation; task `agent`/`agent_conflict`/`agent_unavailable_calls` and `start_complexity_method`
derived from counted calls; `GET /api/analytics/agents`; export v1 additive fields; ADR-006.

## Verification Log

Windows worktree, main checkout's `.venv`:

| Check | Command | Result |
|---|---|---|
| Targeted delta tests | `.venv/Scripts/python.exe -m pytest -q` (delta files) | 64 passed |
| Full backend suite | `.venv/Scripts/python.exe -m pytest -q` | 591 passed, 1 failed, 17 skipped |
| Browser suite | `.venv/Scripts/python.exe -m pytest -q -m browser` | 41 passed, 551 deselected |
| Syntax | `node --check static/app.js`; `py_compile` (app, routes, scripts, producer) | OK |
| Whitespace | `git diff --check` | clean |

The one failure is pre-existing and environmental: `tests/test_cc_attribution.py::test_attribution_vectors_identical`
compares the CRLF Windows fixture against the LF hermes fixture; it passes on Linux/hermes and was not
introduced by this work. No new control is left without a mutation check (next section).

## Mutation checks (goal 1 controls)

Status: **not yet executed as a separate run.** The user folded this obligation into goal 2's CP2
(the merged goal-2 prompt's per-control mutation step), so it is now a goal-2 obligation rather than
a goal-1 job. The control list below is the target; each row names the test that must go red when the
control is broken, then be restored. Runner requirements to honor: `if __name__ == "__main__":` guard;
subprocess `subprocess.run(..., encoding="utf-8", errors="replace")`; one mutation at a time; original
bytes restored in `finally`; every mutation's original string verified present before commit.
Mutation control list source: `hermes/cp2-mutation-prompt.txt`.

| Control | Mutated to | Test that must catch it |
|---|---|---|
| agent label precedence (binding → hook → sidecar → unknown) | drop sidecar step | `test_cc_agent` / hook precedence test |
| custom-name default-deny | allow custom without allowlist | `test_cc_privacy` |
| pseudonym passthrough | re-hash an `a-<hash>` value | `test_cc_agent` (pseudonym passthrough param) |
| state-load policy re-application | skip re-apply on load | `test_cc_state_client.test_state_load_reapplies_revoked_agent_name_policy` |
| sidecar bounded walk + shape | accept a non-`subagents` dir | `test_cc_agent` (nested/rejection cases) |
| `cc-input-size-v1` tier boundaries | shift a threshold | `test_cc_complexity` |
| malformed usage → unscored | coerce to tier 1 | `test_cc_complexity` |
| ingest agent fail-open validation | reject on bad agent | `test_agent_ingest` |
| task agent conflict / unavailable | pool into one value | `test_task_ingest` |
| endpoint unpriced-never-zero | return `0.0` for unpriced | `test_agents_analytics` |
| endpoint task count (cell vs global) | sum cells into global | `test_agents_analytics` |
| evaluator/invalid-attribution exclusion | include them | `test_agents_analytics` |
| export additive fields + method set | omit `agent` | `test_export_contract` |
| migration v13 + rollback | mis-index rollback step | `test_migration_v13` |

## Contract for goal 2

Goal 2 (Agents tab) may use ONLY the fields, value sets and endpoints below. Anything missing is a
backend job, not a frontend invention.

### Endpoint — `GET /api/analytics/agents`

Query parameters (all optional except the range):

| Param | Type | Notes |
|---|---|---|
| `from`, `to` | ISO datetime | required; `to-from ≤ 92 days`; else `400 invalid_filter` |
| `project` | string | `^[a-z0-9][a-z0-9._-]{0,63}$` |
| `runtime` | repeated | from `RUNTIMES` |
| `agent` | repeated | must round-trip `_clean_agent` |
| `agent_missing` | bool | `agent IS NULL`; mutually exclusive with `agent` |
| `method` | repeated | from `("request-shape-v1","cc-input-size-v1")` |
| `complexity` | repeated int | 1–5 |
| `include_unscored` | bool (default true) | false → `complexity IS NOT NULL` |
| `model` | repeated | |

Response shape (no real data):

```json
{
  "contract_version": 1,
  "period": {"from": "...", "to": "..."},
  "time_basis": "COALESCE(occurred_at, recorded_at), UTC calendar day",
  "tier_source": "llm_call",
  "task_count_semantics": "global distinct project/session/turn; cells count distinct contributors",
  "methods": ["cc-input-size-v1", "request-shape-v1"],
  "coverage": {
    "agent_unavailable_calls": 0, "agent_unknown_calls": 0,
    "agent_custom_calls": 0, "complexity_unscored_calls": 0
  },
  "totals": { "calls": 0, "tasks": 0, "calls_without_task": 0,
    "prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
    "priced_calls": 0, "unpriced_calls": 0, "estimated_calls": 0,
    "priced_cost_usd": null, "estimated_cost_usd": 0.0,
    "cost_complete": false, "latency_calls": 0, "avg_process_time_ms": null,
    "ttft_calls": 0, "avg_ttft_ms": null },
  "cells": [
    { "runtime": "claude-code@windows", "agent": "main", "complexity_method": "cc-input-size-v1",
      "complexity": 3, "model": "claude-...", "day": "2026-10-03",
      "calls": 0, "tasks": 0, "calls_without_task": 0,
      "prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
      "priced_calls": 0, "unpriced_calls": 0, "estimated_calls": 0,
      "priced_cost_usd": null, "estimated_cost_usd": 0.0,
      "cost_complete": false, "latency_calls": 0, "avg_process_time_ms": null,
      "ttft_calls": 0, "avg_ttft_ms": null }
  ],
  "complete": true
}
```

Notes for the tab:
- `priced_cost_usd` is `null` when no priced call contributes — **never `$0`** for unpriced data.
  Pair it with `unpriced_calls` (`unpriced_calls > 0` → the cost is incomplete).
- `tasks` in a cell = distinct contributing tasks; `totals.tasks` = global distinct count, **not**
  the sum of cells.
- `cost_complete` = every contributing call was priced.
- Errors: `400 invalid_filter`; `413 result_too_large` (static, never truncated — >20 000 cells).

### Fields and value sets

- `token_events.agent` (nullable, ≤64 chars): `main` | verified built-in | exact allowlisted custom
  (safe-id shape, not a local identifier) | stable `a-<sha256[:16]>` pseudonym | `unknown`. Null =
  unavailable. Framed as a nullable column, never a tag.
- `complexity_method`: `"cc-input-size-v1"` (Claude Code) or `"request-shape-v1"` (Hermes); the two
  are **not tier-comparable**. Task-level `start_complexity_method` carries the earliest eligible
  counted call's actual method.
- Task fields: `agent` (one counted value, else null), `agent_conflict` (≥2 distinct), 
  `agent_unavailable_calls` (counted calls with null agent), `start_complexity`, `start_complexity_method`.
- Tier thresholds (`cc-input-size-v1`, `I = input + cache_read + cache_creation`):
  `<4 000 → 1`, `4 000–15 999 → 2`, `16 000–63 999 → 3`, `64 000–127 999 → 4`, `≥128 000 → 5`;
  malformed usage → unscored (`complexity IS NULL`), never tier 1.
- Export v1 additive: events gain `agent`; tasks gain `agent`, `agent_conflict`,
  `agent_unavailable_calls`, `start_complexity`, `start_complexity_method`; `COMPLEXITY_METHODS`
  gains `cc-input-size-v1`.

### Coverage note to surface in the tab

Agent and CC complexity exist only from the goal-1 deploy onward; older events are **"no label"**
(null agent, null method). Hermes-agent rows carry no agent label (plugin contract) and short
`hermes -z` jobs can lose their last ~2 s of events (exit window). A named label is not promised for
every run — `unknown` is honest.

## Decisions, goal 2

Recorded 2026-10-03 after the CP1 report and user decision round:

1. **CP2 baseline:** use current `feat/cc-agent-usage` HEAD with goal-1 implementation. CP1's pinned-commit audit is historical evidence only; it does not describe current HEAD.
2. **Missing metrics:** implement a separately scoped and tested backend aggregation before claiming full requested coverage. Do not substitute role, task, or unrelated analytics data.
3. **Per-agent task counts:** add a dedicated exact distinct-task aggregation so the sortable table can show task totals safely.
4. **Low sample:** mark fewer than 5 calls; label the threshold as calls.
5. **Jobs handoff:** runtime-only with an explicit warning; do not imply agent, project, model, method, or exact-period filtering.
6. **Agent labels:** display exact labels neutrally; render null as “No label”, `unknown` distinctly, and `a-<hash>` as a pseudonym. Do not infer built-in/custom/subagent classification or structural share without authoritative metadata.

CP2 must first reconcile the CP1 evidence against the current goal-1 HEAD, then update the implementation plan and contract for the separately approved backend aggregation. No code was built before the decision round.

## Deploy (later goal-3, not this goal)

Nothing is deployed here. The later deploy needs: apply migration v13 (backend restart with the
deployed branch head — additive, `export_state.revision` bumped in the same transaction); install the
updated producer files (`producers/claude_code/`, hook on Windows and hermes); plugin unchanged.
`rollback_v13.sql` / `rollback_schema.py STEPS[13]` exist for reversibility. No capture/JEV flag,
no `INGEST_TOKEN`, no allowlist change.

## Known Limitations

- Agent label only from deploy onward; no backfill of historical CC calls (their `cc-` ids are
  idempotent and cannot be re-sent). Any metadata-only historical operation is a separate approved job.
- Sidecar durability across `--resume` copies and backlog drains is unproven; those runs fall back to
  the stored binding or `unknown`.
- The built-in agent-type closed list is not evidenced; unknown built-ins are treated as custom.
- `cc-input-size-v1` thresholds are provisional (calibrate from a metadata-only effective-input
  histogram before freezing v1).
- Hermes plugin has no equivalent agent source; its `child_role` is a possible future source.

## Open Items

- O-CC1: ~~CP2 integration~~ done (`1d6bcbd`); delta fix applied.
- O-CC2: The built-in agent-type closed list is still not evidenced; the plan says do not invent one.
  Treat unknown built-ins as custom until the registry is verified (allowlist decision applies).
- O-CC3: Complexity thresholds are provisional; calibrate from a metadata-only effective-input
  histogram before freezing v1 (a later, separately approved step).
- O-CC4: Sidecar durability across `--resume` copies and backlog drains is unproven; those runs fall
  back to binding / `unknown`.
- O-CC5: Pushes are pending — classifier timeouts blocked `git push` to `hermes` and `origin` at
  checkpoint time; retry until both are updated.
