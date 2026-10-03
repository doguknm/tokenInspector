# Hand-off — goal 1 orchestration moves to an alternative model

Written 2026-10-03 by the Claude (Anthropic) session `Token-Inspector-Claude`, which stopped before CP1 on the
user's instruction.

## User decision (2026-10-03)

- **Role split:** hermes does the coding (and, in later goals, the deployment). The Claude Code session only
  orchestrates: hermes queue, peer messages, prompts, pulling and applying diffs, tests, mutation checks,
  commits, docs, and asking the user.
- **The orchestrator runs on an alternative model through free-claude-code (FCC)**, not on Anthropic Claude:
  an interactive `fcc-claude` session with model `vercel/deepseek/deepseek-v4.1-flash` (Vercel AI Gateway,
  0.3 / 1.2 USD per 1M tokens). `/alt` (alt_run.py) is not used for this role because its child has no Bash,
  no ssh, no SendMessage and cannot ask the user.
- The goal's "stop at a 95 % weekly Claude limit" rule is waived (user, 2026-10-03); it does not apply to the
  FCC session anyway.
- Everything else in `goal-1-agent-complexity.txt` and the `/goal` text stands: CP1 analysis on hermes with
  `hermes chat --provider openai-codex --model gpt-6-astra` (read-only, one run); CP2 integration with the
  hermes default model via `hermes.sh` and no model/provider; no review round; no deploy, install or restart;
  stop on HTTP 429 or a model error; queue row + peer message before and after each hermes job; replies to the
  user in Turkish; last line `Hermes kullanıldı` / `Hermes kullanılmadı`.

## Start the orchestrator

From a new terminal (PowerShell):

```powershell
cd C:\Users\Bentego_Admin\Projects\tokenInspector-agents
$env:ALT_MODEL_CHILD = "1"   # silences the weekly-Claude-limit hook, which does not apply here
fcc-claude --model vercel/deepseek/deepseek-v4.1-flash
```

Then run `/goal` with the original goal text and add: "Orchestrator = this FCC session; read
Plans\cc-agent-usage\HANDOFF-orchestrator.md first."

Probe 2026-10-03: `fcc-claude -p --model vercel/deepseek/deepseek-v4.1-flash` answered `PROBE-OK`
(exit 0). Every request carried about 29K input tokens and **no cache reads**, so each turn costs the full
context again. This session is not counted by `alt_run.py stats` or its 20 USD gate; watch the Vercel dashboard.
claude.ai connectors are off in an FCC session; local MCP servers (NotebookLM) still load.

## State at hand-off

- Branch `feat/cc-agent-usage` created at `9bdc8b5` (= `feat/task-telemetry-jev-pilot` HEAD = hermes prod
  `main`). Not pushed yet.
- Worktree `C:\Users\Bentego_Admin\Projects\tokenInspector-agents` on that branch. The goal files
  (`goal-1/2/3-*.txt`) and this file are copied in and **not committed yet** — commit them with the CP1 report.
- The main checkout `C:\Users\Bentego_Admin\Projects\tokenInspector` is LIVE (the Windows hook runs from it):
  it stays on `feat/task-telemetry-jev-pilot` at `9bdc8b5`; never switch it or edit `producers/claude_code/` there.
  It still shows `Plans/cc-agent-usage/` as untracked; leave it.
- Plugin repo `C:\Users\Bentego_Admin\Projects\token_inspector` at `7a6d094`, remote `hermes` only. Branch it
  only if the plan changes the plugin.
- hermes was idle (no `hermes -z` / `hermes chat` process). Next queue row is **#75** in
  `C:\Users\Bentego_Admin\.claude\SESSION-COORDINATION.md` (section "hermes sırası"). Live peer at hand-off:
  `PossibleSkills-EditScreens`. No row was added and no message was sent.
- Nothing ran on hermes; no hermes worktree exists for this goal.

## Facts already gathered for the CP1 prompt

- Subagent sidecar: `<session>/subagents/agent-<id>.meta.json` (not `.json`) with keys `agentType` (string,
  e.g. a built-in or a `.claude/agents/*.md` name), `description`, `toolUseId`, `spawnDepth` (int). 137 sidecars
  and 137 subagent transcripts in the Windows corpus. O9 C0 rule 2 said the sidecar is never read; the producer
  does not read `agent_type` from SubagentStop stdin either (C0 rule 1). `description` is free text: never send it.
- `request-shape-v1` (plugin `complexity.py`): points for user message length ≥ 256 / 1024 / 4096 chars,
  message count ≥ 8 / 24, approx input tokens ≥ 16 000 / 64 000; tier 1 = 0 points, 2 = 1–2, 3 = 3–4,
  4 = 5–6, 5 = 7. No content, no model call.
- The C0 corpus facts are in `Plans/o9-job-correlation-cc-producer/status.md` Drift Log (C0 rules 1–9).
  Corpus shape for thresholds must be counts only; compute it locally and paste numbers into the prompt
  rather than letting the hermes model read transcripts.

## Next step

CP1 as written in `goal-1-agent-complexity.txt`: queue row #75 + peer message → detached read-only worktrees
`/tmp/<run>/tokenInspector` (`9bdc8b5`) and `/tmp/<run>/token_inspector` (`7a6d094`) on hermes → the
`gpt-6-astra` run (setsid nohup runner, pid file, log in /tmp) → report to
`Plans/cc-agent-usage/hermes/cc-agent-complexity-plan.md` → worktrees removed → queue `bitti` + peer message →
present the open decisions to the user in Turkish → record answers in `status.md` → commit + push.
