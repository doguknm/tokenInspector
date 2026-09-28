# Claude Code producer

Sends the token usage of Claude Code sessions to Token Inspector: Windows sessions (`claude-code@windows`) and `claude -p` runs on hermes, including devir hand-off runs (`claude-code@hermes`). Stdlib only (Python 3.10+), no package install.

It runs as a Claude Code hook on `Stop`, `SubagentStop` and `SessionEnd`, reads only the new part of the session transcript, and posts one `llm_request` event per API call to `POST /api/events/batch`.

## Install / uninstall

Changing the global `~/.claude/settings.json` is a coordinated, user-approved step (SESSION-COORDINATION ledger row first).

```bash
python producers/claude_code/install.py              # dry-run: content-free summary, nothing written
python producers/claude_code/install.py --apply      # backup settings.json.bak-ti-<stamp>, then atomic write
python producers/claude_code/install.py --uninstall --apply
```

On hermes use `python3`. `--settings PATH` targets another file. The installer:

- adds exactly one entry per event and nothing else:
  `{"matcher": "", "hooks": [{"type": "command", "command": "<python> \"<abs path>/cc_hook.py\"", "timeout": 10}]}`
  with `<python>` = `python` on Windows and `python3` elsewhere;
- recognizes its own entries only by that exact command string; every other key and hook (for example PossibleSkills' PreToolUse hook) is preserved, and a mixed matcher group keeps its foreign hooks;
- is idempotent; aborts without writing if the file changed since it was read or is not valid JSON;
- takes PossibleSkills' OS lock `settings.json.lock-possibleskills` for the whole apply, so both installers never write at the same time;
- never prints the settings file, a diff or another hook's command. The backup stays local (never commit or sync it).

Global hooks take effect in running Claude Code sessions immediately, without a restart.

## Configuration

| Setting | Source (first wins) |
|---|---|
| URL | env `TOKEN_INSPECTOR_URL` → `url` in `~/.config/token-inspector/claude-code.json` → `http://127.0.0.1:8100` |
| Ingest token | env `TOKEN_INSPECTOR_API_KEY` → first line of `token_file` (default `~/.config/token-inspector/ingest-token`); none → no header |
| Project aliases | env `TOKEN_INSPECTOR_PROJECT_ALIASES` (JSON object: absolute workspace root → name) → `project_aliases` in the config file |
| Job context | `TOKEN_INSPECTOR_JOB_REF`, `TOKEN_INSPECTOR_WORK_TYPE`, `TOKEN_INSPECTOR_JOB_ATTEMPT` (set by `hermes.sh devir-baslat`; same contract and shapes as the plugin) |

On Windows the URL is the tailnet HTTPS name allowed by the backend host allowlist; keep it in env or the config file, never in repo files. The token is never logged, printed or stored in state.

Project name: alias (longest matching root) → sanitized git `origin` slug → git root folder name → `claude-code`. Names equal to the machine's hostname/FQDN or shaped like an IPv4 address are skipped.

## What is sent

Only these fields: `client_event_id`, `event_type`, `occurred_at`, `provider` (`anthropic`), `model` (`claude-…` or `unknown`), `session_id`, `turn_id`, `task_hierarchy`, `parent_session_id`, `parent_turn_id`, `parent_project_name`, `role` (`primary`/`subagent`), `prompt_tokens` (input without cache), `cache_read_tokens`, `cache_creation_tokens`, `completion_tokens`, `input_tokens_include_cache` (`false`), `status`, `error_type` (`api_error` only), `finish_reason`, `tool_call_count`, and tags `runtime`, `producer`, `project_source`, `schema`, `job_ref`, `work_type`, `job_attempt`.

Session, turn and parent ids are `sha256(raw)[:32]` pseudonyms; `client_event_id` is `cc-` + a hash of `message.id` and `requestId` (ADR-004). **Never sent:** prompt or response text (the producer never sends prompt text, under any setting), tool names, inputs or outputs, `cwd`, transcript paths, git branch, version, skill or agent names, hostnames, `error_message`, `request_tool_names`.

One user turn (the transcript `promptId`) is one task; a subagent run is a child task linked to the turn that started it.

## Behaviour and limits

- **Fail-open, bounded:** a watchdog ends the hook after 5 s (`HOOK_BOUND_S`), no POST starts after 2.5 s, each POST times out after 2 s, the installed hook `timeout` is 10 s. The hook prints nothing and always exits 0.
- **At-least-once + idempotent:** the cursor advances after each acknowledged batch, never past a streaming call whose final usage is not yet on disk. Items the backend rejects are dropped and counted.
- **State:** `%LOCALAPPDATA%\token_inspector_cc\` (Windows) or `~/.local/state/token_inspector_cc/`: one small file per transcript (offset, turn id, file identity, pending flag, project name and job keys), a lock per transcript, `counters.json` (integers only). At most 512 state files.
- **Backlog:** work left over (budget) is marked pending and drained by later hooks of the same project directory. A directory where no hook ever fires again keeps its backlog until one does.
- **Not measured:** calls that leave no usage record (API errors appear only as zero-usage `<synthetic>` lines), and an interrupted last call of a session that has no `stop_reason` until the session continues.
- A resumed copy that resolves to another project is counted once, in the project of first arrival.
