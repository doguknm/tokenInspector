# O9 — resume point (2026-09-28)

State:
- PM round 2 (`--finalize`) done → `2026-09-28-summary.md` (incl. "Open questions resolved").
- Planner done → `database.md`, `backend.md`, `frontend.md`, `tests-other.md`, `tests-e2e.md`, `status.md`.
- Hermes plan review r1 done (2 parts, 41 findings; reports in `hermes/`). All 41 applied into the lane files (32 fixed, 9 merged into their Part A twin; none left unapplied) — table in status.md `## Hermes Reviews`. No round 2.
- **Now:** user answers O13 (hook latency bound, default 5 s) → lock the plan → write PLAN_BASE (O11) and the Hermes Code Review spec (O12) into status.md → commit/push → Phase 1 in a subagent. S1 waits for O5 (owner of the AIFromScratch `hermes.sh`).

Decisions already made (do not ask again):
- Every lane Claude Opus 5.5, direct (frontend with `frontend-design`; tests-e2e extends the Playwright suite). Hermes plan review 1 round; hermes code review 1 round; Claude integration review (Opus).
- Research: a targeted NotebookLM query, no web research; the notebook is stale, repo files win.
- One plan, phased; commit/push checkpoint after each phase; a single prod deploy after the integration review, with user approval.
- User answers: Q1 all three `hermes.sh` copies; Q2 producer in `producers/claude_code/`; INGEST_TOKEN stays unset until the Activation Gate; O9 #4 (PA proposals view) out of scope; repair script yes (prod apply needs separate approval); jobs table in the dashboard; `/hermes` skill passes work_type; tailnet address removed from notebook-bound docs.
- `~/.claude/settings.json`: TI and PossibleSkills both merge-only; install order goes through a "settings.json hook kurulumu" row in the hermes queue; after each install, check the other side's entries survived.

Inputs: `Plans/task-telemetry-jev-pilot/o9-inputs.md`, `ps-export-contract-inputs.md`, `Projects/PossibleSkills/D8-ortak-olay-sozlesmesi-taslak.md`, `Projects/PossibleSkills/SISTEM-TASARIMI.md`.
