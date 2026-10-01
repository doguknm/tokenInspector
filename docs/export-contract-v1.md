# Token Inspector export contract v1

**Contract version:** `schema_version: 1` (this document)
**Status:** implemented on branch `feat/task-telemetry-jev-pilot` (O9 Phase 3); not deployed yet
**Design record:** [ADR-005](adr/005-export-contract.md)

This contract is versioned **separately** from the Token Inspector application version and from the
database schema version (`/api/meta` `schema_version`, currently 12). The export `schema_version`
changes **only on a breaking change** (a removed or renamed field, a changed type or meaning).
Adding a field is not a breaking change: **consumers must ignore unknown fields.**

JSON is for machines; CSV is for people. The dashboard (Jobs view → Export (v1)) builds a CSV in
the browser from these JSON pages with the same field set and order. There is no server-side CSV.

## Endpoints

| Method | Path | Dataset |
|---|---|---|
| GET | `/api/export/v1/jobs` | one item per launcher job (`job_ref`) |
| GET | `/api/export/v1/tasks` | one item per task (one Hermes turn / Claude Code prompt) |
| GET | `/api/export/v1/events` | one item per counted LLM call |

Read-only: no endpoint writes anything. No authentication is required, also when `INGEST_TOKEN` is
set (the Host allowlist `TOKEN_INSPECTOR_ALLOWED_HOSTS` still applies). `POST`, `PUT`, `PATCH` and
`DELETE` return 405. These endpoints are separate from `GET /api/events` (which is not part of this
contract).

## Parameters

| Param | Rule |
|---|---|
| `from` | required; ISO-8601 with `Z` or an offset (e.g. `2026-09-01T00:00:00Z`, `2026-09-01T03:00:00+03:00`); normalized to UTC; **inclusive** |
| `to` | required; same format; **exclusive**; `from < to`; the span `to - from` is at most **92 days** |
| `limit` | optional; integer 1–**1000**; default 500 |
| `cursor` | optional; the opaque `next_cursor` of the previous page, unchanged |

Times are compared on each call's own time: `COALESCE(occurred_at, recorded_at)`.

## Envelope

```json
{
  "schema_version": 1,
  "dataset": "events",
  "generated_at": "2026-09-28T15:00:00.000000Z",
  "period": {"from": "2026-09-01T00:00:00.000000Z", "to": "2026-09-28T00:00:00.000000Z"},
  "fields": ["event_id", "..."],
  "staleness": {"hermes-agent": "2026-09-28T14:59:10.000000Z", "claude-code@windows": null,
                "claude-code@hermes": null, "app": null},
  "coverage": {"measured": ["hermes-agent"], "not_measured": ["claude-code@windows", "claude-code@hermes", "app"]},
  "items": [],
  "next_cursor": null,
  "complete": true
}
```

| Key | Meaning |
|---|---|
| `schema_version` | export contract version (1) |
| `dataset` | `jobs`, `tasks` or `events` |
| `generated_at` | UTC time the page was built |
| `period` | the normalized `from` (inclusive) and `to` (exclusive) |
| `fields` | the dataset's allowed fields in column order (the CSV header); an item never has another key |
| `staleness` | per runtime: the latest `recorded_at` (ingest time) of any snapshot event of that runtime, over **all time** (not limited to the period); `null` when there is none. Evaluator usage and invalid-attribution events are excluded |
| `coverage` | `measured`: runtimes with at least one counted call in the period; `not_measured`: the other runtimes. **Not measured is never zero usage** |
| `items` | the page's items |
| `next_cursor` | the cursor for the next page, or `null` on the last page |
| `complete` | `false` while more pages remain (partial result); `true` on the last page |

Runtimes: `hermes-agent`, `claude-code@windows`, `claude-code@hermes`, `app` (no `app` producer
exists yet). Today only `hermes-agent` (Hermes plugin) and the Claude Code runtimes (hook producer,
once installed) can be measured.

## Pagination and snapshot

- Keyset pagination with a stable order: events by `ingest_seq` (internal, not exported), tasks by
  `task_ref`, jobs by `job_ref`. Follow `next_cursor` until `complete: true`. Concatenated pages
  equal the full result: no duplicates and no gaps, also while new events are being ingested.
- **Snapshot.** The first page fixes a snapshot high-water mark (`as_of`); every later page of that
  cursor chain reads only events ingested up to it. Membership and sums — including the job a task
  belongs to — are derived only from events inside the snapshot. Calls ingested later (also late
  calls with an old `occurred_at`) appear in the next export, never in an open cursor chain.
- **Cursor lifetime rule.** A cursor is valid until `snapshot_expired`: it is bound to its dataset,
  `from` and `to` (another dataset or range → `invalid_cursor`), and it expires when already
  snapshotted data was changed — a pricing recost that changed costs, a parent repair that changed
  tasks, or a schema rollback and re-upgrade. Then restart the export from page 1. New ingest never
  expires a cursor.
- **Live attributes** are read at page time and can differ between pages: on tasks
  `hierarchy_status`, `parent_task_ref`, `root_task_ref`, `completion`, `completed_at`, `updated_at`;
  on events `complexity`, `complexity_method`.

## Period semantics: events by time, tasks and jobs as a start-time cohort

- **events**: counted calls whose own time is in `[from, to)`.
- **tasks**: a **start-time cohort** — tasks whose first snapshot call is in `[from, to)`. Their sums
  cover **all** of the task's snapshot calls, including calls at or after `to`.
- **jobs**: a start-time cohort — jobs whose first snapshot call is in `[from, to)`; sums over all of
  the job's snapshot calls, including calls at or after `to`.
- **Reconciliation limits.** A job or task that started before `from` is not in the jobs/tasks
  datasets of that period, although its in-period calls are in the events dataset. The sum of job or
  task costs for a period therefore need not equal the sum of event costs for the same period.
  For period-exact money, sum the events dataset.

## Counted calls

An event is counted (and exported) when it is an LLM call (`event_type = llm_request`), is not
evaluator usage (JEV: project `token-inspector` with role `evaluator`), and is not marked
`attribution_invalid` (an invalid runtime/producer pair, ADR-004). Marked events are in no dataset
and in no staleness or coverage value.

**Legacy runtime inference.** An event with no `runtime`, no `producer` and no mark predates the job
tags; it is exported with `runtime: "hermes-agent"` and `runtime_inferred: true`, and has no
`producer` key. Tagged events have `runtime_inferred: false`.

## Zero, unknown and absent

- `0` = measured zero.
- `null` = measured but unknown (for example `cost_usd` of an unpriced call, `ttft_ms` the producer
  did not report, `model` that fails its value rule).
- **key absent** = not measured or not applicable (in CSV: an empty cell).

They are never collapsed into each other. Units: token counts are integers; costs are USD floats
(list-price estimates, not a bill) and always come with `cost_status`; times are UTC ISO-8601 with `Z`.

## Fields: events

| Field | Type | Unit / meaning | Absent / null | Value rule |
|---|---|---|---|---|
| `event_id` | string | stable id of the stored call | never absent | safe id or `p-` pseudonym |
| `client_event_id` | string | producer's idempotency id | `null` when the producer sent none | safe id or `p-` pseudonym |
| `project_name` | string | lowercase TI project name | never absent | stored name `^[a-z0-9][a-z0-9._-]{0,63}$` |
| `session_id` | string | producer session id | `null` when none | safe id or `p-` pseudonym |
| `task_ref` | string | 32-hex task id | **absent** when the call is not a task turn | 32 hex |
| `runtime` | string | runtime of the call | never absent | closed list (legacy → `hermes-agent`) |
| `runtime_inferred` | boolean | `true` for legacy (untagged) calls | never absent | — |
| `producer` | string | producer of the call | **absent** for legacy calls | `hermes-plugin`, `claude-code-hook`, `app-provider` |
| `job_ref` | string | the call's **canonical** job: its task's snapshot job, else its own tag | **absent** when none | `^(devir-)?[0-9]{8}-[0-9]{6}-[0-9]{1,10}$` |
| `job_ref_conflict` | boolean | `true` when the call's own job tag differs from its canonical job (the differing value is not exported) | never absent | — |
| `work_type` | string | launcher work type | **absent** when none | `brainstorm`, `review`, `code`, `devir`, `k1`, `k2`, `other` |
| `job_attempt` | integer | launcher attempt (≥ 1); unrelated to `attempt` | **absent** when none | integer ≥ 1 |
| `occurred_at` | string | call time (UTC) | `null` only for very old rows | ISO-8601 `Z` |
| `recorded_at` | string | ingest time (UTC) | never absent | ISO-8601 `Z` |
| `provider` | string | provider name | `null` when none | safe id or `p-` pseudonym |
| `model` | string | resolved model | `null` when unknown or failing the rule | model rule |
| `pricing_model` | string | model used for pricing | `null` when unknown or failing the rule | model rule |
| `status` | string | call outcome | never absent | `success`, `error`, `timeout`, `cancelled`, else `other` |
| `error_type` | string | error class | `null` when no error class | closed class rule |
| `http_status` | integer | HTTP status | `null` when not reported | — |
| `prompt_tokens` | integer | input tokens excluding cache | never absent | — |
| `completion_tokens` | integer | output tokens | never absent | — |
| `cache_read_tokens` | integer | cache-read tokens | never absent | — |
| `cache_creation_tokens` | integer | cache-write tokens | never absent | — |
| `reasoning_tokens` | integer | reasoning tokens | **absent** for `producer == claude-code-hook` (not reported by transcripts) | — |
| `cost_status` | string | `priced`, `unpriced`, `partial`, `estimated`, `legacy` | never absent | closed list |
| `cost_usd` | number | USD cost of a priced call | `null` unless `cost_status == priced` | — |
| `estimated_cost_usd` | number | USD estimate | `null` unless `partial`, `estimated` or `legacy` | — |
| `process_time_ms` | integer | ms | `null` when not reported | — |
| `ttft_ms` | integer | ms to first token | `null` when not reported; **absent** for `claude-code-hook` | — |
| `attempt` | integer | per-call retry attempt | never absent | — |
| `retry_count` | integer | retries | never absent | — |
| `tool_call_count` | integer | tool calls requested by the response | `null` when not reported | — |
| `role` | string | `primary`, `subagent` (else `other`) | `null` when none | closed list |
| `complexity` | integer | deterministic tier 1–5 | `null` when not scored | — |
| `complexity_method` | string | `request-shape-v1` | **absent** for any other or no method | closed list |

## Fields: tasks

| Field | Type | Unit / meaning | Absent / null | Value rule |
|---|---|---|---|---|
| `task_ref` | string | stable 32-hex task id | never absent | 32 hex |
| `project_name` | string | TI project name | never absent | stored name |
| `session_id` | string | producer session id | `null` when none | safe id or `p-` pseudonym |
| `runtimes` | array of string | sorted distinct runtimes of the task's counted snapshot calls | never absent | closed list |
| `runtime_inferred` | boolean | `true` when any contributing runtime was inferred (legacy) | never absent | — |
| `job_ref` | string | the task's snapshot job (first job tag in ingest order) | **absent** when none | job ref rule |
| `work_type` | string | the single work type | `null` when several; **absent** when none | closed list |
| `work_types` | array of string | sorted distinct work types | never absent (may be empty) | closed list |
| `hierarchy_status` | string | `root`, `child`, `unknown` (live) | never absent | closed list |
| `parent_task_ref` | string | parent task (live) | `null` when none | 32 hex |
| `root_task_ref` | string | root task (live) | `null` when none | 32 hex |
| `first_event_at` | string | first snapshot call time | never absent | ISO-8601 `Z` |
| `last_event_at` | string | last snapshot call time | never absent | ISO-8601 `Z` |
| `wall_time_ms` | integer | `last_event_at - first_event_at` (not end-to-end task time) | never absent | — |
| `completion` | string | how the task ended (live; not success) | never absent | server-derived |
| `completed_at` | string | completion time (live) | `null` while open | ISO-8601 `Z` |
| `llm_request_count` | integer | counted calls | never absent | — |
| `prompt_tokens`, `completion_tokens`, `cache_read_tokens`, `cache_creation_tokens` | integer | token sums | never absent | — |
| `priced_count` | integer | priced calls | never absent | — |
| `unpriced_count` | integer | unpriced calls | never absent | — |
| `cost_usd` | number | sum of priced calls | `null` when no call is priced (never zero) | — |
| `estimated_cost_usd` | number | sum of partial/estimated/legacy calls | never absent | — |
| `cost_complete` | boolean | `true` only when every call is priced | never absent | — |
| `conflict_count` | integer | calls whose own job tag differs from the task's job | never absent | — |
| `updated_at` | string | last change of the task row (live; **informational**) | never absent | ISO-8601 `Z` |

## Fields: jobs

| Field | Type | Unit / meaning | Absent / null | Value rule |
|---|---|---|---|---|
| `job_ref` | string | launcher job id | never absent | job ref rule |
| `runtimes` | array of string | sorted distinct runtimes | never absent | closed list |
| `runtime_inferred` | boolean | `true` when any contributing runtime was inferred | never absent | — |
| `work_type` | string | the single work type | `null` when several; **absent** when none | closed list |
| `work_types` | array of string | sorted distinct work types | never absent | closed list |
| `attempts` | array of integer | distinct launcher attempts | never absent (may be empty) | integers ≥ 1 |
| `projects` | array of string | distinct project names | never absent | stored names |
| `task_count` | integer | tasks of the job | never absent | — |
| `llm_request_count` | integer | counted calls | never absent | — |
| `prompt_tokens`, `completion_tokens`, `cache_read_tokens`, `cache_creation_tokens` | integer | token sums | never absent | — |
| `priced_count`, `unpriced_count` | integer | priced / unpriced calls | never absent | — |
| `cost_usd` | number | sum of priced calls | `null` when no call is priced (never zero) | — |
| `estimated_cost_usd` | number | sum of partial/estimated/legacy calls | never absent | — |
| `cost_complete` | boolean | `true` only when every call is priced | never absent | — |
| `first_event_at`, `last_event_at` | string | first / last snapshot call time (not job duration) | never absent | ISO-8601 `Z` |
| `conflict_count` | integer | the job's calls whose own job tag differs from their canonical job | never absent | — |
| `conflict_task_count` | integer | tasks with at least one such call | never absent | — |
| `updated_at` | string | max `recorded_at` of the job's snapshot calls (**informational**) | never absent | ISO-8601 `Z` |

The job of a call is its task's snapshot job when the call belongs to a task, else its own job tag.
A task belongs to at most one job; a later conflicting job tag is counted in `conflict_count` and
never moves the task.

## Tags

Only these tag keys surface, as top-level fields: `runtime`, `producer`, `job_ref`, `work_type`,
`job_attempt`. The full `tags` object is never exported.

## Excluded everywhere

Prompt text of any kind (raw prompts, task prompts), label notes, legacy `error_message`, tool
arguments and tool names, `prompt_hash`, `prompt_length`, `user_id_hash`, full `tags`,
`request_tool_names`, trace and span ids, file paths, hostnames, JEV scores.

## Value rules and the privacy residual

A key allowlist is not enough, so every exported string also passes a value rule:

- **Safe id** (`event_id`, `client_event_id`, `session_id`, `provider`): exported as-is when it
  matches `^[A-Za-z0-9_-]{1,128}$` (no `.`, `/`, `\`, `:`, `@` or whitespace — it cannot be a path,
  URL, host name, IP or free text); otherwise the deterministic pseudonym `"p-" + sha256(value)[:32]`
  (the same input always gives the same pseudonym, so joins and dedup still work).
- **Model rule** (`model`, `pricing_model`): `^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$` without `//`;
  anything else is `null`.
- **Closed error class** (`error_type`): the last dotted component when it matches
  `^[A-Za-z][A-Za-z0-9_]{0,63}$`, else `"other"`. Historical free text never leaves.
- **Closed lists**: `runtime`, `producer`, `work_type`, `status`, `cost_status`, `role`,
  `hierarchy_status`, `complexity_method` as listed in the field tables.
- **Job ref**: `^(devir-)?[0-9]{8}-[0-9]{6}-[0-9]{1,10}$`.
- **Task refs**: 32 lowercase hex characters.
- **Project names**: the lowercase names Token Inspector stores (`X-Project-Name` is lowercased).
  Aliases are mapped by the consumer. Absolute paths and full Git remote URLs never enter storage.
- Claude Code session, turn and parent ids are pseudonymized by the producer before ingest.

**Residual (value privacy).** A single-label string that happens to equal a machine's short host
name — or a host-like model name such as `my-host.internal` — cannot be told apart from an id, a
model or a repository name by its shape. The Claude Code producer rejects project names equal to the
local host name or FQDN or shaped like an IPv4 address; other fields carry this residual.

## Stable ids and the consumer dedup rule

- **Events:** `event_id` is stable. Producer idempotency: `client_event_id` is unique within
  `project_name`; for Claude Code (`cc-` prefix) it is unique **across** projects — a resumed copy
  of the same call is stored once, under the first project that reported it
  ([ADR-004](adr/004-cross-source-dedup-authority.md)). Each exported event is one provider call;
  never add producer hook-level data on top.
- **Tasks:** `task_ref`. **Jobs:** `job_ref`.
- **`updated_at` is informational**, not a change marker: a recost, a parent repair or a re-rooted
  task changes exported values without moving it. Consumers use **full replacement**: re-fetch a
  period and replace the stored copy of that period; never use `updated_at` for incremental change
  detection.

## Errors and limits

| Case | Status | Body (static; the input is never echoed) |
|---|---|---|
| missing or unparsable `from`/`to`, no timezone, `from >= to`, span over 92 days | 400 | `{"error": "invalid_range", "schema_version": 1}` |
| `limit` not an integer in 1–1000 | 400 | `{"error": "invalid_limit", "schema_version": 1}` |
| cursor undecodable or for another dataset or range | 400 | `{"error": "invalid_cursor", "schema_version": 1}` |
| the snapshot changed since page 1 (recost or repair applied, schema re-upgrade) | 409 | `{"error": "snapshot_expired", "schema_version": 1}` — restart from page 1 |
| unknown dataset path | 404 | framework default |
| database busy or locked | 503 with `Retry-After: 5` | `{"error": "busy", "schema_version": 1}` |

Limits: max page size 1000 items (default 500); max range 92 days. The server sets no request
timeout of its own. **Timeouts:** use a client timeout of 30 s; on a timeout or a 503, retry the same
URL (pages are idempotent within a snapshot); a smaller `limit` shortens a page.

## CSV (dashboard download)

The dashboard fetches every page with `limit=1000` and writes `ti-export-v1-<dataset>-<from>-<to>.csv`
(UTF-8 with BOM, CRLF, RFC 4180 quoting). Header = `fields`. Absent key → empty cell; `null` →
`null`; arrays joined with `;`; booleans `true`/`false`. A text cell starting with `=`, `+`, `-`,
`@`, tab or carriage return is prefixed with `'` (formula-injection guard). An export above 100 000
rows is refused ("narrow the range").
