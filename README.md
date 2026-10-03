# Token Inspector

A standalone local service that collects token usage events from any connected project and provides a cross-project analytics dashboard.

## Stack

- **Backend:** FastAPI + SQLite (via aiosqlite) — single process, ~40MB RAM idle
- **Frontend:** Vanilla JS SPA + Chart.js — no build step, no framework
- **Port:** 8100

## Run

Development:

```bash
pip install -r requirements.txt
DB_PATH=/tmp/token-inspector-dev.db STORE_RAW_PROMPTS=0 \
python -m uvicorn main:app --host 127.0.0.1 --port 8100
```

Production on this machine is managed by the `token-inspector.service` systemd user unit and stores SQLite outside the repository. Do not delete a repository-local database expecting to reset production.

Open http://127.0.0.1:8100 on the server itself.

**Remote dashboard (from another tailnet machine):** hermes has no desktop, so the dashboard is served to the tailnet by `tailscale serve`, which proxies `https://<tailnet-host>` (the machine's tailnet HTTPS name) to `127.0.0.1:8100`. The service still binds loopback only. It is reachable from tailnet devices only, never from the internet. The Host and Origin allowlists must include that name. They are set in a drop-in, not in the unit itself; the real name lives only in that drop-in, never in repository files:

```ini
# ~/.config/systemd/user/token-inspector.service.d/remote-access.conf
[Service]
Environment=TOKEN_INSPECTOR_ALLOWED_HOSTS=127.0.0.1,localhost,<tailnet-host>
Environment=TOKEN_INSPECTOR_ALLOWED_ORIGINS=https://<tailnet-host>
```

Apply it with `systemctl --user daemon-reload && systemctl --user restart token-inspector.service`, and check it with `tailscale serve status`. Read views need no token. Sensitive actions (prompt text, labels, evaluate, purge) still require `X-Ingest-Token`.

## Dashboard Views

| View | What it shows |
|---|---|
| Overview | Stat cards (input, cache read, cache write and output tokens shown separately, plus their labelled sum), daily stacked token-type bars with a cost line, top models by cost, per-project table with one column per token type |
| Projects | Separates bounded Git repository inventory (including repositories with zero events) from event-backed observed activity; select either identity for filtered analytics |
| Models | Cross-project latency bar chart, cost comparison table |
| Complexity | Per-call `request-shape-v1` tier (1–5), filterable by project, model, method and 7/30/90 days: a calls/tasks/tokens/cost-per-tier chart split by model, a per-tier volume table (calls, tasks, input / cache read / cache write / output with per-call and per-task averages) and a tier × model table (per-task and per-call tokens, latency, TTFT, error rate, priced cost per task with unpriced calls shown, completion counts, low-sample and lowest-cost/latency marks); routing recommendations table |
| Tasks | One row per task (a Hermes turn): request-shape-v1 start complexity, JEV difficulty and confidence, tokens, cost, tools, wall time, completion and prompt state; a start-complexity vs JEV scatter; a detail panel with probabilities, child tasks and evaluations. Never shows prompt text |
| Jobs | One row per launcher job (`job_ref`), filterable by runtime, work type and 7/30/90 days: work type, runtime, projects, tasks, calls, the four token types and their sum, priced cost with the unpriced count (never `$0`), estimated cost, first/last event (not job duration), attempts and conflicts (calls / tasks); an anomaly line for job conflicts and invalid attribution. Below the table, **Export (v1)** downloads jobs, tasks or LLM calls for a UTC date range as CSV (built in the browser from the JSON export, same fields, formula-injection guarded) and links the JSON first page |
| Settings | Edit/add/delete pricing rules (USD per 1M tokens) |

## Task telemetry and the JEV pilot

Tasks are derived at ingest from each Hermes turn. Task prompt capture and JEV scoring are **off by default** and must not be enabled before the Approval section of [ADR-002](docs/adr/002-task-prompt-retention-and-jev.md) is filled in.

| Variable | Default | Meaning |
|---|---|---|
| `STORE_TASK_PROMPTS` | off | Store one scrubbed prompt per task (needs `INGEST_TOKEN` ≥ 16 chars, a valid purge interval and a non-empty `TASK_PROMPT_ALLOWED_PROJECTS`) |
| `TASK_PROMPT_ALLOWED_PROJECTS` | unset (= empty = off) | Comma-separated exact project names allowed for prompt storage **and** JEV. Entries are lower-cased; an entry that is not a valid project name (`^[a-z0-9][a-z0-9._-]{0,63}$`) is dropped with one startup warning `invalid allowlist entries ignored` |
| `TASK_PROMPT_PURGE_INTERVAL_S` | `21600` | Purge loop target lag, 1..21600; `0` only after a verified all-copy purge |
| `JEV_ENABLED` | off | JEV worker (needs `AI_GATEWAY_API_KEY`, `INGEST_TOKEN`, valid budget) |
| `AI_GATEWAY_API_KEY` | — | From `~/.config/token-inspector/secrets.env`; never logged or returned |
| `JEV_DAILY_BUDGET_USD` / `JEV_MONTHLY_BUDGET_USD` / `JEV_MAX_CALLS_PER_DAY` | `0.05` / `0.50` / `200` | Ceilings, enforced before every call |
| `JEV_MAX_RETRY_WAIT_S` | `60` | Longest Retry-After honoured in a run |
| `TOKEN_INSPECTOR_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Host allowlist |
| `TOKEN_INSPECTOR_ALLOWED_ORIGINS` | loopback origins | Extra origins for sensitive endpoints |
| `REDACT_INTERNAL_HOST_SUFFIXES`, `REDACT_EXTRA_TERMS` | — | Extra redaction for internal domains and names |

The systemd unit reads the key with `EnvironmentFile=%h/.config/token-inspector/secrets.env`, set in the drop-in `~/.config/systemd/user/token-inspector.service.d/secrets.conf` (in production since 2026-09-28). `GET /api/meta` shows both gates and a configuration preflight (`config_errors`) even while the flags are off. It also reports `task_prompt_allowlist` as `configured` or `empty`, never the names. With an empty list, `config_errors` holds `no_allowed_projects` for `capture` and `jev` (flags off: the reasons stay `not_enabled`); with a flag on, that feature's disabled reason becomes `no_allowed_projects` unless an earlier error (`ingest_token_missing`, `invalid_purge_interval`, `credentials_missing`, `invalid_budget_config`) comes first.

### Per-project prompt allowlist (O10)

A prompt is stored and scored only when **both** lists allow its project: the backend's `TASK_PROMPT_ALLOWED_PROJECTS` and the plugin's `task_prompt_allowed_projects`. Both are empty by default, so nothing is captured. Names match exactly after lower-casing; there are no globs or prefixes. The `hermes` fallback project may be listed, but then work done in general `hermes` chat (PEGA included) is captured too (accepted risk, ADR-002).

- **Root tasks only.** A prompt is stored only for a proven root task (`hierarchy_status='root'`, no parent, `root_task_ref` NULL or its own id). Subagent (child) sessions never carry one. A task that later becomes a child loses its stored prompt (NULLed like a purge).
- **Eligibility marker.** The backend stores a prompt only when the event carries `prompt_eligibility: "v1-allowed"`, which the current plugin sets on an authorized root prompt. An older plugin or a pre-upgrade spool file cannot deliver one.
- **Path deny (plugin).** Sessions whose resolved workspace is under `~/Projects/*-devir` (hand-off clones) or a configured extra glob never send a prompt, even when the project name is listed.
- **Not allowed = metadata only.** The event is still inserted and counted; only the prompt is dropped. A validation error never echoes the prompt.
- **JEV.** Only completed turns are scored (completion `session_end`/`next_task`, or 60 idle minutes), like the pilot's `select`. Before every provider attempt the worker re-checks the list, the proven-root rule and completion. A task that fails is recorded with `error_type='project_not_allowed'` (unlisted or not a root) or `task_not_completed` (`skipped` before the first attempt, `error` after one); `/evaluate` reports the same reasons. Removing a project and restarting blocks its stored tasks from JEV without purging them; they expire through the normal 30 days.
- `GET /api/tasks?allowed_only=true` (with `X-Ingest-Token`) lists only tasks of listed projects; the pilot's `select` uses it.

What each capability needs. Metadata-only use does not depend on prompt capture or JEV:

| Capability | Needs | Auth |
|---|---|---|
| Task metadata: list, detail, tokens, cost, tools, wall time, completion, start complexity | Schema v10 (any plugin version; backfilled tasks have hierarchy `unknown`) | none (loopback / tailnet only) |
| Hierarchy (`root`/`child`), request-composition counts | Plugin 0c (`subagent_start`, `pre_api_request` composition) | none |
| Store one scrubbed prompt per task | `STORE_TASK_PROMPTS=1` + `INGEST_TOKEN` + valid purge interval, plugin `capture_task_prompt: true`, `/api/meta` probe ready, ADR-002 approved; project on backend `TASK_PROMPT_ALLOWED_PROJECTS` **and** plugin `task_prompt_allowed_projects`, workspace path not denied, root task only | producer sends `X-Ingest-Token` |
| Read prompt text | Stored and not expired; `GET /api/tasks/{ref}?include_prompt=true` | `X-Ingest-Token` + allowed Origin |
| Human labels, pilot CLI | Schema v10 | `X-Ingest-Token` |
| JEV scoring | `JEV_ENABLED=1` + `AI_GATEWAY_API_KEY` + `INGEST_TOKEN` + valid budget, ADR-002 approved; task is a proven root of a project on the backend allowlist | `X-Ingest-Token` for `/evaluate` |
| Purge / retention status | Schema v10 | `X-Ingest-Token` |

Pilot CLI (over HTTP, token from `INGEST_TOKEN`; sample files and reports hold no prompt text):

```bash
python jev_pilot.py select --n 50 --seed 7
python jev_pilot.py label  --sample ~/.local/share/token-inspector/pilot/sample-seed7.json --labeler <you>
python jev_pilot.py score  --sample <file> --labeler <you>
python jev_pilot.py report --sample <file> --labeler <you> --out Plans/task-telemetry-jev-pilot/pilot-report.md
```

Rollback and purge: see the runbook in ADR-002 (`scripts/purge_task_prompts.py`, `scripts/rollback_v10.sql`).

## Launcher jobs and cost per job (O9, schema v11)

> **Status:** on the feature branch, not deployed. Production still runs the O10 release until the single O9 deploy.

A launcher job (`hermes.sh send`, `hermes.sh devir-baslat`) is tied to its LLM calls through event tags. Contract: [ADR-003](docs/adr/003-job-correlation-contract.md).

| Env var (launcher → producer) | Tag | Accepted shape |
|---|---|---|
| `TOKEN_INSPECTOR_JOB_REF` | `job_ref` | the launcher id `[devir-]YYYYMMDD-HHMMSS-<pid>` |
| `TOKEN_INSPECTOR_WORK_TYPE` | `work_type` | `brainstorm`, `review`, `code`, `devir`, `k1`, `k2`, `other`; producers omit any other value (the backend maps an unknown value that still arrives to `other`) |
| `TOKEN_INSPECTOR_JOB_ATTEMPT` | `job_attempt` | integer 1–9999 (launcher retry, not the per-call `attempt`) |

The `/hermes` skill passes `HERMES_WORK_TYPE` / `HERMES_JOB_ATTEMPT` to `hermes.sh`, which validates them and exports the variables above right before the agent starts. Producers add `runtime` (`hermes-agent`, `claude-code@windows`, `claude-code@hermes`) and `producer` (`hermes-plugin`, `claude-code-hook`) themselves.

- **Never rejected.** The backend normalizes the reserved keys (`job_ref`, `runtime`, `work_type`, `job_attempt`, `producer`) instead of rejecting the event. An invalid or unpaired `runtime`/`producer` marks the event `attribution_invalid`: stored, never counted in jobs.
- **Tag cap** is 1024 bytes and 20 keys (was 512 bytes).
- **One job per task:** the first valid `job_ref` wins; a later different one is counted in `job_ref_conflicts`, never applied.
- **Cost per job** sums priced calls only; `cost_usd` is `null` when no call is priced, and `unpriced_count > 0` means the total is incomplete. JEV evaluator usage is excluded. There is no job duration: first/last event times are not end-to-end job time.
- **Install order** inside the one deploy: backend (new cap) before the plugin that sends the new keys.

**Cross-project subagents.** A child whose parent turn lives in another project now links to it: the plugin sends a validated `parent_project_name` (never stored). Rows written before the fix are repaired with:

```bash
python scripts/repair_task_parents.py --db "$DB_PATH"            # dry-run (default): counts only
python scripts/repair_task_parents.py --db "$DB_PATH" --apply    # verified online backup first, then one transaction
```

`--backup-dir DIR` puts the backup elsewhere (default: next to the DB, `<db>.bak-repair-<UTC stamp>`). Output is one count per class, never refs or names.

**Schema rollback** goes only through `python scripts/rollback_schema.py --db "$DB_PATH" --to 11` (or `--to 10`; service stopped; exact source-version guard, SQLite ≥ 3.35). `--to 11` runs `scripts/rollback_v12.sql`; `--to 10` runs it and then `scripts/rollback_v11.sql`, one step per transaction. The runner names the app commit to start afterwards.

## Versioned read-only export (O9, schema v12)

> **Status:** on the feature branch, not deployed.

`GET /api/export/v1/jobs`, `/tasks` and `/events` serve jobs, tasks and LLM calls as JSON for other local tools. Read-only, no auth (also with `INGEST_TOKEN` set). Contract, field tables and error codes: [docs/export-contract-v1.md](docs/export-contract-v1.md); design: [ADR-005](docs/adr/005-export-contract.md).

```bash
curl -s "http://127.0.0.1:8100/api/export/v1/events?from=2026-09-01T00:00:00Z&to=2026-09-29T00:00:00Z&limit=1000"
```

- `from` inclusive, `to` exclusive, at most 92 days; `limit` 1–1000 (default 500); follow `next_cursor` until `complete: true`.
- Pages are a snapshot: calls ingested after page 1 appear in the next export, never in an open cursor chain. A recost or repair that changes stored rows expires open cursors (`409 snapshot_expired`: restart from page 1).
- Tasks and jobs are a start-time cohort (first call in the period; sums include their later calls). For period-exact money, sum the events dataset.
- Zero, `null` (unknown) and an absent key (not measured) are different. No prompt or response text, tool data, paths or host names are exported.
- Schema v12 adds `token_events.ingest_seq` (assigned in commit order from a persistent high-water) and the `export_state` table; the migration numbers existing rows once.

## Claude Code producer

`producers/claude_code/` is a stdlib-only Claude Code hook (`Stop`, `SubagentStop`, `SessionEnd`) that sends one `llm_request` event per API call from the session transcript: allowlisted metadata only, never prompt or response text, tool data or paths. It is bounded to 5 s per hook, fail-open, and at-least-once with idempotent `cc-` event ids that are unique across projects ([ADR-004](docs/adr/004-cross-source-dedup-authority.md)). Install, configuration, sent fields and limits: [producers/claude_code/README.md](producers/claude_code/README.md).

> **Status:** built and tested, **not installed** on any machine yet. `install.py` defaults to a content-free dry-run summary; `--apply` changes the global `~/.claude/settings.json` and is a separate, user-approved deploy step.

## Connecting Hermes or any other project

Use the helper below or adapt it into your Hermes plugin / provider wrapper. It is a thin wrapper over the bounded client in `token_inspector_client.py`, so the caller never waits for HTTP:

- it posts only to `/api/events/batch` and sends `X-Ingest-Token` from `TOKEN_INSPECTOR_API_KEY` (the value of the backend's `INGEST_TOKEN`);
- it gives every event a stable `client_event_id` (written back to the caller's object, so a resend is deduplicated);
- it checks each event at enqueue with the same JSON encoding the transport uses (no NaN/Infinity, valid UTF-8); an event that fails is counted in `serialization_failed` and never blocks the rest of its batch;
- it never sends `prompt_text`, `task_prompt_text` or `error_message` (send `error_type` and `http_status` instead);
- it checks the HTTP status and the batch ack (`valid_ack`) and counts every event once: `delivered_events`, confirmed loss (`lost_events` = `dropped_events` queue full + `serialization_failed` + `rejected_events` + `http_failures` + `dropped_on_close`) and unconfirmed delivery (`unconfirmed_events` = `transport_unconfirmed` + `malformed_acks`; the server may have stored these).

```python
import os
import uuid

from token_inspector_client import TokenInspectorClient, valid_ack  # noqa: F401 (valid_ack checks every ack)

_client: TokenInspectorClient | None = None


def _token_inspector() -> TokenInspectorClient:
    """One module-level bounded client; status, ack (valid_ack) and loss counters are its own."""
    global _client
    if _client is None:
        _client = TokenInspectorClient(
            project_name=os.environ.get("TOKEN_INSPECTOR_PROJECT", "hermes"),
            base_url=os.environ.get("TOKEN_INSPECTOR_URL", "http://127.0.0.1:8100"),
            ingest_token=os.environ.get("TOKEN_INSPECTOR_API_KEY") or None,
        )
    return _client


async def push_token_event(
    model: str,
    provider: str | None = None,
    tool_name: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    turn_id: str | None = None,
    api_request_id: str | None = None,
    client_event_id: str | None = None,
    tool_call_count: int | None = None,
    role: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,
    complexity: int | None = None,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    process_time_ms: int | None = None,
    request_size_bytes: int | None = None,
    response_size_bytes: int | None = None,
    status: str = "success",
    error_type: str | None = None,
    http_status: int | None = None,
) -> bool:
    """Enqueue only (never awaits HTTP); False means the event was dropped and counted."""
    payload = {
        "model": model,
        "provider": provider,
        "tool_name": tool_name,
        "session_id": session_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "turn_id": turn_id,
        "api_request_id": api_request_id,
        "client_event_id": client_event_id or uuid.uuid4().hex,  # reused on any resend
        "tool_call_count": tool_call_count,
        "role": role,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "complexity": complexity,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "process_time_ms": process_time_ms,
        "request_size_bytes": request_size_bytes,
        "response_size_bytes": response_size_bytes,
        "status": status,
        "error_type": error_type,
        "http_status": http_status,
    }
    return await _token_inspector().post_event({k: v for k, v in payload.items() if v is not None})


async def close_token_inspector(timeout: float = 2.0) -> None:
    """Call at shutdown: drains the queue, then counts anything left as dropped_on_close."""
    if _client is not None:
        await _client.close(timeout)
```

### Batch ingest

If you already buffer events in memory, the bounded client above is the simplest way to send them. To POST a batch yourself, give every event a stable `client_event_id`, send no prompt or error text, and account for every event:

```python
import os

import httpx

from token_inspector_client import valid_ack


async def post_batch(client: httpx.AsyncClient, events: list[dict]) -> dict:
    """One direct batch POST; every event ends as delivered, lost or unconfirmed."""
    n = len(events)
    result = {"delivered": 0, "lost": 0, "unconfirmed": 0}
    try:
        response = await client.post(
            "http://127.0.0.1:8100/api/events/batch",
            headers={"X-Project-Name": "hermes", "X-Ingest-Token": os.environ["TOKEN_INSPECTOR_API_KEY"]},
            json={"events": events},
        )
    except httpx.HTTPError:
        result["unconfirmed"] = n  # transport failure: the server may have stored them
        return result
    if not response.is_success:
        result["lost"] = n  # non-2xx: nothing was stored
        return result
    try:
        ack = response.json()
    except ValueError:
        ack = None
    if not valid_ack(ack, n):
        result["unconfirmed"] = n  # 2xx with a malformed ack: stored or not is unknown
        return result
    result["delivered"] = ack["inserted"] + ack["duplicates"]
    result["lost"] = ack["rejected"]  # a rejected event is lost, never delivered
    return result
```

The batch endpoint deduplicates by `client_event_id` when present (per project; ids starting with `cc-`, reserved for the Claude Code producer, across projects).

## Hermes integration notes

For Hermes, the best source of data is the shared model/provider hook path:

- `post_api_request` / `post_llm_call` for usage + latency + model/provider metadata
- `pre_tool_call` / `post_tool_call` for the active tool name and tool result boundary
- `session_id`, `turn_id`, and `api_request_id` for later trace correlation

That lets one telemetry sink cover:

- main agent model calls
- auxiliary calls from browser / vision / compression / search / MCP / TTS paths
- tool-scoped and session-scoped analysis in the dashboard

## API Reference

```
POST /api/events                          Ingest a token event
POST /api/events/batch                    Ingest a batch of token events
GET  /api/events?project=&model=&status=  Paginated event log

GET  /api/meta                            Schema version, feature gates, config_errors, task_prompt_allowlist
GET  /api/tasks?allowed_only=true         Tasks of allowlisted projects only (needs X-Ingest-Token)
GET  /api/jobs?days=30&runtime=&work_type=&page=1&page_size=50
                                          Cost per launcher job (read-only, no auth; invalid filter → 400 invalid_filter)
GET  /api/export/v1/{jobs,tasks,events}?from=&to=&limit=&cursor=
                                          Versioned read-only export (docs/export-contract-v1.md)

GET  /api/analytics/summary?days=7        Overall stats
GET  /api/analytics/project-inventory     Bounded Git inventory with telemetry status
GET  /api/analytics/by-project?days=30    Event-backed per-project breakdown
GET  /api/analytics/by-model?days=30      Per-model breakdown
GET  /api/analytics/by-provider?days=30   Per-provider breakdown
GET  /api/analytics/by-tool?days=30       Per-tool breakdown
GET  /api/analytics/by-role?days=30       Per-role breakdown
GET  /api/analytics/timeseries?days=30    Daily buckets for charts
GET  /api/analytics/by-complexity?days=30 Avg cost/tokens grouped by complexity tier
GET  /api/analytics/routing-recommendations Model switch suggestions by (role, complexity)

GET    /api/settings/pricing              List pricing rules
POST   /api/settings/pricing              Add or update a pricing rule
DELETE /api/settings/pricing/{model}      Remove a pricing rule
```

## Data Fields Captured Per Event

| Field | Type | Notes |
|---|---|---|
| project_name | string | From `X-Project-Name` header |
| model | string | e.g. `claude-sonnet-4-6`, `openai/gpt-4o` |
| provider | string? | e.g. `anthropic`, `openai`, `google` |
| tool_name | string? | Hermes tool / aux task name |
| session_id | string? | Session correlation id |
| trace_id | string? | Request trace id |
| span_id | string? | Child span id |
| parent_span_id | string? | Parent span id |
| turn_id | string? | Turn-level correlation id |
| api_request_id | string? | Request-scoped correlation id |
| client_event_id | string? | Optional idempotency key |
| tool_call_count | int? | Number of tool calls in the assistant response |
| role | string? | Agent role, nullable |
| prompt_tokens | int | Input token count |
| completion_tokens | int | Output token count |
| cache_read_tokens | int | Tokens served from cache |
| cache_creation_tokens | int | Tokens written to cache |
| process_time_ms | int? | LLM call latency |
| request_size_bytes | int? | Raw prompt payload size |
| response_size_bytes | int? | Raw response payload size |
| estimated_cost_usd | float? | Calculated at ingest from pricing table |
| status | string | success / error / timeout |
| error_message | string? | Accepted from older producers; the Hermes plugin and the safe client no longer send it (they send `error_type` + `http_status`) |
| complexity | int? | 1–5 tier; deterministic plugin metadata or legacy AI scorer |
| prompt_text | str? | Truncated prompt for AI scoring (max 3000 chars) |
| recorded_at | string | ISO8601 UTC timestamp |

## Complexity Scoring

The Hermes plugin normally sends deterministic `request-shape-v1` complexity. It is computed at request time from numeric metadata and does not require or retain raw content. The `/api/analytics/by-complexity` endpoint includes both priced and unpriced events in token averages; cost averages remain `null` when no priced observations exist.

### Legacy opt-in AI scorer

When another source pushes an event with `prompt_text` but no `complexity`, the inspector
asynchronously calls an LLM to score complexity (1–5) and updates the stored event.
This happens in the background — the `POST /api/events` response returns immediately.

**Config:**
- `COMPLEXITY_SCORER_MODEL` — model to use (default: `claude-haiku-4-5-20251001`)
- `COMPLEXITY_SCORER_API_KEY` — API key; falls back to `ANTHROPIC_API_KEY`

If no API key is set, scoring is silently skipped. Existing events with null complexity
are not retroactively scored.

## Pre-seeded Pricing Rules

22 models across Anthropic, OpenAI, and Google Gemini are seeded on first run.
All rules are editable via the Settings view.
