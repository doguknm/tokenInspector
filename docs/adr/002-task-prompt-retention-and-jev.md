# ADR-002: Task Prompt Retention and JEV Difficulty Scoring

**Status:** Accepted for implementation; **activation pending the Approval section below**
**Date:** 2026-09-27
**Plan:** `Plans/task-telemetry-jev-pilot/` (status.md Drift Log records the D0 decisions)

## Context

The pilot compares a semantic start-of-task difficulty score from JEV (TypeSafe AI, via Vercel AI Gateway) with human labels and with the deterministic `request-shape-v1` heuristic. JEV needs the text of the request, but the service contract so far was `STORE_RAW_PROMPTS=0`: no prompt text is stored.

A task is one Hermes turn: one user prompt through the final response, keyed by `(project_name, session_id, turn_id)`. Hermes' own `task_id` is session-scoped on the gateway and API platforms, so it is only kept as `source_task_id`.

## Decision

### 1. A separate, gated exception for task prompts

- `STORE_RAW_PROMPTS` stays `0`. Event-level raw prompts are unchanged.
- A new flag `STORE_TASK_PROMPTS` stores **one scrubbed prompt per task** in `tasks.prompt_text`, never in `token_events`.
- Capture starts only when `STORE_TASK_PROMPTS` is on, `INGEST_TOKEN` is set (at least 16 chars) and `TASK_PROMPT_PURGE_INTERVAL_S` is 1..21600. Otherwise it refuses to start, logs `[SECURITY] … disabled: <reason>` and reports the reason in `/api/meta`.
- The Hermes plugin sends the prompt only when its own `capture_task_prompt` is on **and** `/api/meta` reports schema ≥ 10 with capture enabled (fail closed).
- Stored text is capped at 32,000 chars; `prompt_length` keeps the original length. First write wins.

### 2. Redaction `task-redact-v1`

`redaction.py` (backend) and `task_redact.py` (plugin) are byte-identical and run the same vectors (`tests/fixtures/redaction_vectors.json`). The plugin scrubs before the in-memory queue and the disk spool; the backend scrubs again before storage.

Categories replaced: PEM private keys; key shapes (`sk-`, `AKIA`, `gh*_`, `github_pat_`, `xox*-`, `AIza`); JWTs; bearer tokens; values of secret-named assignments (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*PASSWD*`, `*CREDENTIAL*`); Git remotes; URL userinfo; home prefixes (to `~`); other absolute POSIX and Windows paths; internal hostnames (`.local`, `.lan`, `.internal`, `.intranet`, `.corp`, `.home.arpa`, `.ts.net`, plus `REDACT_INTERNAL_HOST_SUFFIXES`) and whole-word `REDACT_EXTRA_TERMS`; private IPv4 ranges; emails.

**Residual risk:** pattern-based scrubbing is not exhaustive. Secrets in unusual formats, customer names, business details and free-text confidential content are **not** detected.

### 3. Retention

- Expiry = producer capture time + 30 days, **never extended**. A spool replay whose capture time is already 30 days old is discarded.
- Read-time enforcement: expired-but-unpurged text is never returned, never selected for the pilot and never sent to JEV.
- The plugin strips expired prompts from its spool and dead-letter files, and orphan `.tmp` files never survive a startup.
- The purge runs at startup before serving and then whenever capture is on or retained data exists. It NULLs columns (no row is deleted), commits, runs `wal_checkpoint(TRUNCATE)` whenever the persisted `pending_wal_checkpoint` flag is set, and re-purges + VACUUMs DB-directory backups (`<db>.bak-v*`). Human label notes follow the same 30 days.
- `TASK_PROMPT_PURGE_INTERVAL_S=0` is honoured only after `scripts/purge_task_prompts.py --all --include-backups --verify` confirms no text remains anywhere.
- **No pre-purge backup** is taken, because it would copy the expiring text.

| Scope | Guarantee |
|---|---|
| Main DB file | Column NULLed; `secure_delete=ON` zeroes the freed page content |
| WAL | Truncated after checkpoint (retried until it succeeds) |
| DB-directory backups | Re-purged and VACUUMed |
| Filesystem/SSD remnants, OS caches, copies outside the DB directory | **Not guaranteed** |
| Copies held by JEV providers | **Outside local control** |

### 4. JEV exposure and cost controls

- Requests go to `https://ai-gateway.vercel.sh/typesafe/v1/systemone`, model `typesafe-ai/jev`, provider `typesafe-ai` first and `digitalocean` only after a typesafe-ai 429. The scrubbed prompt is the whole `state`.
- The catalogue shows `no_training: all` and `zdr: none`. These are **descriptive gateway metadata, not a verified contractual guarantee**. Provider-side deletion is not controlled.
- Budget: every HTTP call is preceded by a committed `evaluator_attempts` reservation of `request bytes + 1024` tokens at $0.042/M. This assumes a byte-level/BPE tokenizer emits at most one token per input byte and 1024 tokens cover the hidden template (the captured fixture billed 446 tokens for a body well under 1 KB). Uncertain and failed attempts keep their reserved cost. Defaults: $0.05/day, $0.50/month, 200 calls/day.
- Retry-After is honoured exactly and persisted per provider across tasks, runs and restarts. At most 2 attempts per provider; 401/403 stops the run.
- Evaluator usage is recorded as a `token-inspector` event with no task, so it is never scored itself. JEV runs only through `POST /api/tasks/evaluate`.

### 5. Access guards

`require_sensitive_auth` (token always required, Origin allowlist) guards `include_prompt`, labels, evaluate and purge. `TrustedHostMiddleware` rejects foreign `Host` headers (DNS rebinding). The backend stays on loopback.

## Rollback runbook

1. Set `STORE_TASK_PROMPTS=0`, `JEV_ENABLED=0` and plugin `capture_task_prompt: false`; restart the backend and `hermes gateway restart`.
2. `python scripts/purge_task_prompts.py --db <prod path> --all --include-backups --verify` (stdlib only).
3. Prefer a code-only rollback to `0aab993`: v9 code runs on a v10 DB (`scripts/check_v9_app_on_v10.py`). A schema rollback uses `scripts/rollback_v10.sql` after exporting `task_evaluations` outside Git.

## Rejected alternatives

- Reusing `STORE_RAW_PROMPTS` for task prompts: it would store every event's prompt.
- Storing unscrubbed text and scrubbing at read time: the text would sit in the DB, WAL and backups.
- A model-traffic proxy: risky for the Claude Pro OAuth session and not needed for task boundaries.
- Hard-coding `task_id` as the task key: it is session-scoped on the gateway (D0).

## Approval (filled in by the user before activation)

Nothing may be enabled in production until every line below is completed and dated.

- [ ] Data categories that may be captured (task prompt text, scrubbed): ______
- [ ] Both JEV routes accepted (typesafe-ai; digitalocean on overload): ______
- [ ] Residual redaction risk accepted (section 2): ______
- [ ] Provider retention understood (`zdr: none`, not contractual): ______
- [ ] Budget ceilings confirmed (default $0.05/day, $0.50/month, 200 calls/day): ______
- [ ] Extra redaction terms set (e.g. machine and customer names): ______
- Approved by: ______  Date: ______
