# ADR-004: Cross-Source Dedup Authority

**Status:** Accepted
**Date:** 2026-09-28
**Plan:** `Plans/o9-job-correlation-cc-producer/` (backend.md C10; status.md Drift Log "C0 rule 1-9")

## Context

Token Inspector now has two counting producers: the Hermes plugin (`hermes-plugin`) and the Claude Code hook (`claude-code-hook`, `producers/claude_code/`). A consumer that sums llm-call events must count every provider call exactly once, even when the same call is observed more than once: Claude Code writes several transcript lines per API call while it streams, a hook can run again over the same transcript, a resumed session copies earlier messages into a new session file, and a copy may resolve to a different project (another cwd or alias).

## Decision

### 1. One counting producer per runtime

| Runtime | Only counting producer |
|---|---|
| `hermes-agent` | `hermes-plugin` |
| `claude-code@windows` | `claude-code-hook` |
| `claude-code@hermes` | `claude-code-hook` |
| `app` | `app-provider` (not built) |

The backend (`routes/events.py` `_normalize_reserved`) enforces the pairing. A `runtime` without its producer, a `producer` without its runtime, or any other pair drops **both** tags and marks the event `attribution_invalid: true`. The event is still inserted (never rejected) but the jobs API and the export never count it, and it is never inferred as legacy `hermes-agent`. Genuine legacy = no `runtime`, no `producer` and no mark.

The plugin and the hook observe disjoint runtimes: a `claude -p` process started by a hermes job (devir) is `claude-code@hermes`, never `hermes-agent`.

### 2. Within Claude Code, the transcript usage record wins

Only transcript assistant lines with `message.usage` produce events. Hook stdin only locates the transcript and the project; no event is built from hook fields (test `test_no_event_from_hook_stdin`). `<synthetic>` lines (zero usage, API errors) are never sent.

### 3. Claude Code event identity

`client_event_id = "cc-" + sha256(message.id + "\x1f" + (requestId or ""))[:32]` — never derived from the session. The lines of one streaming group share it; the reader emits a group only once it is final (a line with a non-null `stop_reason`, a later call, or a new prompt), so the final usage is the one stored. Re-runs and resumed copies keep `message.id` and `requestId` and produce the same id.

The `cc-` prefix is reserved for this producer. `ux_token_events_cc_client_event` (v11) makes it unique **across projects**, and `_insert_event` treats a conflict on either unique index as a duplicate. The first arrival owns the project and task; a later copy under another project is acknowledged as `duplicates` and counted once. Other producers keep per-project idempotency `(project_name, client_event_id)`.

### 4. Consumer dedup rule

A consumer counts `token_events` rows (each already unique per call as above) and never sums a producer's hook-level data. Events marked `attribution_invalid` are excluded. The versioned export (Phase 3) documents the same rule and links here.

## Consequences

- A resumed Claude Code copy that lands in another project is not double counted, but its tokens stay in the project of first arrival.
- A streaming group whose final line is not yet on disk waits for the next hook (never an early usage). An interrupted last call of a session without a `stop_reason` is sent only when the session continues (producer README, coverage limits).
- A new producer must be added to the pairing table before its events count.
