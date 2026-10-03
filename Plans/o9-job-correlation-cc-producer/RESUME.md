# O9 — resume point (2026-09-28)

State:
- PM round 2 (`--finalize`) done → `2026-09-28-summary.md` (incl. "Open questions resolved").
- Planner done → `database.md`, `backend.md`, `frontend.md`, `tests-other.md`, `tests-e2e.md`, `status.md`.
- Hermes plan review r1 done (2 parts, 41 findings; reports in `hermes/`). All 41 applied into the lane files (32 fixed, 9 merged into their Part A twin; none left unapplied) — table in status.md `## Hermes Reviews`. No round 2.
- Plan LOCKED 2026-09-28 (`ffa5228`): PLAN_BASE `904dec2` (backend), plugin base `4b069b4`; Hermes Code Review spec in status.md. O5 resolved (TI owns the AIFromScratch `hermes.sh` and `commands/hermes.md`; ledger rows added), O13 resolved (hook bound 5 s). S1 also prints `job=<id>` (PossibleSkills request). The Windows settings file is `C:\Users\Doğukan Mutlu\.claude\settings.json`.
- Phase 1 DONE (checkpoint 2026-09-28): backend `e1cab32`, plugin `6b79ec9`, both pushed to `hermes` (backend also `origin`); suites green locally and on hermes; mutations 37/37 caught. S1/S4 edited (ledger rows); S2/S3 patches in `shared-patches/`, request row in the ledger, owners not yet applied.
- Phase 2 DONE (checkpoint 2026-09-28): C0 gate passed (rules 1–9 in the Drift Log); backend `6ec9922`, `58b9753`, `f289af4` (+ status commit), plugin `bbe0781`; pushed to `hermes`; suites green locally and on hermes; mutations CM1–CM37 37/37 caught.
- Phase 3 DONE (checkpoint 2026-10-01, goal `goal-1-phase3.txt`): flaky repair test fixed (`fdbb1e5`, 10/10 on hermes); backend `2f5a761` (v12, X1), `cf4b1bf` (X2–X5), `be86f18` (Export panel), `c4a07bc` (X6 docs) + status commit; pushed to `hermes` (`origin` unreachable); suites green locally (535 incl. 41 browser) and on hermes (478 passed, 17 skipped); mutations XM1–XM25, FM2–FM3, FM5–FM7 30/30 caught.
- Stage 2 DONE (2026-10-03, goal `goal-2-reviews.txt`): hermes code review r1 (10 parts, 32 findings: 25 fixed, 7 known limitation, 0 rejected) and the integration review (hermes, 2 MEDIUM + 4 gaps: 3 fixed, 2 known limitation, 1 Drift Log entry); mutations RM1–RM21 21/21 caught; suites green on Windows and hermes; pushed to `hermes`. Status rows: status.md `## Hermes Reviews`.
- Stage 3 DONE (2026-10-03, goal `goal-3-deploy.txt`): runbook review r1 (gpt review on hermes, 1 HIGH + 5 MEDIUM: 5 fixed in the runbook text, F5 known limitation); prod backend `main` ff to the O9 branch (schema 12, verified online backup + the app's own pre-migration backup), repair dry-run all 0; S2/S3 applied (uncommitted in the owner repos); plugin `7a6d094` + gateway restart; CC hook on Windows and hermes; AC1.2 and AC2.2 observed live, AC2.3 pending first real devir run; PossibleSkills told the export is live (O10).
- **Now:** O9 is in production. Open: O14 (`hermes -z` exit loses the last ~2 s of plugin events — needs a decision), AC2.3 at the next real devir run, O7 (watch for unpriced new Claude models), O15 (`origin` push), owners to commit S2/S3.

Decisions already made (do not ask again):
- Every lane Claude Opus 5.5, direct (frontend with `frontend-design`; tests-e2e extends the Playwright suite). Hermes plan review 1 round; hermes code review 1 round; Claude integration review (Opus).
- Research: a targeted NotebookLM query, no web research; the notebook is stale, repo files win.
- One plan, phased; commit/push checkpoint after each phase; a single prod deploy after the integration review, with user approval.
- User answers: Q1 all three `hermes.sh` copies; Q2 producer in `producers/claude_code/`; INGEST_TOKEN stays unset until the Activation Gate; O9 #4 (PA proposals view) out of scope; repair script yes (prod apply needs separate approval); jobs table in the dashboard; `/hermes` skill passes work_type; tailnet address removed from notebook-bound docs.
- `~/.claude/settings.json`: TI and PossibleSkills both merge-only; install order goes through a "settings.json hook kurulumu" row in the hermes queue; after each install, check the other side's entries survived.

Inputs: `Plans/task-telemetry-jev-pilot/o9-inputs.md`, `ps-export-contract-inputs.md`, `Projects/PossibleSkills/D8-ortak-olay-sozlesmesi-taslak.md`, `Projects/PossibleSkills/SISTEM-TASARIMI.md`.
