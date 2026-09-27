# D0 Discovery Evidence — task-telemetry-jev-pilot

**Date:** 2026-09-27 · **Executor:** Claude Code (Opus 5.5), read-only on hermes over SSH
**Sources:** prod DB `~/.local/share/token-inspector/token-inspector.db` opened `mode=ro` + `PRAGMA query_only=ON`;
Hermes core `~/.hermes/hermes-agent` @ `5646fed`; plugin `~/Projects/token_inspector` @ `df5eb43` (clean tree).
Counts, field names and hook names only — no prompt or response content was read or copied.

## DB facts (2026-09-27)

| Query | Result |
|---|---|
| SQLite version | 3.45.1 (`PRAGMA user_version` = 0; migrations tracked in `schema_migrations`) |
| Tables | `token_events`, `pricing_rules`, `model_aliases`, `schema_migrations` |
| Rows by event_type | llm_request 3,064 · tool_call 5,849 · session 376 |
| Projects | docrag-guardrails 5,503 · hermes 3,461 · pegadocragagent 325 |
| Distinct `(project, task_id)` | 244 |
| task_ids spanning >1 session | 0 |
| Distinct turns per task_id | avg 1.52, max 13; histogram 1→171, 2→64, 3..13→9 |
| Gap between consecutive turns of one task_id | n=363 pairs, median 1,858 s (31 min), p90 ≈ 7.1 days, max ≈ 38.6 days; 23 pairs < 60 s |
| task_ids per session | avg 1.13, max 6 |
| llm_requests per task_id | avg 12.6, max 229 |
| Multi-turn task_ids by task_id length | 13 chars: 46 · 22: 12 · 24: 4 · 36 (uuid4): 11 |
| task_id = session_id | 19 |
| Distinct turns (non-session events) | 300; **no turn spans more than one task_id**; every llm_request has a `turn_id` |
| `turn_id` shape | 299 core-format `session:task:hex8` (2 colons); 74 colon-less (session events: `task_id` or `"session"`) |
| Turns per day (last 7 days) | 10, 7, 11, 44, 29, 16, 12 → 129 (≈ 18/day) |
| Raw prompts stored | 0 |
| `role` filled | 0 rows |
| Complexity provenance | all 8,484 complexity rows carry `tags.complexity_version='request-shape-v1'` and `tags.complexity_source='deterministic_metadata'`; no `complexity_method` tag or column |
| Session events | `finish_reason` holds the phase: start 275 (success) · end 72 (success, **all 72 carry task_id**) · shutdown 23 (cancelled) · new_session 6 (cancelled); no `tags.session_phase` |
| Tag keys present | schema, message_count, api_mode, project_source, project_confidence, complexity_version, complexity_user_message_length, complexity_source, complexity_points, complexity_message_count, complexity_approx_input_units, tool_status, roles (377, all null) |

## Hook / code facts

| Question (O1–O3) | Evidence |
|---|---|
| Who mints `task_id` | `agent/turn_context.py`: `effective_task_id = task_id or str(uuid.uuid4())` per `run_conversation()`. Callers that pass one: `gateway/run.py:22474` `"task_id": session_id` (messaging gateway → **session-scoped**); `gateway/platforms/api_server.py:5782/6234` `session_id or uuid4 / run_id`; `gateway/slash_commands.py:3198` background `bg_<HHMMSS>_<hex6>`. CLI/cron pass none → a fresh uuid4 per message. |
| Is `task_id` = one user prompt → final stop? | **No, not reliably.** 73 of 244 task_ids (30%) span 2–13 turns, mostly the non-uuid (gateway/API, session-scoped) ids, with a median gap of 31 minutes between turns. |
| What is one user prompt → final stop | Hermes **turn**: `turn_id = f"{session_id}:{effective_task_id}:{uuid4.hex[:8]}"`, minted once per `run_conversation()` (turn_context.py). 300 turns, none spans two task_ids, every llm_request carries it. |
| Hook kwargs (`pre_api_request`) | task_id, turn_id, api_request_id, session_id, user_message, conversation_history, platform, model, provider, base_url, api_mode, api_call_count, **request_messages**, message_count, tool_count, approx_input_tokens, request_char_count → request composition is available here (not only at `pre_llm_call`). |
| Hook kwargs (`post_api_request`) | …, finish_reason, response_model, response, usage, assistant_message, **assistant_content_chars**, **assistant_tool_call_count** (plugin reads `response_char_count`/`response_content` → the AC1 defect is confirmed). No `role` kwarg. |
| `role` source | Not in any API/tool hook. `subagent_start` carries **`child_role`**; primary turns have no role → role = `primary` unless the session is a known subagent child. |
| Delegation parent | `tools/delegate_tool.py:1597` `subagent_start(parent_session_id, parent_turn_id, parent_subagent_id, child_session_id, child_subagent_id, child_role, child_goal)`; `:2678` `subagent_stop(…, tool_call_history)`. The plugin does not register these hooks yet. |
| Session end / finalize | Hooks `on_session_end` (plugin emits `finish_reason='end'`, carries task_id), `on_session_finalize(session_id, platform, reason)` (plugin emits finalize; `reason` e.g. shutdown/session_boundary), `on_session_reset` (`new_session`). |
| File-reading tool names | `read_file`, `search_files`, `read_terminal` (writers: `write_file`, `patch`) |
| In-memory queue bound | plugin `config.queue_max = 2048` events (`sink.py` `queue.Queue(maxsize=queue_max)`) |
| VALID_HOOKS (relevant) | pre/post_tool_call, pre/post_llm_call, pre_verify, pre/post_api_request, api_request_error, on_session_start/end/finalize/reset, subagent_start/stop, pre_gateway_dispatch, transform_* |

## Consequences for the locked plan (pending user decision — see status.md Drift Log)
1. **Task unit (stop rule triggered).** `task_id` is session-scoped on gateway/API platforms, so the backfill/ingest key `(project, task_id)` does not mean "one user prompt → final stop". The Hermes **turn** does.
2. Session phase lives in `token_events.finish_reason`, not `tags.session_phase`.
3. Complexity provenance is explicit (`tags.complexity_version`), so no `-inferred` method or `BACKFILL_INFER_RS1` flag is needed.
4. `role` cannot come from API hooks; it must come from `subagent_start.child_role` (children) or default `primary`.
5. Hierarchy is available from `subagent_start` (child session → parent session + parent turn) once the plugin registers it.
