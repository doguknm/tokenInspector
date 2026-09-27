# Tests (E2E / UI) — Task-Level Telemetry Pilot (Tasks view)

**Lane**: tests-e2e
**Tool**: Claude Code — Opus 5.5, direct (no handoff); live browser via Claude's browser tools. Automated Playwright suites stay in tests-other, not in this lane
**Status**: not started
**Brief**: ./2026-09-27-summary.md
**Revision**: r1 (2026-09-27), plan-review round 1 fixes applied (items 25, 26, 27, 28, 29)
**Revision**: r2 (2026-09-27), plan-review round 2 fixes applied (items 9, 13). **Plan LOCKED** — later changes go to the status.md Drift Log only.

## Goal

Verify in a live browser that the Tasks view does all of the following correctly:
- lists, filters and paginates tasks, with stable order;
- shows details, completion, hierarchy, request-shape-v1 and JEV values;
- renders its empty and cap states;
- survives rapid filter changes;
- escapes hostile strings;
- never exposes prompt or note text.

It must also confirm the existing views still work.

States that need forced server responses are covered by tests-other's Playwright stub suite, not here: JEV enabled/running/budget/deferred, and request failures.

## Preconditions

These are prepared by the backend/tests-other lanes or the human. This lane needs only the running app and a browser.

- **Demo DB "standard"**, created by `scripts/seed_tasks_demo.py --db <new file> --variant standard`. The app runs at `http://127.0.0.1:8100/` with `DB_PATH` set to that file.
- `JEV_ENABLED` and `STORE_TASK_PROMPTS` are unset.
- **Demo DB "many-scored"** (`--variant many-scored`) is used for one scenario (AC7h cap). The app is restarted against it.
- **Never use the production DB.**

Seeded "standard" dataset. Timestamps are relative to seeding time.

| Project | Task | Hierarchy | Age | RS-v1 | Prompt | JEV | Notes |
|---|---|---|---|---|---|---|---|
| demo-alpha | `demo-task-1` | root | 1 d | 2 | retained (text contains `CANARY-PROMPT-TEXT-7731`) | **3.2/5**, conf **0.83**, L3 0.8 / L4 0.2, typesafe-ai | 3 tool calls; completion **session end**; human label shown as 3, note contains `CANARY-NOTE-5512` |
| demo-alpha | `demo-task-1-child` | child | 1 d | 1 | — | not scored | |
| demo-alpha | `demo-task-2` | root | 2 d | 4 | retained | **4.6/5**, conf **0.61**, digitalocean | completion **next task**; 1 unpriced event |
| demo-alpha | `demo-task-3` | root | 3 d | — | purged | not scored | all events unpriced → cost "—" |
| demo-beta | `demo-task-4` | root | 2 d | 5 | retained | not scored | |
| demo-beta | `x"><img src=x onerror="window.__xss=1">` | root | 1 d | 3 | — | not scored | session `<svg onload="window.__xss=2">`; one JEV error evaluation |
| demo-beta | `demo-filler-01`…`59` | root | 1–5 d | 1..5 | — (`demo-filler-59` is seeded with an expired prompt; the app's startup purge makes it **purged**) | not scored | fillers 01–10 share an identical last-seen time |
| demo-beta | `demo-old` | unknown | 40 d | 3 | — | not scored | visible only with 90 days |

Expected counts:
- 7 or 30 days: **65** (page 1 of 2);
- 90 days: **66**;
- "Root tasks only" at 30 days: **64**;
- "Scored only": **2**;
- demo-alpha: **4**;
- "many-scored" DB, "Scored only" at 30 days: **207**.

## Test Plan by Acceptance Criterion

| AC | Lane | Description |
|---|---|---|
| AC7b | frontend | Click "Tasks"; assert the Tasks view shows and its nav link is active. Click Overview, Projects, Models, Complexity and Settings in turn; assert each renders its content with no error text. |
| AC7c | frontend | Default 30 days: assert 50 rows and "Page 1 of 2 (65 tasks)", with Prev disabled. Click Next: assert 15 rows, "Page 2 of 2", Next disabled. Click Prev: assert page 1 again. |
| AC7c | frontend | Collect the Task column of page 1 and page 2. Assert 65 distinct tasks in total, no task on both pages, and every `demo-filler-01`…`10` (identical timestamps) appearing exactly once. Reload the page twice; assert the page-1 row order is identical each time. |
| AC7p | frontend | Select project "demo-alpha". Open the project dropdown and assert it still lists "All projects", "demo-alpha" and "demo-beta" (the list is not narrowed by the project filter). Select "demo-beta" from it and assert the table switches to demo-beta tasks (r2 item 13). |
| AC7d | frontend | Row `demo-task-1`: assert RS-v1 "C2", JEV "3.2/5", Conf. "0.83", Completion "session end", Prompt "retained". |
| AC7d | frontend | Row `demo-task-4`: assert JEV "not scored", Conf. "—", RS-v1 "C5". Row `demo-task-3`: assert RS-v1 "—" and Prompt "purged". Row `demo-filler-59`: assert Prompt "purged". This is the real startup-purge check: its prompt was seeded already expired and was purged before the app served its first page (r2 item 9). |
| AC7e | frontend | Row `demo-task-2`: assert Cost shows an amount with an "unpriced" badge. Row `demo-task-3`: assert Cost shows "—" (not "$0.0000") and an "unpriced" badge. |
| AC7f | frontend | Check "Scored only": assert exactly 2 rows (`demo-task-1`, `demo-task-2`) and "Page 1 of 1 (2 tasks)". Uncheck it and assert 65. |
| AC7f | frontend | Check "Root tasks only": assert total 64 and no "↳" prefix. Uncheck. Assert the `demo-task-1-child` row shows "↳". |
| AC7f | frontend | Select project "demo-alpha": assert 4 tasks, all demo-alpha. Select "All projects": assert 65. |
| AC7f | frontend | Go to page 2, then click 90 days: assert page 1 and total 66 including `demo-old`. With "Root tasks only" checked at 90 days, assert 64 (`demo-old` is unknown hierarchy and excluded). Click 7 days and assert `demo-old` is absent. |
| AC7g | frontend | Click `demo-task-1`. Assert the detail panel shows `demo-task-1`, demo-alpha, hierarchy root, completion session end, 3 tool calls, JEV 3.2 / 0.83 / typesafe-ai, and a 5-bar probability chart with the tallest bar at L3. Assert children lists `demo-task-1-child`, and the evaluations table shows a JEV ok row and a human row with label 3. Click Close; assert the panel hides. |
| AC7g | frontend | Tab to the `demo-task-2` row and press Enter. Assert the panel opens for `demo-task-2`, showing digitalocean, completion next task and unpriced count 1. |
| AC7h | frontend | Standard DB, filters cleared: assert the scatter "Start complexity vs JEV difficulty" shows 2 points, near (2, 3.2) and (4, 4.6), and no "Showing latest 200" note. Select "demo-beta": assert "No scored tasks in this window." replaces the chart. |
| AC7h | frontend | "many-scored" DB: open Tasks. Assert the note under the chart reads "Showing latest 200 of 207 scored tasks". |
| AC7i | frontend | Standard DB: assert the JEV status line reads exactly "JEV: disabled", with no config-error suffix. |
| AC7j | frontend | Open the `demo-task-1` detail, then close it. Each time, inspect the **live DOM including hidden elements** (full document markup, not only visible text). Assert neither `CANARY-PROMPT-TEXT-7731` nor `CANARY-NOTE-5512` occurs anywhere. |
| AC7j | frontend | Inspect every network request the page made during all scenarios above. Assert none has `include_prompt` in its URL and none is a POST/PUT/DELETE. |
| AC7j | frontend | Find the hostile task row (starts with `x">`). Assert its Task cell shows the literal characters `x"><img src=x…` as text. Open its detail; assert the session field shows the literal `<svg onload=…>` text. Assert that in the live DOM there is **no** `img` element with `src="x"` and **no** `svg` element with an `onload` attribute, that `window.__xss` is undefined, and that no dialog or alert appeared. |
| AC7k | frontend | Select project "demo-beta" with "Scored only" checked. Assert the table area shows "No tasks in this window.", the pagination text reads "No tasks" with Prev and Next disabled (never "Page 1 of 0"), the chart area shows "No scored tasks in this window.", and no rows from the previous view remain. |
| AC7k | frontend | Go to page 2 (65 tasks), then check "Root tasks only". Assert the view is on page 1 (filters reset) and shows valid rows, never an empty page with total > 0. |
| AC7l | frontend | Rapidly click 7 → 90 → 30 → 90 days (within about 1 second), then wait for the network to settle. Assert the 90-day button is active and the table shows total 66. Rapidly click the rows `demo-task-1` then `demo-task-2`. Assert the detail panel finally shows `demo-task-2`. |

## Primary User Flow

**Feature:** the Tasks view. It shows per-task start complexity (request-shape-v1), completion and hierarchy, observed intensity, and JEV difficulty.

**Flow:**
1. Open `http://127.0.0.1:8100/` and click "Tasks".
2. Read the JEV status line and the chart.
3. Scan the table.
4. Filter by Scored only, project, root-only or days.
5. Page through the results.
6. Open a task's detail, then close it.

**Happy-path scenario:**
1. Open Tasks. Assert "JEV: disabled", 65 tasks and "Page 1 of 2".
2. Check "Scored only". Assert two rows:
   - `demo-task-1`: C2, 3.2/5 (0.83), session end;
   - `demo-task-2`: C4, 4.6/5 (0.61), next task, unpriced badge.
3. Click `demo-task-1`. Assert the probability chart peaks at L3, the child is listed and the human label is 3.
4. **Pass** if every value matches the seed and neither canary exists anywhere in the live DOM. **Fail** on any mismatch, error text or canary.

**Invalid / edge scenario:**
1. Select "demo-beta" with "Scored only". Assert "No tasks in this window.", "No tasks" pagination with both buttons disabled, and the chart's empty message.
2. Clear the filters and open the hostile task. Assert its id and session render as literal text, `window.__xss` stays undefined, and no injected `img`/`svg` elements exist.
3. **Pass** if the empty states are clean (no stale rows, no "Page 1 of 0") and nothing executes. **Fail** otherwise.

## Out of Scope

- The following are covered by tests-other's Playwright stub suite, not here:
  - JEV enabled/running/budget/deferred status rendering;
  - the detail 404;
  - failed list/chart/status requests, including chart-only failure;
  - the out-of-range page clamp;
  - a delayed detail response after Close;
  - the `expired` prompt state. It cannot be seeded, because the startup purge runs first; it is covered by an injected clock or a stubbed response.
- Real JEV calls and production data.
- Label entry, purge, and the pilot CLI.
- Playwright in this lane.
- Plugin behaviour.
- Performance.
