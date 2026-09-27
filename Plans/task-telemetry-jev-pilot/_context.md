# Context — task-telemetry-jev-pilot (2026-09-27)

Input for project-manager / planner. Sources: user brainstorm decisions, Hermes read-only research run
(2026-09-27), measurements taken on the hermes machine, live JEV test calls.

## Where the code lives
- Backend `tokenInspector`: canonical working copy on hermes `~/Projects/tokenInspector` (production service
  `token-inspector.service`, 127.0.0.1:8100, `.venv`, prod DB `~/.local/share/token-inspector/token-inspector.db`).
  This Windows repo was fast-forwarded to the same commit `0aab993`; GitHub `origin/main` is 4 commits behind.
- Hermes producer plugin `token_inspector` v0.2.1: separate repo only on hermes `~/Projects/token_inspector`
  (installed copy `~/.hermes/plugins/token_inspector`, no git remote). Files: `__init__.py` (hooks), `mapping.py`,
  `complexity.py` (request-shape-v1), `project.py`, `redact.py`, `sink.py` (bounded queue → `POST /api/events/batch`),
  `ids.py`, `config.py`, `state.py`, tests/ (25 tests).
- Claude Code runs on this Windows machine: v2.1.283, **Pro subscription via claude.ai OAuth**, no API key,
  no ANTHROPIC_BASE_URL, telemetry not configured.

## Locked user decisions
| Topic | Decision |
|---|---|
| Architecture | "Orta": Claude Code OTel (logs+metrics) + hooks for task boundaries; Hermes plugin fixes; **no model-traffic proxy** |
| Task unit | One user prompt → final Stop = one task; subagents and Hermes delegations = child tasks (`parent_task_id`/`root_task_id`) |
| Content stored | Per task: user's prompt text, **redacted**, full text. Per model call: **request composition** (system / history / tool-output / file-content token or char counts, tool names, file refs). No response bodies, no raw API bodies |
| Retention | Request content 30 days; token/cost/difficulty metadata permanent |
| Difficulty | JEV **pilot first** on ~50 hand-labelled tasks before automating. Start-of-task difficulty kept separate from observed intensity (tokens, cost, tool calls, retries, wall time) |
| Loss tolerance | Durable disk spool + visible drop/reject counts |
| Database | Stay on SQLite (same DB). Add `tasks` + `task_evaluations`; no Postgres/DuckDB/ClickHouse now |
| JEV provider routing | typesafe-ai first; **digitalocean allowed as fallback** when typesafe-ai is overloaded (429) |

Consequence: this reverses the repo contract `STORE_RAW_PROMPTS=0` for task prompts — deliberate, needs redaction +
30-day purge, and doc/ADR update.

## Measured facts (hermes, 2026-09-27)
- Prod DB: 9,146 `token_events` rows (3,030 llm_request + 5,750 tool_call + 366 session), 268 sessions,
  `task_id` populated on 8,850 rows, raw prompt populated on 0, schema version 9. File 13.1 MiB + WAL 4.0 MiB.
  ~420 events/day last week. `task_id` column exists; no `tasks` table and no FK.
- Plugin defect 1: Hermes core emits `assistant_content_chars`, `assistant_tool_call_count`, `assistant_message`,
  `response`; plugin expects `response_char_count`/`response_content` and does not map tool count →
  `response_size_bytes`, `tool_call_count`, `role` are empty for all prod rows. No TTFT.
- Plugin defect 2: `sink.py` drops a batch that fails to send (no re-queue, no disk spool).
- `redact.py` masks mostly by field name; free-text secret scrubbing is not guaranteed.
- Plugin trace ids are 32-hex; W3C span_id is 16-hex → needs mapping if traceparent is adopted.
- Existing `tags` accept scalars only, max 20 keys / 512 bytes → do not put JEV distributions in tags.
- Cross-source dedup: OTel / JSONL / proxy could report the same request; `client_event_id` alone does not dedupe
  across sources → one canonical source per signal.
- `sqlite3` CLI is not installed on hermes; use Python `sqlite3` with `mode=ro` for read-only checks.

## JEV facts (verified live 2026-09-27)
- Endpoint: `POST https://ai-gateway.vercel.sh/typesafe/v1/systemone`, `Authorization: Bearer $AI_GATEWAY_API_KEY`.
  Key file on hermes: `~/.config/token-inspector/secrets.env` (mode 600, line `AI_GATEWAY_API_KEY=…`). Never log it.
- Body: `{"model":"typesafe-ai/jev","state":<string|json>,"questions":{id:{type,instructions,criteria}}}`.
  Types: `score` (criteria = ordered list, returns fractional `score` in [0,n-1], `confidence`, `probabilities`,
  `legend`), `choice` (criteria = map), `noul` (boolean probability).
- Provider routing via `providerOptions.gateway.only` / order. With `only:["typesafe-ai"]` we got repeated
  `429 rate_limit_exceeded` ("upstream provider high demand") → must honor `retry-after`, exponential backoff,
  and (per decision) allow digitalocean fallback.
- Limits: 64K tokens per request; state + longest question ≤ 32K tokens. Price $0.042 / 1M input tokens, output free.
  Test: 446 input tokens → $0.0000187, ~240 ms.
- Vercel account: Hobby plan, paid credits ($15), JEV is **not** on the free tier. Budget cap recommended.
- Sample result: task "add tasks table + migration + correlate Hermes/Claude events + tests" → score 2.2/4
  (p: L2 0.8, L3 0.2), confidence 0.83; `needs_research` noul 0.3.
- Proposed rubric (0–4, displayed as 1–5; NOT validated): 0 single routine step; 1 a few known steps;
  2 multi-file/multi-step with tests; 3 unclear root cause or multi-system coordination;
  4 open-ended research / deep architectural uncertainty. Store raw_score, probabilities, confidence, model,
  rubric_version, input hash, evaluated_at, provider actually used, cost.
- Privacy: JEV sees the redacted prompt (TypeSafe; digitalocean on fallback; Vercel does not retain content).
  `no_training: all`, `zdr: none`.
- Evaluator usage must be recorded under a separate purpose and must never itself be scored (no loop).

## Claude Code collection facts (for the staged phase)
- OTel: `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_METRICS_EXPORTER`/`OTEL_LOGS_EXPORTER=otlp`,
  `OTEL_EXPORTER_OTLP_ENDPOINT/PROTOCOL`. Events `api_request` (model, tokens incl. cache, cost, duration, request_id),
  `api_error`, `tool_result`, `user_prompt` (`OTEL_LOG_USER_PROMPTS=1` for text). `prompt.id` correlates events of
  one user prompt; hooks expose matching `prompt_id`. tokenInspector has no OTLP receiver → a normalizer is needed.
- Hooks: UserPromptSubmit (start + prompt), Stop/StopFailure (end), SubagentStart/SubagentStop (child), PreToolUse/
  PostToolUse(+Failure), SessionStart/End. Hooks carry no per-call usage (except foreground Agent tool_response).
- JSONL transcripts `~/.claude/projects/**/*.jsonl` (+ nested subagents) → backfill; dedup by message.id/requestId,
  persistent cursor; latency/TTFT not reliable from transcripts.
- Network: prod backend binds 127.0.0.1 on hermes → Windows needs SSH tunnel over Tailscale or a tailnet-only
  authenticated listener; never public.

## Proposed phasing (from brainstorm)
- Faz 0: plugin mapping fix + durable spool (plugin repo).
- Faz 1: Claude Code OTel + task hooks → normalizer → tokenInspector (staged after pilot prep is fine).
- Faz 2: `tasks` + `task_evaluations` (+ request composition, 30-day content purge), Tasks view.
- Faz 3: JEV pilot: ~50 hand-labelled tasks, compare JEV vs labels (and vs request-shape-v1), then automate.
- Faz 4 (optional, not now): model-traffic gateway.

## Constraints for any plan
- Hermes machine cannot reach PEGA; irrelevant here but every hermes prompt must say so.
- Hermes only reviews (plan + code); Claude Code implements. Max 2 review rounds per deliverable.
- Production DB: additive migrations, online backup first, never reset/delete.
- Frontend is vanilla JS + Chart.js, no build step. Plugin must stay bounded, short-timeout, fail-open.
