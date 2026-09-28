# Architecture

## System Boundary

Token Inspector is a standalone localhost service. It does not own provider calls and is not embedded in Hermes core. Producers emit structured telemetry after lifecycle events; producer failures and exporter failures must not block the originating workload.

```text
Producer lifecycle
  → bounded privacy-safe event mapping
  → short-timeout batch HTTP
  → FastAPI ingest boundary
  → SQLite transaction/pricing
  → analytics endpoints
  → local dashboard
```

The Hermes producer lives in the separate `token_inspector` repository and registers lifecycle hooks through the supported plugin registry.

A second producer (O9, on the feature branch, not installed yet) is the Claude Code hook in `producers/claude_code/`. It runs as a stdlib-only script on Claude Code's `Stop`, `SubagentStop` and `SessionEnd` hooks, reads only the new part of the session transcript, and posts one `llm_request` per API call to the same batch endpoint:

```text
Claude Code hook (stdin: transcript location + cwd only)
  → transcript reader (usage records, streaming group finality, subagent files)
  → allowlisted mapping (pseudonymized ids, cc- client_event_id, runtime/producer/job tags)
  → cursor state per transcript (lock, checkpoint after each acknowledged batch)
  → POST /api/events/batch (2 s timeout, 5 s bound per hook, fail-open)
```

Each runtime has exactly one counting producer (ADR-004): `hermes-agent` → Hermes plugin, `claude-code@windows` / `claude-code@hermes` → Claude Code hook. The backend enforces the pairing.

## Trust Boundaries

### Producer

Trusted to report structured counts and correlation identifiers. It must not send raw prompts, responses, tool arguments, absolute paths, full Git remotes, or credentials by default.

### Ingest API

Validates bounded event batches, normalizes fields, optionally authenticates ingest, resolves pricing, and performs idempotent writes. Invalid events are rejected without compromising accepted events or caller availability.

### Database

SQLite is local durable state. Production storage is outside the repository. WAL and a partial unique index support concurrent idempotent ingest. Migrations are additive and backed up before changes.

### Dashboard

The dashboard is a read-oriented localhost UI. It distinguishes filesystem inventory from telemetry activity and never treats an inventory-only repository as token usage.

## Event Identity

```text
(project_name, client_event_id)
```

is the idempotency boundary when `client_event_id` exists. Session/turn/trace/span fields are correlation dimensions, not uniqueness guarantees.

Exception (v11): `cc-` ids, reserved for the Claude Code producer and derived from the provider's message and request ids, are unique across projects, so a resumed copy that resolves to another project counts once, in the project of first arrival (ADR-004).

## Job Correlation (schema v11)

```text
hermes.sh send / devir-baslat → exports TOKEN_INSPECTOR_JOB_REF / _WORK_TYPE / _JOB_ATTEMPT
  → producer process (hermes -z plugin, or claude -p hook) reads them once, keeps valid shapes only
  → event tags job_ref, work_type, job_attempt + runtime, producer
  → ingest: _normalize_reserved (never rejects; invalid pair → attribution_invalid)
  → task upsert: tasks.job_ref = first valid job; later different job → job_ref_conflicts + 1
  → GET /api/jobs: aggregate per job (job of an event = its task's job, else its own tag) → Jobs view
```

A job is an aggregate, not a table, and a task belongs to at most one job. Counted events are `llm_request` only, excluding evaluator usage and `attribution_invalid` events; cost is priced calls only (ADR-003).

A cross-project subagent names its parent's project (`parent_project_name`, validated, never stored), so the parent task ref is hashed with the right project; `scripts/repair_task_parents.py` re-links rows written before that fix.

## Model and Pricing Identity

Requested, resolved, aliased, and pricing model values remain separate. Pricing is calculated at ingest and carries status/reason/version metadata. Missing rules yield `unpriced` and null cost.

## Token Dimensions

Prompt, completion, cache read, cache creation, and reasoning counts are stored independently. Provider semantics determine whether cache/reasoning are already included in broader input/output totals; pricing code must avoid double counting.

## Complexity

`request-shape-v1` assigns a deterministic 1–5 tier from numeric request-shape metadata. It is reproducible and privacy-safe but is not a semantic difficulty classifier. Post-response execution intensity belongs to a separate future metric.

A legacy AI scorer remains opt-in for non-Hermes sources that explicitly submit capped prompt text.

## Project Identity

Two datasets exist:

1. **Filesystem inventory:** bounded direct-child Git discovery from configured roots. Canonical name prefers sanitized `origin` repository slug, otherwise root directory name. Path identity is represented by a short hash.
2. **Observed activity:** SQL aggregation over actual LLM events by producer-supplied `project_name`.

Hermes producer attribution order:

```text
hook metadata
→ optional explicit marker
→ configured alias
→ Git origin slug
→ Git root name
→ fallback
```

Explicit prompt markers are disabled by default to prevent examples or ordinary text from creating phantom projects.

## Tasks, Retention and the Evaluator

```text
Hermes turn (plugin 0c: task_hierarchy, parent session/turn, composition counts, scrubbed prompt when ready)
  → ingest: event insert + task upsert in one transaction (task = project + session + turn)
  → tasks / task_evaluations (schema v10)
  → retention: purge at startup and in a loop (DB, WAL, DB-directory backups)
  → POST /api/tasks/evaluate → JEV worker → Vercel AI Gateway (typesafe-ai, digitalocean on 429)
  → Tasks view and the pilot CLI (HTTP only)
```

- Task prompts are stored only under ADR-002's gate and are scrubbed twice (plugin before the queue, backend before storage).
- A fourth gate limits prompts and JEV to allowlisted projects and proven root tasks: the plugin checks its allowlist, the `-devir` path deny and root lineage before queue/spool; the backend checks `TASK_PROMPT_ALLOWED_PROJECTS`, the eligibility marker and the proven-root rule at ingest; the JEV worker re-checks before every provider attempt (ADR-002 §6).
- The evaluator is a separate trust boundary: the scrubbed prompt leaves the machine; every call is reserved in `evaluator_attempts` first; its own usage is a `token-inspector` event that is never scored.
- The plugin spools undeliverable batches to disk (fsync, dead-letter) and never holds a prompt past capture time + 30 days.

## Runtime State

```text
Backend unit: token-inspector.service
Bind: 127.0.0.1:8100
Production DB: user-local application data directory
Plugin: token_inspector, independently installed and gateway-loaded
Claude Code hook: global ~/.claude/settings.json entries via producers/claude_code/install.py (not installed yet)
Claude Code hook state: per-user local state dir (cursor per transcript, counters.json with integers only)
```

Remote exposure is not part of the default architecture. If enabled, it must remain private-network-only; public Funnel-style exposure is rejected.

## Failure Behavior

- Backend down: producer spools the batch durably and replays it later; caller proceeds. Only the in-memory queue (≤ 2,048 events) and the in-flight batch can be lost on a crash.
- Backend down for the Claude Code hook: the hook exits 0 within its bound; the cursor does not advance, so the next hook resends (at-least-once, idempotent by `cc-` id).
- Duplicate event: database conflict path returns deduplicated result.
- Invalid job tags or runtime/producer pair: normalized or marked `attribution_invalid`, never rejected.
- Unknown model price: event persists as unpriced.
- Invalid event: rejected and counted; no fabricated fallback values.
- NotebookLM unavailable: local docs/Vault succeed and external sync is queued.

## Rejected Alternatives

- Patching Hermes core or every individual tool: excessive coupling and upgrade risk.
- Storing raw prompts by default: unnecessary privacy exposure (task prompts are a gated, scrubbed, 30-day exception; ADR-002).
- A model-traffic proxy for task telemetry: risky for OAuth-based agents and blind to task boundaries.
- Treating unknown prices as zero: falsely reports free usage.
- Deriving project identity only from folder name: diverges from canonical repository identity.
- Mixing repository inventory with event aggregation: implies activity where none occurred.
- Unbounded recursive home-directory discovery: privacy and performance risk.
- A jobs table or job-level launcher event: a job is an aggregate of tagged events; there is no job duration (ADR-003).
- Building Claude Code events from hook stdin: only transcript usage records count, so re-runs, streaming lines and resumed copies count once (ADR-004).
