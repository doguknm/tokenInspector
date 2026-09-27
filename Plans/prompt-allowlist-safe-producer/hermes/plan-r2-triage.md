# Plan review r2 — triage (user decision 2026-09-27: simplify + fix the rest; no round 3)

Raw findings: `prompt-allowlist-safe-producer-plan-r2.md`. Q1–Q5, R1 and R7 are unchanged.

## Design simplification (resolves F1, F3, F4, F5, F7 at the root)
**Prompts are captured and stored only for root tasks. Child (subagent) sessions never carry a prompt.** The JEV pilot already samples root tasks only. Child metadata (tokens, cost, hierarchy) is unchanged.
- Plugin: a prompt is attached only when the session is positively classified as root (its `on_session_start` was seen in this process and it has no `subagent_start` registration), its project is allowlisted, and its own resolved workspace is not path-denied. Remove the ancestor-chain walk, the cached parent decisions, the generation tracking and the depth limit for children. Children: always no prompt.
- Late child classification (F1): when `subagent_start` arrives for a session that already attached a prompt:
  1. the plugin strips `task_prompt_text` from that session's pending queue items and spool files (local rewrite; no path in telemetry);
  2. the plugin emits the hierarchy fields on the child's next event as today;
  3. the backend nulls the stored prompt of any task that becomes `child` (purge path: `prompt_purged_at`, secure_delete) and skips it in JEV. Children never store a prompt at ingest.
- Backend ancestry: replace the transitive walk with "store/score only if the task is a proven root" (see F6) and its project is allowlisted.
- Accepted residual: a prompt that reached the local spool/DB before the late `subagent_start` exists locally until the rewrite/purge runs. hermes is local, per the user's decision; it is never sent to JEV because of the child skip.

| F | Decision | Fix |
|---|---|---|
| F1 | fix via simplification | as above; test `subagent_start` after the first call, for pending, spooled and stored prompts |
| F2 | fix | `Sink.emit` keeps `task_prompt_text` and stamps the eligibility marker only when an internal attach authorization (set by the root attach decision, not settable from event fields) is present. Otherwise it strips. Test direct emit for denied, unknown and child sessions. |
| F3 | resolved by simplification | there are no child prompts, so there is no pending child prompt to revoke. Replay still strips a prompt without the marker and re-checks project membership. |
| F4 | resolved by simplification | only the root's own workspace matters and it is checked at attach. Document that a root whose cwd later moves into a denied dir keeps its already-attached turn prompt; later turns are re-checked per attach. |
| F5 | resolved by simplification | no parent-decision lookup for children. |
| F6 | fix | a proven root needs `hierarchy_status='root'`, `parent_task_ref IS NULL` and `root_task_ref IS NULL OR root_task_ref = id`. Clarification 2026-09-27: the code stores roots with `root_task_ref = NULL`, so `= id` alone would reject every root. The rule is "never points to another task", and the data model is unchanged. Otherwise no prompt is stored and JEV skips it. Test ingest + JEV, including a root whose `root_task_ref` points to another id. |
| F7 | resolved by simplification | any transition to `child` nulls the prompt; no descendant walk is needed. |
| F8 | fix | qualify AC8: before the first provider attempt → `skipped`/`project_not_allowed`; after an attempt started → `error` with the same error_type. Align spec and tests. |
| F9 | fix | rewrite the mutation list for the simplified design with exact mutation sites and an isolated fixture per gate. Mark redundant protections as such. |
| F10 | fix | the startup warning for invalid allowlist entries carries no count and no names ("invalid allowlist entries ignored"). |
