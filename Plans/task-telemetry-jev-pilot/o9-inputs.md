# O9 inputs (next joint plan with PA)

Collected 2026-09-28 for `/new-plan` of O9. Working rules for that plan: Hermes plan review 1 round, Hermes findings applied by default, auth minimal (single user, local).

## PA priority order (PA user approved, 2026-09-28)
1. **#2 job correlation**: `job_ref`, runtime, work type (brainstorm/review/code/devir) and attempt, all carried in `tags` without content. One job maps to many `task_ref`s. It comes first because it is small and gives P1 value at once: cost per hermes brainstorm/review job.
2. **#7 Claude Code producer**: Windows sessions plus `claude -p` hand-off runs on hermes, with a cross-source dedup authority. This is P1's main data gap, but a bigger piece of work.
3. **Dashboard CSV/JSON export.**
4. **PA proposals view**: approve/reject is recorded here; PA's script applies it.

Already covered: #1, #3 and #5 are done; #4 and #6 are done in O10.

## D8 contract
`Projects/PossibleSkills/D8-ortak-olay-sozlesmesi-taslak.md` (owner: PA). Two points were agreed:
- §1: the real repository name;
- §5: data does not leave hermes, except for the D10 exception.

Nothing has changed since then.

## Also in scope
- The cross-project child hierarchy defect: a child whose parent lives in another project never finds its parent row. It was logged separately during O10 planning.

## PA availability (2026-09-28)
The user told PA to end its session after its current work. O9 is therefore planned by TI alone from the PA input above and the D8 draft; nothing else is expected from PA. A later PA session can review the plan.

## Hermes queue
PA's next hermes job is the /foy reconciliation code review. It runs after TI's queue row 8 (the O10 code review), and PA messages TI before starting it.
