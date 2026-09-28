# Tests (E2E / UI) — O9: job correlation, cross-project child fix, Claude Code producer and a versioned read-only export

**Lane**: tests-e2e
**Tool**: Claude Code — Opus 5.5, direct (no handoff) — extends the existing Playwright browser suite in `tests/browser/` (marker `browser`, run with `python -m pytest -q -m browser`); live browser against a running app
**Status**: not started
**Brief**: ./2026-09-28-summary.md
**Repo**: backend only — `C:\Users\Bentego_Admin\Projects\tokenInspector`, branch `feat/task-telemetry-jev-pilot`

## Goal

Verify in a real browser that the Jobs view shows cost per job with the API's semantics (Phase 1, AC1.6) and that the Export panel downloads a CSV with the JSON field set and formula-injection protection (Phase 3, AC3.1, AC3.6, AC3.9).

## Environment and Conventions

- New files: `tests/browser/test_jobs_view.py` (Phase 1), `tests/browser/test_export_download.py` (Phase 3).
- **Own server per module.** Do not add job data to the shared session `server` fixture or to `scripts/seed_tasks_demo.py` (other browser tests count Tasks rows). Each new module starts its own module-scoped uvicorn on a free port with a temp `DB_PATH`, copying the pattern of `tests/browser/conftest.py::server` (same env as that fixture: `STORE_RAW_PROMPTS=0`, the same `TOKEN_INSPECTOR_ALLOWED_HOSTS` value, `INGEST_TOKEN` and capture flags removed), then seeds data by posting events to `/api/events/batch` with `httpx` (ingest is open while `INGEST_TOKEN` is unset). Terminate the process in `finally` with a `wait(timeout=10)`.
- Keep the `browser` fixture module-scoped (RP 7). The browser suite stays a separate CI job.
- Pricing: seed a pricing rule for `priced-model` through `POST /api/settings/pricing`; `unpriced-model` has no rule (stays `unpriced`).
- Seed data (all timestamps within the last 2 days, UTC):
  - job `20260928-100000-111` (work_type `review`, runtime `hermes-agent`, producer `hermes-plugin`): 2 tasks, all calls on `priced-model` → complete cost.
  - job `20260928-110000-222` (work_type `code`): 1 task, one priced + one unpriced call → incomplete.
  - job `20260928-120000-333` (work_type `brainstorm`): 1 task, only `unpriced-model` calls → no priced call.
  - job `devir-20260928-130000-444` (work_type `devir`, runtime `claude-code@hermes`, producer `claude-code-hook`, project `pegadocrag`): 1 task, priced.
  - one task of job `...-111` gets two later calls (on `priced-model`) tagged `...-222` and `...-333` → one task, two conflicting calls (B-F15); they count under `...-111`.
  - job `20260928-140000-555` (hermes-agent) with two tasks of work types `review` and `code` → "mixed" work type (B-F13).
  - events without any job (must not create rows).
  - Phase 3 extra: a CC event (no `reasoning_tokens` in the export) and an unpriced event (`cost_usd` null); one llm event whose `model` is `=HYPERLINK("x")` (the backend value rule exports it as `null` — asserted, so real data can never carry a formula); plus 1 050 small priced events in one extra project so the LLM-calls export needs two JSON pages at `limit=1000` (B-F13); one event at exactly 00:00:00Z of the default `to` date (excluded) and one at 00:00:00Z of the `from` date (included).
- **Fabricated responses** (Playwright `page.route`) are used where seeding cannot produce the state cheaply: a Jobs response with `total=120` for page navigation, a `partial` cost row, delayed/raced responses, export page failures, a repeated cursor and the row cap. Fabricated bodies follow the documented shapes exactly.
- Downloads: `with page.expect_download() as d: page.click("#export-csv")`, then read `d.value.path()` as UTF-8 (strip the BOM) and parse with `csv.reader`.
- Every check below is written as a user action and an assertion.

## Test Plan by Acceptance Criterion

### Phase 1 — Jobs view

| AC | Lane | Description |
|---|---|---|
| AC1.6 | frontend | Open the dashboard and click the **Jobs** nav link; assert the Jobs view is visible and `#table-jobs` has exactly 5 rows (jobs without a `job_ref` never appear) |
| AC1.6 | frontend | In the row for `20260928-140000-555`, assert Work type shows "mixed" with a title listing `code` and `review` |
| AC1.6 | frontend | In the row for `20260928-100000-111`, assert Tasks = 2, Work type = `review`, Runtime = `hermes-agent`, the four token cells and the sum match the seeded totals, and Cost shows a plain dollar value with no badge |
| AC1.6-a | frontend | In the row for `20260928-120000-333` (all calls unpriced), assert the Cost cell shows "—" with an `unpriced` badge and the cell text never contains `$0.0000` |
| AC1.6-a | frontend | In the row for `20260928-110000-222` (mixed), assert the Cost cell starts with "≥ $" and shows a `1 unpriced` badge |
| AC1.6-b | frontend | Choose `claude-code@hermes` in the runtime select; assert only the `devir-…-444` row remains and its Projects cell is `pegadocrag` |
| AC1.6-b | frontend | Reset runtime, choose work type `code`; assert exactly the `…-222` and `…-555` rows remain (filters select whole jobs), and the `…-555` row still shows the totals of both its tasks |
| AC1.6-b | frontend | Click the 7d button; assert the table reloads (request with `days=7` observed) and still shows the seeded jobs |
| AC1.6-b | frontend | Intercept `/api/jobs` to return 500; reload the view; assert `#jobs-error` shows the fixed text and no server body text is displayed |
| AC1.6-b | frontend | Intercept `/api/jobs` to return an empty `items` list; assert `#jobs-empty` is visible |
| AC1.6-b | frontend | Intercept `/api/jobs` to return `total=120`, `page_size=50` with 50 fabricated rows; assert the page info shows page 1 of 3, click → and assert a request with `page=2` and the page info page 2 of 3; ← goes back; → is disabled on page 3 (B-F13) |
| AC1.6-a | frontend | Intercept `/api/jobs` to return one row with `cost_complete=false`, `unpriced_count=0` and a non-null `cost_usd`; assert the Cost cell shows the plain value plus a `partial` badge (B-F13) |
| AC1.6-c | frontend | Assert `#jobs-anomalies` is visible and says 1 task received a different job id on 2 calls; assert the `…-111` row's Conflicts cell shows 2 with the warning badge and its title names 2 calls in 1 task (B-F15) |
| AC1.6-e | frontend | Race (B-F12): route `/api/jobs?…runtime=hermes-agent…` with a 1.5 s delay and the unfiltered request with no delay; choose `hermes-agent`, then immediately reset to "All runtimes"; wait 2 s; assert the table shows the unfiltered rows (the late filtered response was discarded) and no error line is shown |
| AC1.6-d | frontend | Click through every existing view (Overview, Projects, Models, Complexity, Tasks, Settings) and back to Jobs; assert no console error was logged |

### Phase 3 — Export panel

| AC | Lane | Description |
|---|---|---|
| AC3.1 | frontend | In the Jobs view, choose dataset "LLM calls", keep the default range, click **Download CSV**; assert a file downloads, its first row equals the `fields` list of `GET /api/export/v1/events` for the same range, the export made **two** page requests (the second with the first page's `next_cursor`), and the number of data rows equals the total number of exported items across all JSON pages (B-F13) |
| AC3.3 | frontend | Assert the event at 00:00:00Z of the `from` date is in the CSV and the one at 00:00:00Z of the `to` date is not (B-F13) |
| AC3.1 | frontend | Choose a range with no events; click Download CSV; assert a file with only the header row downloads and the status says 0 rows (B-F13) |
| AC3.1 | frontend | Repeat for "Jobs" and "Tasks"; assert headers equal the respective `fields` lists |
| AC3.1 | frontend | Assert the **Open JSON (first page)** link points at `/api/export/v1/<dataset>?from=…&to=…` matching the chosen inputs; follow it and assert `schema_version` is 1 and `period` echoes the range |
| AC3.9 | frontend | Because the backend value rules never export a string led by `= + - @`, tab or CR, the client-side guard is tested with an **intercepted** one-page events response whose `model` cells are `=HYPERLINK("x")`, `+1+2`, `-3`, `@SUM(A1)`, `\tx` and `\rx` (the guard must hold even if a future backend change let such a value through): assert each CSV cell starts with a single quote `'`; assert numeric token and cost cells do not start with `'` (B-F13). In the real-data events CSV, assert the `=HYPERLINK("x")` event's model cell is `null` |
| AC3.9 | frontend | In the same intercepted response, a string cell containing `a,"b"` and one containing a newline round-trip through `csv.reader` unchanged (RFC 4180 quoting) |
| AC3.6 | frontend | In the events CSV, assert the CC event's `reasoning_tokens` cell is empty (absent), the unpriced event's `cost_usd` cell is `null` (unknown), and a zero-token cell is `0` |
| AC3.3 | frontend | Set a range longer than 92 days (or `from` after `to`) and click Download CSV; assert the status line shows the fixed range message and no export request was sent |
| AC3.3 | frontend | Intercept `/api/export/v1/events` to return 400 `{"error":"invalid_range","schema_version":1}`; click Download CSV; assert the status line shows exactly the fixed message mapped for `invalid_range` |
| AC3.3 | frontend | Closed error policy (B-F14): intercept with (a) 400 `{"error":"zz-canary-host.internal C:\\Users\\ZZ-CANARY-USER"}`, (b) 500 with an HTML body containing a canary, (c) a body that is not JSON, (d) an aborted request (network failure), (e) 409 `snapshot_expired`; assert (a)–(d) show exactly the generic "Export failed." and no canary text anywhere in the page, and (e) shows the fixed "Data changed during the export; please download again" |
| AC3.4 (UI) | frontend | Export loop termination (B-F12): (a) page 2 fails with 503 → fixed "busy" message, no download; (b) page 2 returns the same `next_cursor` as page 1 → generic failure, no download, no third request; (c) `complete=false` with `next_cursor=null` → generic failure; (d) fabricated pages of 1 000 items each until the 100 000-row cap → "Too many rows; narrow the range", no download, and no request after the cap |
| AC3.4 (UI) | frontend | Superseded run (B-F12): delay every export page by 1 s; start an export of "LLM calls", then change the dataset to "Jobs" and start another; assert only the second run downloads, the first run makes no request after the second started, and the status line reflects only the second run; changing the date inputs during a run does not change the parameters of that run's later page requests |

### Mutation checks (browser)

| # | Control | Mutation site | Expected failing test |
|---|---|---|---|
| FM1 | unpriced never zero | `app.js` `jobCostCell`: render `fmt.cost(r.cost_usd ?? 0)` | `test_jobs_unpriced_never_zero` (AC1.6-a) |
| FM2 | CSV formula guard | `app.js` `csvCell`: remove the leading-character prefix | `test_csv_formula_injection_guard` (AC3.9) |
| FM3 | CSV header = fields | `app.js` `toCsv`: header from `Object.keys(items[0])` | `test_csv_header_equals_fields` (AC3.1; the first item lacks absent keys, so the header shrinks) |
| FM4 | stale Jobs response discarded | `app.js` `loadJobs`: render every response (drop the generation check) | `test_jobs_latest_request_wins` (AC1.6-e) |
| FM5 | closed export errors | `app.js` `exportErrorMessage`: return `"Export failed: " + code` for unknown codes | `test_export_error_policy_closed` (AC3.3 closed policy, case a) |
| FM6 | superseded run stops | `app.js` `fetchAllExport`: drop the `runId` check | `test_export_superseded_run_stops` (AC3.4 UI) |
| FM7 | repeated cursor | `app.js` `fetchAllExport`: drop the seen-cursor check | `test_export_loop_terminates` (b) |
| FM8 | conflict wording | `app.js`: anomaly line from `job_ref_conflicts` only (tasks = calls) | `test_jobs_conflict_counts` (AC1.6-c) |

Record results in status.md `## Verification Log`.

## Primary User Flow

Feature: cost per launcher job and a read-only export in the Token Inspector dashboard. The user opens the dashboard, clicks **Jobs**, and sees one row per job started through `hermes.sh` or a Claude Code hand-off run, with task count, runtime, work type, token totals and cost. They filter by runtime or work type and change the time window. At the bottom of the view they choose a dataset (jobs, tasks or LLM calls) and a UTC date range, click **Download CSV**, and open the file in a spreadsheet.

- Happy path: with the five seeded jobs, the table shows 5 rows; the fully priced job shows a plain cost; the CSV for LLM calls downloads with the same columns as the JSON export and one row per exported call. Pass = rows, badges and CSV header/rows match the API.
- Invalid/edge path: the all-unpriced job must show "—" with an `unpriced` badge (fail if any `$0.0000` appears); a string cell starting with `=` (intercepted response) must appear in the CSV as `'=…` (fail if a cell starts with `=`, `+`, `-`, `@`, tab or CR without the quote); a 100-day range must be refused with the fixed message and no request (fail if a request is sent).

## Out of Scope

- Any live production check (Deploy Runbook, user-approved).
- The PA proposals view (not built).
- Claude Code hook behaviour (tests-other, no browser).
- Browser tests on hermes (no Playwright there; the module skips).
