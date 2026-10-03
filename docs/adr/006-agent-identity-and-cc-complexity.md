# ADR-006: Agent identity and Claude Code input-size complexity

- Status: Accepted
- Date: 2026-10-03
- Scope: schema v13 data layer; no Agents dashboard tab

## Context

Claude Code transcripts identify subagent runs, but raw ids, names, paths and sidecar descriptions are not safe
telemetry dimensions. A label answers “which sanitized agent category produced this call”; it is not a run
identity. Run correlation remains the existing pseudonymized session/turn/task structure.

Claude Code also lacks the request-time message shape used by Hermes `request-shape-v1`. Assigning that method
to transcript usage would make incomparable evidence appear comparable.

## Decision

### Agent label

The producer binds one sanitized label to each transcript run. Resolution precedence is:

1. an existing state binding for the same run/file identity;
2. the current `SubagentStop.agent_type` hook value;
3. only the sibling `agent-<id>.meta.json` `agentType` value, read with a 64 KiB cap;
4. `unknown`.

Primary transcripts use the producer-generated label `main`. File truncation, replacement or run-key mismatch
clears the old binding before resolution. No hook value may leak to a different target transcript.

Custom names are default-deny. The producer passes a custom name only in `allowlist` mode when its complete
value matches the safe grammar, is an exact allowlist member, and case-insensitively differs from every detected
local hostname candidate. Otherwise it emits `a-` plus the first 16 lowercase hexadecimal characters of
SHA-256 over the complete source value. Source values equal to reserved producer labels (`main`, `custom`,
`unknown`) are also pseudonymized. Values that are missing, non-string or longer than 64 characters become
`unknown`. Values are never trimmed, lowercased, slugified, decoded or partially salvaged.

The backend validates fail-open: an invalid agent becomes null and never rejects or echoes the event. The label
is a nullable `token_events.agent` column, not a tag. Task `agent`, `agent_conflict` and
`agent_unavailable_calls` are derived from that task's own counted calls. Export derives them from snapshot
events, never unrestricted live events.

### Claude Code complexity

`cc-input-size-v1` uses only:

```text
I = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
```

Its tiers are `[0,4000)`, `[4000,16000)`, `[16000,64000)`, `[64000,128000)`, and `[128000,+∞)`.
All three inputs must be present non-negative integers; malformed usage is unscored. Output tokens, text, tools,
price and elapsed time do not contribute. `input_tokens_include_cache=false` remains unchanged: emitted
`prompt_tokens` already excludes cache.

`cc-input-size-v1` and `request-shape-v1` have separate method names and are not comparable tier-for-tier.
Task start complexity retains the actual method of the earliest eligible counted LLM call.

### Read contract and coverage

`GET /api/analytics/agents` groups counted calls by UTC day, runtime, nullable agent, nullable method/tier and
model within an explicit bounded range. It excludes evaluator and invalid-attribution calls, retains unpriced
and unscored calls by default, and reports global distinct tasks separately from cell task counts. More than
20,000 cells returns a static `result_too_large` error rather than truncation.

Agent and Claude Code complexity coverage starts when schema-v13-compatible producers are deployed. Historical
calls are not backfilled. A named label is not promised for every run; unverifiable resumed/backlog drains are
`unknown`.

## Consequences

- Agent labels are privacy-bounded, stable where pseudonymized, and cannot spoof reserved producer meanings.
- State grows by only a bounded four-key agent block and remains under its 4096-byte cap.
- Sidecar work is one bounded local read per unresolved drained subagent, inside the existing hook budget.
- Consumers must retain method identity when comparing complexity and must handle null agent/tier values.
- Export v1 gains additive fields; unknown complexity methods remain omitted, so the contract version stays 1.
