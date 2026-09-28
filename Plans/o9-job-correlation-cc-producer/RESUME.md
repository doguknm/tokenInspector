# O9 — resume point (2026-09-28)

State:
- PM round 2 (`--finalize`) done → `2026-09-28-summary.md` (incl. "Open questions resolved").
- Planner done → `database.md`, `backend.md`, `frontend.md`, `tests-other.md`, `tests-e2e.md`, `status.md`.
- **Now:** hermes plan review r1 (one round, 2 parts: A = summary + database + backend, B = summary + frontend + tests-other + tests-e2e + status). Tags `o9-job-correlation-cc-producer-plan-r1-A` / `-B`; hermes queue row 15. Reports go to `hermes/<tag>.md`.
- Next: apply findings (ask the user only for ones that conflict with a locked decision, open prod/flags, or grow scope) → lock the plan → write PLAN_BASE and the Hermes Code Review spec into status.md → commit/push → Phase 1 in a subagent.

Decisions already made (do not ask again):
- Every lane Claude Opus 5.5, direct (frontend with `frontend-design`; tests-e2e extends the Playwright suite). Hermes plan review 1 round; hermes code review 1 round; Claude integration review (Opus).
- Research: a targeted NotebookLM query, no web research; the notebook is stale, repo files win.
- One plan, phased; commit/push checkpoint after each phase; a single prod deploy after the integration review, with user approval.
- User answers: Q1 all three `hermes.sh` copies; Q2 producer in `producers/claude_code/`; INGEST_TOKEN stays unset until the Activation Gate; O9 #4 (PA proposals view) out of scope; repair script yes (prod apply needs separate approval); jobs table in the dashboard; `/hermes` skill passes work_type; tailnet address removed from notebook-bound docs.
- `~/.claude/settings.json`: TI and PossibleSkills both merge-only; install order goes through a "settings.json hook kurulumu" row in the hermes queue; after each install, check the other side's entries survived.

Inputs: `Plans/task-telemetry-jev-pilot/o9-inputs.md`, `ps-export-contract-inputs.md`, `Projects/PossibleSkills/D8-ortak-olay-sozlesmesi-taslak.md`, `Projects/PossibleSkills/SISTEM-TASARIMI.md`.
