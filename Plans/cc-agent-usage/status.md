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
| CP2 | Integration in the Windows worktree (hermes default model, diff pulled and applied) | pending |
| CP3 | Docs and handover (Contract for goal 2, deploy steps) | pending |

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

## Open Items

- O-CC1: CP2 integration (hermes default model, one code job) — not started; needs a queue row and the
  `gpt-5.6-sol` default with no model/provider flag.
- O-CC2: The built-in agent-type closed list is still not evidenced; the plan says do not invent one.
  Treat unknown built-ins as custom until the registry is verified (allowlist decision applies).
- O-CC3: Complexity thresholds are provisional; calibrate from a metadata-only effective-input
  histogram before freezing v1 (a later, separately approved step).
- O-CC4: Sidecar durability across `--resume` copies and backlog drains is unproven; those runs fall
  back to binding / `unknown`.
