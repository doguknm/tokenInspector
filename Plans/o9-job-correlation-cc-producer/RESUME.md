# O9 — resume point (2026-09-28)

State:
- PM round 2 (`--finalize`) done → `2026-09-28-summary.md` (incl. "Open questions resolved").
- Planner done → `database.md`, `backend.md`, `frontend.md`, `tests-other.md`, `tests-e2e.md`, `status.md`.
- Hermes plan review r1 done (2 parts, 41 findings; reports in `hermes/`). All 41 applied into the lane files (32 fixed, 9 merged into their Part A twin; none left unapplied) — table in status.md `## Hermes Reviews`. No round 2.
- Plan LOCKED 2026-09-28 (`ffa5228`): PLAN_BASE `904dec2` (backend), plugin base `4b069b4`; Hermes Code Review spec in status.md. O5 resolved (TI owns the AIFromScratch `hermes.sh` and `commands/hermes.md`; ledger rows added), O13 resolved (hook bound 5 s). S1 also prints `job=<id>` (PossibleSkills request). The Windows settings file is `C:\Users\Doğukan Mutlu\.claude\settings.json`.
- Phase 1 DONE (checkpoint 2026-09-28): backend `e1cab32`, plugin `6b79ec9`, both pushed to `hermes` (backend also `origin`); suites green locally and on hermes; mutations 37/37 caught. S1/S4 edited (ledger rows); S2/S3 patches in `shared-patches/`, request row in the ledger, owners not yet applied.
- **Now:** Phase 2 (Claude Code producer + dedup authority) in an Opus subagent, starting with the C0 / AC2.1 gate (stop and ask when evidence is missing or contradicts an AC). The C9 installer takes PossibleSkills' `settings.json.lock-possibleskills` lock (Drift Log). Parallel-session coordination: a `koordinator` round every 20 min (user request).

Decisions already made (do not ask again):
- Every lane Claude Opus 5.5, direct (frontend with `frontend-design`; tests-e2e extends the Playwright suite). Hermes plan review 1 round; hermes code review 1 round; Claude integration review (Opus).
- Research: a targeted NotebookLM query, no web research; the notebook is stale, repo files win.
- One plan, phased; commit/push checkpoint after each phase; a single prod deploy after the integration review, with user approval.
- User answers: Q1 all three `hermes.sh` copies; Q2 producer in `producers/claude_code/`; INGEST_TOKEN stays unset until the Activation Gate; O9 #4 (PA proposals view) out of scope; repair script yes (prod apply needs separate approval); jobs table in the dashboard; `/hermes` skill passes work_type; tailnet address removed from notebook-bound docs.
- `~/.claude/settings.json`: TI and PossibleSkills both merge-only; install order goes through a "settings.json hook kurulumu" row in the hermes queue; after each install, check the other side's entries survived.

Inputs: `Plans/task-telemetry-jev-pilot/o9-inputs.md`, `ps-export-contract-inputs.md`, `Projects/PossibleSkills/D8-ortak-olay-sozlesmesi-taslak.md`, `Projects/PossibleSkills/SISTEM-TASARIMI.md`.
