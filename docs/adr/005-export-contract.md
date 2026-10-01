# ADR-005: Versioned Read-Only Export Contract

**Status:** Accepted
**Date:** 2026-10-01
**Plan:** `Plans/o9-job-correlation-cc-producer/` (backend.md X1–X6, database.md v12)
**Contract:** [`docs/export-contract-v1.md`](../export-contract-v1.md)

## Context

Other local tools (first PossibleSkills) need Token Inspector's jobs, tasks and LLM-call usage
without reading its SQLite database or scraping the dashboard. They need a stable field list, stable
ids, a dedup rule, explicit zero/unknown/absent semantics and pagination that stays consistent while
the producers keep ingesting. The existing `GET /api/events` listing is a dashboard API: it returns
whole rows (with tags and metadata that must not leave), orders by `recorded_at`, and uses
offset pages that shift under concurrent ingest.

## Decision

### 1. A separate read-only router

`routes/export.py` serves `/api/export/v1/{jobs,tasks,events}`. It never writes, does not reuse
`GET /api/events`, and has its own contract version (`schema_version: 1`), independent of the app
and the database schema. Errors are static bodies that never echo input.

### 2. Snapshot by `ingest_seq` with a persistent high-water

Schema v12 adds `token_events.ingest_seq`, assigned inside the INSERT from
`export_state.last_seq + 1` under the write lock, so the sequence follows commit order. The
high-water `last_seq` never decreases, so a number at or below a saved snapshot is never handed out
again, even after deletes. The first page reads `last_seq` as `as_of`; every page of that cursor
chain reads only `ingest_seq <= as_of`, and keys events by `ingest_seq`.

Rejected: `recorded_at` (computed before the write lock, so commit order and timestamp order can
differ — a late commit with an earlier time would fall into a gap) and `rowid` (renumbered by
`VACUUM`, reusable after deletes). Rejected: `MAX(ingest_seq) + 1` (reuses numbers after the top
rows are deleted).

Membership and sums come only from snapshot events, including a task's job (the first job tag among
its snapshot events, not the live `tasks.job_ref`), so a page never mixes two states of the data.

### 3. Revision and epoch invalidate cursors

`export_state.revision` is bumped in the same transaction by any write that changes already stored
data the export reads: recost `--apply` with changes and repair `--apply` with changes (and any
future maintenance that deletes or rewrites `token_events`). `export_state.epoch` is a random id
written when the row is created; a schema rollback drops it and a re-upgrade makes a new one.
Cursors carry `as_of`, `revision` and `epoch`; a mismatch returns `409 snapshot_expired`, and the
consumer restarts. Rows written by older code (NULL `ingest_seq` after a code-only rollback) are
numbered at startup above the high-water, inside the migration transaction.

### 4. Allowlists for keys and values

Items are built field by field from `EXPORT_FIELDS[dataset]`; only five tag keys surface. A key
allowlist does not stop a path or host name stored inside an allowed field, so every exported string
also has a value rule (safe id or deterministic pseudonym, model shape, closed error class, closed
lists, job ref shape). A denylist was rejected: a new column or tag would leak by default.

### 5. No authentication

The export is read-only, serves no content (no prompts, responses, tool data, paths or host names)
and runs on a loopback/tailnet single-user service, like `/api/analytics/*` and `/api/jobs`. It must
not depend on `require_sensitive_auth`, which refuses every request while `INGEST_TOKEN` is unset.

## Consequences

- Tasks and jobs are a start-time cohort: their period sums can differ from the events dataset of the
  same period; the contract documents this, and period-exact money comes from the events dataset.
- `updated_at` is informational; consumers replace whole periods (full replacement).
- Every write path that deletes or rewrites `token_events` rows must bump `export_state.revision`
  (AGENTS.md gotcha); schema rollback runs only through `scripts/rollback_schema.py`.
- The CSV for people is built in the dashboard from the JSON pages; the server has no CSV code.
