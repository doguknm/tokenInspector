# O9 — PossibleSkills inputs for the export contract (2026-09-28)

Source: possibleskills-f1 session (continuation of PA), after its hermes brainstorm
job 20260928-141024-960 (queue row 14). Input for PM round 2 (--finalize).

## Screen decision
- Hermes agreed with TI's view: now C (CLI + markdown report, approve/reject in chat), later A
  (PossibleSkills' own local screen). A TI dashboard tab is the wrong dependency direction.
- PossibleSkills decisions and proposals are not stored in TI.
- User decision relayed by possibleskills-f1: O9 #4 (PA proposals view in TI) leaves the TI
  plan. The only link is the versioned, read-only export (O9 #3); the export does not block
  PossibleSkills' delivery.
- The consumer's minimum field list is also in
  `Projects/PossibleSkills/Plans/toplayici-karar-defteri/plan.md`, section "TI export sözleşmesi".

## What the export contract must state (hermes gap list)
- Coverage field: which runtimes the data covers (today only hermes-agent turns).
- Stable event/job ids and the dedup rule.
- UTC timestamps; whether date range bounds are inclusive or exclusive.
- Units, and the difference between zero, unknown and absent (D8 §3).
- Project identity mapping (no user paths).
- Pagination and a partial-response marker.
- Generation time, covered period and staleness.
- `updated_at` / snapshot meaning for tasks and jobs that can still change.
- Error and timeout responses.
- An explicit allowed-field list.
- JSON is for machines, CSV for humans.
- `schema_version` changes only on a breaking change; consumers ignore unknown extra fields;
  the contract is versioned separately from the TI app version.

## Consumer behaviour PossibleSkills commits to
- On a version mismatch or when TI is unreachable, only the TI-backed view is disabled.
- Missing TI data is never read as zero usage.
- PossibleSkills does not connect to TI until the export is ready (no DB reads, no screen
  scraping).
