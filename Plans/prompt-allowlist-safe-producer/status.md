# Status — Per-project prompt/JEV allowlist with clone path-deny, and a safe producer example (O10)

**Started**: 2026-09-27
**Last updated**: 2026-09-28 (final integration review passed; **O10 resolved**)
**Plan base commits**: backend `3f52fe1`, plugin `f19ffd2` (both on `feat/task-telemetry-jev-pilot`)
**Closes**: O10 in `Plans/task-telemetry-jev-pilot/status.md` (blocks that plan's Activation Gate). O10 is marked resolved only after the final review lane passes, including the AC13 semantic checklist; the maintenance-docs step only records "docs done, pending final review" (F17)

## Execution Plan
_Chosen at /new-plan. Every executed step is run by Claude directly — **no handoffs** (`handoffs/` is not created)._

| Step | Executor | Model | Skill / plugin | Handoff |
|---|---|---|---|---|
| database | out of scope | — | — | no |
| backend (Part A tokenInspector repo + Part B plugin repo `C:\Users\Bentego_Admin\Projects\token_inspector`) | Claude Code (direct) | Opus 5.5 | — | no |
| frontend | out of scope | — | — | no |
| tests-other (backend + plugin pytest, mutation check per gate) | Claude Code (direct) | Opus 5.5 | — | no |
| tests-e2e | out of scope | — | — | no |
| maintenance-docs (before hermes code review) | Claude Code (direct) | Opus 5.5 | — | no |
| hermes plan review | Hermes (driven by Claude) | hermes default | — | no |
| hermes code review | Hermes (driven by Claude) | hermes default | — | no — spec in `## Hermes Code Review` |
| review (final integration) | Claude Code (direct) | Opus 5.5 | — | no |

## Lane Status
_Tool / model / skill per step come from the execution plan above. Claude steps run directly (no handoff)._

| Lane | Tool (model, skill) | Status | Notes |
|---|---|---|---|
| database | out of scope | not applicable | No schema change; the skip reason uses `task_evaluations.error_type` |
| backend | Claude Code (Opus 5.5, direct) | done 2026-09-28 — Part A `efb72ef`, Part B plugin `2844f39`; pushed to `hermes` | Part A (A1–A5) then Part B (B1–B4) per backend.md. No prod flag, deploy, plugin install or gateway restart without user approval |
| frontend | out of scope | not applicable | No UI change |
| tests-other | Claude Code (Opus 5.5, direct) | done 2026-09-28 — backend tests `964d801`, plugin tests `cdc3387`; mutations 50/50 caught (see Verification Log); `test_docs_allowlist.py` deferred to maintenance-docs (Drift Log) | Written with each backend step; mutation checks PM1–PM24, BM1–BM19, SM1–SM7 with exact sites and isolated fixtures (AC18). Plugin suite runs locally **and** on hermes (temp clone of the pushed branch) |
| tests-e2e | out of scope | not applicable | |
| maintenance-docs | Claude Code (Opus 5.5, direct) | done 2026-09-28 — README, AGENTS.md, CLAUDE.md (RP 9 four gates, new RP 13 WAL residual, RP 14 allowed-path test fixtures), CHANGELOG, ARCHITECTURE.md, ADR-002 §6, pilot status.md (Activation Gate (a) item + (c)-1, O9 hand-off rule, O10 "docs done, resolution pending final review"), plugin README; `tests/test_docs_allowlist.py` green. CONTRIBUTING.md and `.github/workflows/ci.yml`: no change needed (new tests run in the existing job) | backend.md "Documentation" (AC13). Must be done before the Hermes code review |
| hermes plan review | Hermes (driven by Claude Code) | done — r1 17/17 fixed, r2 10/10 fixed; plan locked | Max 2 rounds reached; no round 3 |
| hermes-review (code) | Hermes (driven by Claude Code) | done 2026-09-28 — r1 (single round by orchestrator decision): 12 findings, all fixed (backend `fcaa317`, plugin `65104cb`) | After tests-other and maintenance-docs, before review — spec in `## Hermes Code Review` |
| review | Claude Code (Opus 5.5, direct) | done 2026-09-28 — passed: no BLOCKER/HIGH; F1 MEDIUM fixed (plugin `4b069b4`); 2 UNSURE items → Known Limitations; O10 resolved | Run last — full integration review (AC → test map, cross-repo contracts, `ac13_semantic_checklist`, `runbook_review`); on pass, marks O10 resolved |

## Drift Log
_Append here when any lane discovers the spec is wrong. Do not edit lane files mid-flight._

- 2026-09-27 — **Plan locked** after Hermes plan review r2 (root-only simplification applied to the summary, backend.md and tests-other.md). No drift recorded yet; from now on every spec change goes through this log, not through a lane-file edit.
- 2026-09-28 — README: only the two A5 code blocks (`push_token_event`, "Batch ingest") changed in Part A (orchestrator-approved scope); every other README change stays in the maintenance-docs step.
- 2026-09-28 — `tests/test_docs_allowlist.py` (AC13 literal check) was written in the maintenance-docs step (it can only pass after it), not with the other tests. CHANGELOG uses the heading `## Unreleased` (no brackets); the test reads that section.
- 2026-09-28 — BM9 (JEV entry gate, planned as *redundant*) was **caught**, not survived: the entry gate decides the reported reason (`project_not_allowed` before `already_scored`) and `test_pre_attempt_gate_is_skipped` isolates the loop gate by opening the first gate call. Recorded as caught.
- 2026-09-28 — Child-transition null (A2.7): the nulled row is overwritten in place with `secure_delete`, but the earlier WAL frame that held the prompt stays until the next checkpoint (SQLite auto-checkpoint or the retention loop's TRUNCATE). `test_prompt_nulled_when_task_becomes_child` checkpoints before its DB/WAL byte assertion. Same class as the R7 local residual; flagged for the code review.
- 2026-09-28 — F14 nuance: `GET /api/tasks?allowed_only=true` binds the allowlist names as SQL parameters, so they appear in the **aiosqlite DEBUG** driver log (off in production; the same driver logs every SQL parameter at DEBUG). Application logs never carry them; `test_allowlist_config_boundary` filters driver loggers. Flagged for the code review.
- 2026-09-28 — Safe client: a dict that already carries the backend alias `event_id` keeps it (no generated `client_event_id` that would override the alias). Small addition to A5.5 ("a caller-provided id is never changed").
- 2026-09-28 — Test layout: the plugin probe test `test_probe_not_ready_when_backend_reports_no_allowed_projects` lives in plugin `tests/test_prompt_allowlist.py` (not `test_capture_0c.py`); `test_session_discriminator_fixed_categories` is split into `..._fixed_categories` and `..._free_text_becomes_other`. `_hooks` also defaults `auto_project=False` and `emit_session_events=False` so the event lists stay deterministic.
- 2026-09-28 — code-r1 P1 F1: JEV now also requires a completed turn (new skip reason `task_not_completed`), extending AC8. Decided by the orchestrator on Hermes' recommendation; recorded here because AC8 named only `project_not_allowed`.
- 2026-09-28 — code-r1 P3 F2: the plugin's revocation set is no longer a `BoundedTTLMap` (B3.7 said 2048 / 86400 s); it is a plain set kept for the process lifetime, and overflow switches to strip-everything.
- 2026-09-28 — Hermes runs: backend suite ran in a temp worktree with the existing `~/Projects/tokenInspector/.venv` (Python 3.12.3), the plugin suite with the same venv **and** with the Hermes runtime venv (Python 3.11.15), instead of a fresh 3.13 venv. The browser suite is skipped on hermes (no Playwright there).

## Hermes Reviews
_Max 2 rounds per deliverable (plan, code). No round 3. Reports: Plans/prompt-allowlist-safe-producer/hermes/._

| Deliverable | Round | Date | Findings | Fixed | Known limitation | Rejected |
|---|---|---|---|---|---|---|
| plan | r1 | 2026-09-27 | 17 (2 BLOCKER, 10 HIGH, 5 MEDIUM) | 17 | 0 | 0 |
| plan | r2 | 2026-09-27 | 10 (1 BLOCKER, 6 HIGH, 3 MEDIUM) | 10 (5 by root-only simplification) | 0 | 0 |
| code | r1 | 2026-09-28 | 12 (4 HIGH, 8 MEDIUM) in 3 parts | 12 | 0 | 0 |

- plan r1: raw report `hermes/prompt-allowlist-safe-producer-plan-r1.md`; user decisions and fixes `hermes/plan-r1-triage.md` (all 17 fixed in the summary, backend.md and tests-other.md).
- plan r2: raw report `hermes/prompt-allowlist-safe-producer-plan-r2.md`; user decisions `hermes/plan-r2-triage.md`. Design simplification: prompts only for proven root tasks, child sessions never carry one (resolves F1, F3, F4, F5, F7 at the root); F2, F6, F8, F9, F10 fixed directly; F6 clarified with the user (proven root allows `root_task_ref` NULL or equal to its own id). Where each lands: F1 → AC7/AC16, backend.md A2.7 + B3.7; F2 → AC17, B3.5–B3.6; F3/F4/F5/F7 → AC4/AC7 "resolved by root-only rule" notes; F6 → AC7/AC8, A2.2 + A3.1; F8 → AC8, A3.2; F9 → AC18, tests-other `## Mutation checks`; F10 → AC9, A1.1.
- Plan locked after r2 — no round 3; verified by tests and mutation checks.
- Final integration review (Claude, 2026-09-28): AC → test map, cross-repo contracts, `ac13_semantic_checklist` and `runbook_review` passed. No BLOCKER or HIGH.
  - F1 MEDIUM `subagent_start` revoked every child, so the 2049th ordinary subagent without a gateway restart switched the sink to strip-every-prompt for good (fail-closed, but pilot data loss) — fixed `4b069b4`: only a child with root evidence or an attached prompt (`_prompt_sent`) is revoked; the first overflow logs one fixed warning (no names, no counts) and increments `revocation_overflows`; plugin README and CLAUDE.md RP 9 say to restart the gateway. Tests: 2049 prompt-less subagents leave root prompts flowing; a session that attached a prompt is revoked even without root evidence; warning once without names. Mutations IM1–IM3 caught.
  - UNSURE items → Known Limitations (below).
- code r1: raw reports `hermes/prompt-allowlist-safe-producer-code-r1-p1.md` (backend code + docs), `-p2.md` (backend tests), `-p3.md` (plugin). Orchestrator decision: Hermes' recommendations take priority, all 12 applied, **no round 2**; each fix verified by a test and, where it adds a control, by a mutation (Verification Log).
  - P1 F1 HIGH late-child JEV send window — fixed `fcaa317`: JEV scores only completed turns (`session_end`/`next_task`/inferred after 60 min), checked with the proven-root rule before every attempt (`task_not_completed`); ADR-002 §6 explains why completion closes the window and R7 no longer claims the root check alone prevents a send.
  - P1 F2 MEDIUM gate after retention check — fixed `fcaa317`: `project_gate` runs before the per-attempt retention checks; test with a real ingest child transition between attempts.
  - P1 F3 MEDIUM enqueue validation ≠ wire encoding — fixed `fcaa317`: `allow_nan=False` + UTF-8 like httpx; invalid events are `serialization_failed`, valid neighbours are sent.
  - P1 F4 MEDIUM README batch example — fixed `fcaa317`: `post_batch()` counts rejected as lost, malformed ack as unconfirmed, non-2xx as lost; exec-tested; the AC12 helper test stays green.
  - P2 F1 MEDIUM dataclass `task_prompt_text` — fixed `fcaa317`: refusal asserted, plus a subclass that re-adds the fields is stripped.
  - P2 F2 MEDIUM budget ledger not asserted — fixed `fcaa317`: zero-attempt skips assert no attempts and zero spend; one-attempt stops assert one completed attempt, matching spend and `http_attempts`/`cost_usd`.
  - P2 F3 MEDIUM fallback without control — fixed `fcaa317`: a no-change case with the same 429 reaches digitalocean and succeeds.
  - P2 F4 MEDIUM marker only via `/api/events` — fixed `fcaa317`: missing/invalid-marker and old-plugin cases run on both endpoints with DB/WAL absence and a marked control.
  - P3 F1 HIGH revocation checked once per batch — fixed `65104cb`: re-read in `_post` before every HTTP call and before `_keep`/dead-letter; tests revoke B during A's POST and during a failing POST.
  - P3 F2 HIGH bounded revocation evidence — fixed `65104cb`: revoked ids kept for the process lifetime (no TTL/eviction); overflow beyond 2048 sets fail-closed mode that strips every prompt in memory, queue and spool; overflow test.
  - P3 F3 HIGH denial evicted independently of root — fixed `65104cb`: denial pops the root evidence and `on_session_start` does not re-root a denied session; independent-map eviction test.
  - P3 F4 MEDIUM legacy replay sends `error_message` — fixed `65104cb`: removed at the common transmission boundary (`_post`), replay included; legacy envelope test.

## Hermes Code Review
**Hermes code review** of **Per-project prompt/JEV allowlist with clone path-deny, and a safe producer example (O10)**. Run it after the implementation lanes. Hermes reviews; Claude drives the review, triages every finding with the user and applies only the approved fixes. Hermes writes no code and changes no file.

### Content to review
- Backend diff: `git diff 86392c4 -- . ':!Plans'` in `Projects/tokenInspector` (committed + uncommitted work since the plan was locked), plus the full text of new untracked files (`git ls-files --others --exclude-standard`, excluding `Plans/`).
- Plugin diff: `git diff f19ffd2` in `Projects/token_inspector`, plus its new untracked files.
- Read at run time, so drift is included: `Plans/prompt-allowlist-safe-producer/2026-09-27-summary.md`, the **Acceptance Criteria** section of every lane file, and from this status.md the Drift Log and Known Limitations (so Hermes does not re-raise accepted items).
- If one prompt exceeds 100 000 bytes, split it by repo (backend / plugin) and send the parts one after another; together they are one round.

### Review focus (the prompt's TASK)
- Correctness bugs with a concrete failing scenario.
- Every AC implemented and covered by a test.
- Security: injection, authz, secrets in logs or model-visible arguments, and any path by which prompt text is stored, spooled, replayed or sent to JEV for a non-allowed project, a denied path or a child.
- Error paths.
- Contract mismatches between the plugin and the backend.
- Behaviour that deviates from the lane specs without a Drift Log entry.

### Protocol
Used by Step 7a (plan review), by status.md `## Hermes Code Review` (code review) and, for its
script/run/error rules, by `/new-idea` Step 2b. The rules come from global CLAUDE.md →
**Review Rounds** and **Hermes**; none of them is optional.

1. **Script.** `SCRIPT = scripts/hermes.sh` if the project has it. Otherwise use the
   review-only copy `C:/Users/Bentego_Admin/Projects/AIFromScratch/scripts/hermes.sh` (it runs
   in `$HOME` on hermes and touches no repo). If neither exists, skip the hermes step, write
   `Hermes review skipped — no scripts/hermes.sh` under status.md `## Hermes Reviews`, and
   continue. Always run it through the Bash tool as `bash "$SCRIPT" …`. Never hand-roll ssh:
   Git Bash's own ssh fails on this machine with `Host key verification failed`; the script
   already switches to Windows OpenSSH (`/c/WINDOWS/System32/OpenSSH/ssh.exe`).
2. **Never name a model or provider.** No 2nd/3rd argument to `send`, no `HERMES_MODEL` or
   `HERMES_PROVIDER`. The default configured on hermes is used; the user maintains it there.
3. **Prompt file.** Write it to a temp file outside the repo. Hermes sees none of this
   conversation and none of the project files, so the prompt is self-contained:

   ```
   ROLE: You are reviewing the <deliverable> of <feature/project>. READ-ONLY review task.
   RULES (non-negotiable):
   - Do NOT create, modify or delete any file. Do NOT commit, push or run any command
     that changes state. Only read and report.
   - This machine cannot reach PEGA (its hosts do not resolve in DNS). Do not attempt any
     live verification or call to it; never report a result you did not observe.
   - Base the review only on the content below. Any repository copy on this machine may be
     out of date.
   - If you cannot determine something, list it under UNSURE — do not guess.
   TASK: <review focus>
   CONTENT:
   <files / diff, each under "### <repo-relative path>">
   PRIOR ROUND (round 2 only): <round-1 findings, each with the user's decision: fixed /
   known limitation / rejected + reason>. Do not re-raise known limitations or rejected
   items unless the new content changes them.
   REPORT (max 40 lines, exactly this shape):
   FINDINGS:
   F1 [BLOCKER|HIGH|MEDIUM|LOW] <path or section> — <problem> — scenario: <concrete
      input/state → wrong result> — fix: <suggestion>
   UNSURE:
   - <what you could not determine>
   VERDICT: <one line>
   ```

   Check the size with `wc -c`: keep each prompt ≤ 100 000 bytes — the script passes it as a
   single argument and Linux caps one argument at 128 KB. If it is larger, split by lane or
   directory into several prompts sent one after another; together they are one round.
4. **Run.**
   ```bash
   HERMES_TAG=<TAG> bash "$SCRIPT" send <prompt-file>   # note job=<id> if it prints one
   HERMES_TAG=<TAG> bash "$SCRIPT" wait [<id>]          # with run_in_background — do not poll
   HERMES_TAG=<TAG> bash "$SCRIPT" result [<id>]
   ```
   Hermes runs one job at a time: before `send`, follow the single-job queue rule of the
   `/hermes` skill (the "hermes sırası" table in `C:\Users\Bentego_Admin\.claude\SESSION-COORDINATION.md`
   plus a message to the other session; mark your row done and notify them afterwards).
   If `send` refuses because a run is already active, wait for it — it may belong to another
   session; never kill it. Save the report part (FINDINGS / UNSURE / VERDICT) to
   `Plans/<slug>/hermes/<TAG>.md`.
5. **Model/provider or run errors.** If the output shows `No LLM provider configured`,
   `does not support thinking`, any other model/provider error, or `__EXIT__` non-zero with no
   report: stop, show the user the error text, and do not retry with another model or
   provider. The failed run does not count as a round; resume only when the user says so.
6. **Triage — every finding, BLOCKER included.** Nothing is fixed without the user's approval.
   Present each finding in plain language: what is wrong, a concrete scenario, and the fix
   options (including "don't fix"). The user decides:
   - **fix** → Claude applies it. Hermes never writes the fix.
   - **don't fix** → one line under status.md `## Known Limitations`:
     `<plan|code>-review r<N> F<k>: <what> — <why accepted>`. Never dropped silently.
   - **reject** (finding is wrong) → recorded with the reason in the round's row.
   Log the round in status.md `## Hermes Reviews`.
7. **Rounds.** At most 2 per deliverable; the plan and the code are separate deliverables.
   Round 2 reviews the updated content with the round-1 decisions attached. After round 2:
   triage, apply the approved fixes, move on. **There is no round 3** — not even to verify
   round-2 fixes; verify code fixes with tests (mutation checks where a control is involved).
   If a round-2 finding looks severe enough to need re-review, say so and let the user
   decide; never start round 3 on your own.
8. **Usage line.** Every response while this command runs states on its own line
   `Hermes kullanıldı` or `Hermes kullanılmadı`.

### When done
Set the `hermes-review` row in this status.md to done, then run the Claude integration review.

## Upgrade / Rollback Runbook (F16, AC15)
_Owner: Claude executes; the user approves each deploy, install and restart. No schema migration, no automatic purge of retained tasks._

Upgrade (after the review lane):
1. Preflight: all capture/JEV flags off in prod (`STORE_TASK_PROMPTS`, `JEV_ENABLED`, plugin `capture_task_prompt`); note both commits.
2. **Backend first:** deploy, restart `token-inspector.service`, check `/api/meta` (`task_prompt_allowlist: "empty"`, `no_allowed_projects` in `config_errors`). From here the marker gate (AC14) stops any old plugin or legacy spool from storing a prompt.
3. **Plugin second:** confirm no agent job is running (no active delegated or JEV run; `evaluator_runs` has no `queued`/`running`), install the plugin, `hermes gateway restart`. Pre-upgrade spool files replay without prompts (AC14).
4. Post-check: backend journal clean; one metadata event arrives; Verification Log entry.

Rollback (either repo):
1. **Always first:** turn every capture/JEV flag off (backend env + plugin config) and restart, so older code that ignores allowlists never runs with capture on.
2. Then restore the previous commit(s): plugin before backend if both. Stored tasks and prompts stay; they expire through the normal 30-day retention.
3. Re-enabling capture after a rollback goes through the Activation Gate again.

## Known Limitations
_Review findings the user chose not to fix — one line each: `<plan|code>-review r<N> F<k>: <what> — <why accepted>`._

- integration-review UNSURE 1: plugin `error_type` depends on what the Hermes hook passes (`llm_error` falls back to `APIError`; `tool_event` emits one only when the hook passes `error_type`), and whether Hermes passes exception class names was not verified — accepted: the identifier filter (`[A-Za-z0-9_.]{1,64}`, else `other`) bounds any value to an identifier, never free text; the identifier-shaped residual is R6.
- integration-review UNSURE 2: a late child whose prompt was already stored under an earlier turn (different `turn_id`) is nulled only when an event of *that* turn carries the hierarchy fields; a later turn becomes `child` on its own row, so the earlier root row keeps its prompt — accepted: on hermes `subagent_start` fires while the child agent is built, before the child's first turn (Verification Log, B3.3), so a session cannot attach a root prompt before it is named a child; the plugin still strips every queued/spooled prompt of the revoked session, whatever its turn. Residual of R7 class; a fix would need a session-wide backend null (a later plan if hook order ever changes).

## Open Items
| # | Item | Owner | Blocks |
|---|---|---|---|
| P1 | **Known defect (Q1, not fixed here):** `task_store._upsert_task` builds `parent_task_ref` from the child event's own `project_name`, so cross-project children get a wrong `parent_task_ref` and, under this plan, never store a prompt (fail-closed, R5). Logged in AGENTS.md Gotchas by maintenance-docs; fix belongs to a later plan | TI | — |
| P2 | Prod deploy of backend + plugin after the review lane (all flags stay off; expect `/api/meta` `task_prompt_allowlist: "empty"` and `no_allowed_projects` in `config_errors`) | User approves; Claude executes | Activation Gate phase (a) |
| P3 | Setting the real allowlists (backend env + plugin config) and reviewing deny globs is an Activation Gate phase (a) item for the user, not part of this plan | User | Activation Gate |

## Verification Log
| Date | Check | Result |
|---|---|---|
| 2026-09-28 | B3.3 hook order on hermes (read-only, `~/.hermes/hermes-agent` @ `5646fed`) | `on_session_start` fires once for a brand-new session in `agent/conversation_loop.py` (first-turn system-prompt build, not on continuation) before the first API call; `subagent_start` fires in `tools/delegate_tool.py` while the child agent is built, i.e. **before** the child's own first turn (whose `on_session_start` then finds it in `_subagent_state` and records no root). Continuing sessions after a gateway restart get no `on_session_start` → `unknown` → no prompt (R7, fail-closed) |
| 2026-09-28 | Backend local (Windows) | `python -m pytest -q` → **262 passed** (incl. browser 16; baseline 208); `py_compile *.py routes/*.py scripts/*.py` ok; `node --check static/app.js` ok; `git diff --check` clean |
| 2026-09-28 | Backend on hermes (temp worktree `/tmp/o10-test` @ `964d801`, removed after) | **246 passed, 1 skipped** (browser module skipped: no Playwright) |
| 2026-09-28 | Plugin local (Windows) | **151 passed, 2 skipped** (POSIX permissions; `os.symlink` unavailable) — baseline 92 |
| 2026-09-28 | Plugin on hermes (temp worktree @ `cdc3387`, removed after) | Python 3.12.3: **153 passed**; Hermes runtime Python 3.11.15: **153 passed** (symlink test runs there) |
| 2026-09-28 | Existing tests touched (fixture only, no assertion relaxed) | Backend: `conftest.py` (pops `TASK_PROMPT_ALLOWED_PROJECTS`), `test_security_meta.py` (valid config lists `hermes`; preflight body gains the new key `task_prompt_allowlist: "configured"` — the only changed expectation; the flags-off error test sets the list so its exact set stays), `test_retention.py` (`_state` lists `hermes`), `test_task_ingest.py` + `test_review_r1.py` (capture on lists `hermes`, prompt events carry `task_hierarchy="root"` + marker; JEV rows are `root`), `test_jev_worker.py` (list `hermes`, rows `root`). Plugin: `test_capture_0c.py` (`_hooks` with allowlist/deny/cwd stub/registry reset, `_start`, `_child`; the 0c prompt test calls `_start`), `test_hooks_sink.py` + `test_response_mapping.py` (sink doubles accept `prompt_authorized`), `test_review_r1.py` + `test_spool.py` (listed project, marker / authorization) |
| 2026-09-28 | Mutation checks (AC18), one at a time, file restored after each | **50/50 caught** — see table below |
| 2026-09-28 | maintenance-docs: local suites after the docs commit | backend **263 passed** (incl. `test_docs_allowlist.py` and browser 16); plugin **151 passed, 2 skipped**; py_compile / node --check / git diff --check clean |
| 2026-09-28 | code-r1 fixes: local | backend **282 passed** (incl. browser 16); plugin **157 passed, 2 skipped**; py_compile / node --check / git diff --check clean |
| 2026-09-28 | code-r1 fixes: hermes (temp worktrees @ `fcaa317` / `65104cb`, removed; prod checkouts stay on `main`) | backend **266 passed, 1 skipped** (browser module); plugin **159 passed** on Python 3.12.3 and on the Hermes runtime Python 3.11.15 |
| 2026-09-28 | Integration review F1 (`4b069b4`) | plugin local **160 passed, 2 skipped**; hermes **162 passed** on Python 3.12.3 and 3.11.15; backend local **282 passed**. Mutations: IM1 unconditional revoke → `test_promptless_subagents_never_exhaust_the_revocation_set` red; IM2 attached prompt not counted → `test_session_that_attached_a_prompt_is_revoked_even_without_root_evidence` red; IM3 warning on every overflow → `test_revocation_overflow_warns_once_without_names_or_counts` red — **3/3 caught** |
| 2026-09-28 | code-r1 fix mutations, one at a time, file restored after each | **15/15 caught** (RM6 first run was an equivalent mutation — fallback still reached through the cooldown branch — so RM6b disables both fallback paths) — table below |

Code-r1 fix mutations:

| # | Finding | Mutation | Result | Failing test(s) |
|---|---|---|---|---|
| RM1 | P1 F1 | `project_gate`: open turn returns None | caught | `test_only_completed_turns_are_scored` (open), `test_late_child_is_classified_before_the_turn_completes` |
| RM2 | P1 F2 | gate moved back after the retention check | caught | `test_child_transition_between_attempts_reports_project_not_allowed` |
| RM3 | P1 F3 | enqueue check back to plain `json.dumps` | caught | `test_wire_invalid_events_never_poison_a_batch` |
| RM4 | P1 F4 | README: rejected counted as delivered | caught | `test_readme_batch_example_accounts_for_every_event` (2) |
| RM5 | P1 F4 | README: malformed ack counted as delivered | caught | `test_readme_batch_example_accounts_for_every_event` (2) |
| RM6 | P2 F3 | 429 branch keeps typesafe-ai | survived — equivalent (the cooldown branch still falls back) | — |
| RM6b | P2 F3 | fallback disabled on both paths | caught | `test_gate_rechecked_before_fallback` (incl. the no-change control) |
| RM7 | P2 F2 | 5xx attempt never completed (left `reserved`) | caught | `test_gate_rechecked_before_every_attempt` (remove, child) |
| RM8 | P2 F4 | marker check removed (both endpoints) | caught | `test_prompt_without_marker_is_not_stored` (6), `test_old_plugin_payload_cannot_store_prompt` (2) |
| QM1 | P3 F1 | no re-check in `_post` | caught | `test_revocation_rechecked_before_each_http_call` |
| QM2 | P3 F1 | `_keep` without re-check | caught | `test_revocation_rechecked_before_retaining_a_failed_delivery[down]` |
| QM3 | P3 F1 | dead-letter without re-check | caught | `test_revocation_rechecked_before_retaining_a_failed_delivery[reject]` |
| QM4 | P3 F2 | overflow does not set fail-closed mode | caught | `test_revocation_overflow_fails_closed` |
| QM5 | P3 F3 | denial keeps root evidence | caught | `test_denied_eviction_never_restores_root` |
| QM6 | P3 F4 | `_NEVER_SENT = ()` | caught | `test_legacy_error_message_is_never_transmitted` |

Mutation results (backend and safe client local; plugin local **and** on hermes, where PM9's symlink test runs):

| # | Result | Failing test(s) |
|---|---|---|
| BM1 | caught | `test_not_listed_project_prompt_is_stripped`, `test_worker_skips_not_allowed_project_without_provider_call` |
| BM2 | caught | `test_prompt_without_marker_is_not_stored` (3), `test_old_plugin_payload_cannot_store_prompt` |
| BM3 | caught | `test_child_never_stores_prompt`, `test_worker_skips_non_root_tasks` |
| BM4 | caught | `test_proven_root_required` |
| BM5 | caught | `test_proven_root_required`, `test_worker_skips_non_root_tasks` |
| BM6 | caught | `test_prompt_nulled_when_task_becomes_child` (both variants) |
| BM7 | caught | `test_gate_rechecked_before_every_attempt` (remove, child), `test_pre_attempt_gate_is_skipped` |
| BM8 | caught | `test_gate_rechecked_before_every_attempt` (remove, child) |
| BM9 | caught (planned redundant) | `test_pre_attempt_gate_is_skipped`, `test_removing_project_blocks_stored_tasks_without_purge` — see Drift Log |
| BM10 | caught | `test_evaluate_endpoint_reports_project_not_allowed` |
| BM11 | caught | `test_empty_allowlist_disables_capture_with_reason` |
| BM12 | caught | `test_empty_allowlist_contract_by_flags` |
| BM13 | caught | `test_invalid_entries_dropped_without_count`, `test_allowlist_config_boundary` |
| BM14 | caught | `test_meta_reports_allowlist_state_without_names` |
| BM15 | caught | `test_tasks_allowed_only_filters_and_needs_auth` |
| BM16 | caught | `test_tasks_allowed_only_filters_and_needs_auth` |
| BM17 | caught | `test_select_requests_allowed_only` |
| BM18 | caught | `test_validation_errors_never_echo_prompt` |
| BM19 | caught | `test_validation_errors_never_echo_prompt` |
| SM1 | caught | `test_loss_counters_sum_to_every_lost_event` |
| SM2 | caught | `test_ack_validator_rejects_impossible_acks` (4 cases) |
| SM3 | caught | `test_sensitive_fields_refused_or_stripped` |
| SM4 | caught | `test_client_event_id_is_stable_uuid4` |
| SM5 | caught | `test_idless_dict_resent_inserts_once` |
| SM6 | caught | `test_close_cancels_and_counts` |
| SM7 | caught | `test_readme_helper_contract` |
| PM1 | caught | `test_allowed_project_carries_prompt_for_every_source` (7 unlisted cases) |
| PM2 | caught | `test_empty_allowlist_sends_no_prompt_anywhere` (4) |
| PM3 | caught | `test_child_never_carries_prompt`, `test_lineage_requires_positive_evidence` |
| PM4 | caught | `test_lineage_requires_positive_evidence` |
| PM5 | caught | `test_child_never_carries_prompt` |
| PM6 | caught | `test_reset_session_id_reuse_is_unknown` |
| PM7 | caught | `test_devir_clone_is_denied_even_when_slug_allowed` (root, subdir) |
| PM8 | caught | `test_devir_clone_is_denied_even_when_slug_allowed` (subdir) |
| PM9 | caught on hermes (survives on Windows only because the symlink test skips there) | `test_symlinked_paths_match_after_resolve` |
| PM10 | caught | `test_empty_or_failing_candidates_deny_through_hooks` (empty, agent_import, getcwd, resolve; the `expanduser` case is denied inside `path_denied` itself) |
| PM11 | caught | `test_denied_is_sticky_for_the_session` |
| PM12 | caught | `test_emit_requires_attach_authorization` |
| PM13 | caught | `test_emit_requires_attach_authorization` |
| PM14 | caught | `test_emit_gate_strips_prompt_of_unlisted_project` |
| PM15 | caught | `test_late_subagent_start_strips_pending_and_spooled_prompt` |
| PM16 | caught | `test_late_subagent_start_strips_pending_and_spooled_prompt` |
| PM17 | caught | `test_late_subagent_start_strips_pending_and_spooled_prompt` |
| PM18 | caught | `test_replay_strips_revoked_session` |
| PM19 | caught | `test_replay_strips_prompt_of_no_longer_allowed_project` (removed) |
| PM20 | caught | `test_legacy_spool_without_marker_is_stripped` |
| PM21 | caught | `test_allowlist_entries_are_strictly_normalized` |
| PM22 | caught | `test_llm_error_has_no_error_message`, `test_no_error_message_on_any_event_path` |
| PM23 | caught | `test_error_type_is_identifier_or_other` (3) |
| PM24 | caught | `test_session_discriminator_free_text_becomes_other` (2) |
