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
  - one task of job `...-111` gets a later event tagged `...-222` → one conflict.
  - events without any job (must not create rows).
  - Phase 3 extra: one llm event whose `model` is `=HYPERLINK("x")`, one `+1+2`, one `-3`, one `@SUM(A1)`, one containing `a,"b"` (quoting); a CC event (no `reasoning_tokens` in the export) and an unpriced event (`cost_usd` null).
- Downloads: `with page.expect_download() as d: page.click("#export-csv")`, then read `d.value.path()` as UTF-8 (strip the BOM) and parse with `csv.reader`.
- Every check below is written as a user action and an assertion.

## Test Plan by Acceptance Criterion

### Phase 1 — Jobs view

| AC | Lane | Description |
|---|---|---|
| AC1.6 | frontend | Open the dashboard and click the **Jobs** nav link; assert the Jobs view is visible and `#table-jobs` has exactly 4 rows (jobs without a `job_ref` never appear) |
| AC1.6 | frontend | In the row for `20260928-100000-111`, assert Tasks = 2, Work type = `review`, Runtime = `hermes-agent`, the four token cells and the sum match the seeded totals, and Cost shows a plain dollar value with no badge |
| AC1.6-a | frontend | In the row for `20260928-120000-333` (all calls unpriced), assert the Cost cell shows "—" with an `unpriced` badge and the cell text never contains `$0.0000` |
| AC1.6-a | frontend | In the row for `20260928-110000-222` (mixed), assert the Cost cell starts with "≥ $" and shows a `1 unpriced` badge |
| AC1.6-b | frontend | Choose `claude-code@hermes` in the runtime select; assert only the `devir-…-444` row remains and its Projects cell is `pegadocrag` |
| AC1.6-b | frontend | Reset runtime, choose work type `code`; assert only the `…-222` row remains |
| AC1.6-b | frontend | Click the 7d button; assert the table reloads (request with `days=7` observed) and still shows the seeded jobs |
| AC1.6-b | frontend | Intercept `/api/jobs` to return 500; reload the view; assert `#jobs-error` shows the fixed text and no server body text is displayed |
| AC1.6-b | frontend | Intercept `/api/jobs` to return an empty `items` list; assert `#jobs-empty` is visible |
| AC1.6-c | frontend | Assert `#jobs-anomalies` is visible and says 1 task received a second job id; assert the `…-111` row's Conflicts cell shows the warning badge |
| AC1.6-d | frontend | Click through every existing view (Overview, Projects, Models, Complexity, Tasks, Settings) and back to Jobs; assert no console error was logged |

### Phase 3 — Export panel

| AC | Lane | Description |
|---|---|---|
| AC3.1 | frontend | In the Jobs view, choose dataset "LLM calls", keep the default range, click **Download CSV**; assert a file downloads, its first row equals the `fields` list of `GET /api/export/v1/events` for the same range, and the number of data rows equals the total number of exported items across all JSON pages |
| AC3.1 | frontend | Repeat for "Jobs" and "Tasks"; assert headers equal the respective `fields` lists |
| AC3.1 | frontend | Assert the **Open JSON (first page)** link points at `/api/export/v1/<dataset>?from=…&to=…` matching the chosen inputs; follow it and assert `schema_version` is 1 and `period` echoes the range |
| AC3.9 | frontend | In the downloaded events CSV, assert the model cells for `=HYPERLINK("x")`, `+1+2`, `-3` and `@SUM(A1)` start with a single quote `'`; assert numeric token and cost cells do not start with `'` |
| AC3.9 | frontend | Assert the cell containing `a,"b"` round-trips through `csv.reader` unchanged (RFC 4180 quoting) |
| AC3.6 | frontend | In the events CSV, assert the CC event's `reasoning_tokens` cell is empty (absent), the unpriced event's `cost_usd` cell is `null` (unknown), and a zero-token cell is `0` |
| AC3.3 | frontend | Set a range longer than 92 days (or `from` after `to`) and click Download CSV; assert the status line shows the fixed range message and no export request was sent |
| AC3.3 | frontend | Intercept `/api/export/v1/events` to return 400 `{"error":"invalid_range","schema_version":1}`; click Download CSV; assert the status line shows only `invalid_range` |

### Mutation checks (browser)

| # | Control | Mutation site | Expected failing test |
|---|---|---|---|
| FM1 | unpriced never zero | `app.js` `jobCostCell`: render `fmt.cost(r.cost_usd ?? 0)` | `test_jobs_unpriced_never_zero` (AC1.6-a) |
| FM2 | CSV formula guard | `app.js` `csvCell`: remove the leading-character prefix | `test_csv_formula_injection_guard` (AC3.9) |
| FM3 | CSV header = fields | `app.js` `toCsv`: header from `Object.keys(items[0])` | `test_csv_header_equals_fields` (AC3.1; the first item lacks absent keys, so the header shrinks) |

Record results in status.md `## Verification Log`.

## Primary User Flow

Feature: cost per launcher job and a read-only export in the Token Inspector dashboard. The user opens the dashboard, clicks **Jobs**, and sees one row per job started through `hermes.sh` or a Claude Code hand-off run, with task count, runtime, work type, token totals and cost. They filter by runtime or work type and change the time window. At the bottom of the view they choose a dataset (jobs, tasks or LLM calls) and a UTC date range, click **Download CSV**, and open the file in a spreadsheet.

- Happy path: with the four seeded jobs, the table shows 4 rows; the fully priced job shows a plain cost; the CSV for LLM calls downloads with the same columns as the JSON export and one row per exported call. Pass = rows, badges and CSV header/rows match the API.
- Invalid/edge path: the all-unpriced job must show "—" with an `unpriced` badge (fail if any `$0.0000` appears); a model name starting with `=` must appear in the CSV as `'=…` (fail if a cell starts with `=`, `+`, `-` or `@` without the quote); a 100-day range must be refused with the fixed message and no request (fail if a request is sent).

## Out of Scope

- Any live production check (Deploy Runbook, user-approved).
- The PA proposals view (not built).
- Claude Code hook behaviour (tests-other, no browser).
- Browser tests on hermes (no Playwright there; the module skips).
