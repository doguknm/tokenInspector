# O9 — project-manager round 1 output (2026-09-28): TYPE: QUESTIONS

The project-manager read CLAUDE.md, AGENTS.md, o9-inputs, D8, SISTEM-TASARIMI, both status.md files, ADR-002 §6, task_store, routes/events, auth, the plugin mapping and the three hermes.sh copies. The answers go to /new-plan Step 6 (PM round 2, --finalize).

## Questions (user decision needed)
1. **Which `hermes.sh` copies change?** Job correlation needs the launcher to export `job_ref`, work type and job attempt as env vars that the plugin reads. That depends on a read-only check that `hermes -z` loads plugins in its own process.
   - PEGADocRag and PEGADocRagAgent already create job ids. The Agent copy also has `devir-baslat` (`claude -p`, `devir-…` id).
   - AIFromScratch (review-only, used by TI) creates no job id.
   - No copy knows the work type; only the /hermes skill mode does.

   Options:
   - A: all three, coordinated through SESSION-COORDINATION (PM's weak recommendation).
   - B: AIFromScratch only, plus a written env contract for the PEGA copies.
   - C: none; ship only the plugin/backend side and the env contract.
2. **Where does the Claude Code producer live?** It is installed as Stop / SubagentStop / SessionEnd hooks in the global `~/.claude/settings.json` on Windows and hermes. That file is shared, so the change goes through SESSION-COORDINATION. The producer reads the new part of the transcript and sends metadata only.

   Options:
   - A: a new standalone repo.
   - B: inside the plugin repo `token_inspector`.
   - C: inside tokenInspector under `producers/claude_code/`, stdlib-only (PM's weak recommendation).

   Also confirm: **INGEST_TOKEN is set in prod during phase 2** (service + hermes plugin config in the same deploy). This is earlier than the Activation Gate, but it turns on no capture flag. Phase 3's decision endpoint needs it too, because `require_sensitive_auth` returns 403 without it.
3. **What does TI store for a PA proposal?**
   - A (PM's weak recommendation): structured fields plus a summary of at most 500 characters:
     - PA proposal id and a reference to PA's decision log;
     - kind: archive, merge, new, update, delete or other;
     - target type: skill, agent or command;
     - target name;
     - the evidence summary (no paths);
     - the user's approve or reject, with an optional note.

     PA's script reports `applied` or `failed` back.
   - B: A plus PA's full rationale markdown.
   - C: only id, kind and target.

## Conflicts the brief will carry
- **Tag budget.** `_clean_tags` (routes/events.py:161-174) allows at most 20 keys and 512 bytes and rejects the whole event above that. The plugin's llm event already has about 12 keys (~400 B). The new keys add ~140 B. Measure the real worst case first. Fix options: raise the cap, drop keys that duplicate columns, or use first-class columns (departs from PA's "in tags").
- **Two meanings of attempt.** The tag key is `job_attempt`; `EventIn.attempt` stays the per-call retry.
- **Work types.** Use D8 §2's closed list (brainstorm, review, code, devir, k1, k2, other). Unknown values become `other`.
- **Dedup authority.** Dedup is unique per (project_name, client_event_id). Rules:
  - one counting producer per runtime (hermes-plugin for hermes-agent; the CC producer for claude-code@windows and claude-code@hermes);
  - within CC, the transcript usage record wins over hook-derived data;
  - client_event_id comes from the provider message id plus the request id, not from the session.

  Resumed sessions copy earlier messages, and one response spans several lines with the same message id (known CC behaviour, not checked here).
- **Executed tools.** Do not put executed tools into `request_tool_names` (that field means tools offered). Skill and agent names are tool args, so D8 §5 forbids sending them.
- **Cross-project child fix.**
  - The plugin sends a validated `parent_project_name`.
  - The backend falls back to the current behaviour for older plugins.
  - The re-root UPDATE no longer filters by project.
  - A child is never downgraded.

  Still open: whether existing rows get a dry-run/apply repair.
- **Review jobs land in the `hermes` project** (AIFromScratch runs `hermes -z` in $HOME). `job_ref` still gives cost per job. Cost per originating project is out of scope; it would also touch O10 risk R1.
- **Export.** It must exclude prompt text, label notes, the legacy `error_message` and tool args. `GET /api/events` returns the legacy `error_message`, so the export must not reuse it unchanged.
- **Project name case.** The backend lowercases `X-Project-Name`; keep that, and PA maps names on its side.
- **Devir clone.** Its slug is `pegadocrag`; the backend cannot recognise `-devir` (ADR-002 R2). D10 still holds because the CC producer sends no prompt text.
- **No job-duration event.** The launcher measures job time (D8 §4); a job-level event is out of scope unless added.
