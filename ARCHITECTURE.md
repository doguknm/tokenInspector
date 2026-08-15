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

## Runtime State

```text
Backend unit: token-inspector.service
Bind: 127.0.0.1:8100
Production DB: user-local application data directory
Plugin: token_inspector, independently installed and gateway-loaded
```

Remote exposure is not part of the default architecture. If enabled, it must remain private-network-only; public Funnel-style exposure is rejected.

## Failure Behavior

- Backend down: producer drops/retries within bounded queue policy; caller proceeds.
- Duplicate event: database conflict path returns deduplicated result.
- Unknown model price: event persists as unpriced.
- Invalid event: rejected and counted; no fabricated fallback values.
- NotebookLM unavailable: local docs/Vault succeed and external sync is queued.

## Rejected Alternatives

- Patching Hermes core or every individual tool: excessive coupling and upgrade risk.
- Storing raw prompts by default: unnecessary privacy exposure.
- Treating unknown prices as zero: falsely reports free usage.
- Deriving project identity only from folder name: diverges from canonical repository identity.
- Mixing repository inventory with event aggregation: implies activity where none occurred.
- Unbounded recursive home-directory discovery: privacy and performance risk.
