# Frontend — O9: job correlation, cross-project child fix, Claude Code producer and a versioned read-only export

**Lane**: frontend
**Tool**: Claude Code — Opus 5.5, direct (no handoff), `frontend-design` skill
**Status**: not started
**Brief**: ./2026-09-28-summary.md
**Repo**: backend only — `C:\Users\Bentego_Admin\Projects\tokenInspector` (`static/index.html`, `static/app.js`, `static/style.css`), branch `feat/task-telemetry-jev-pilot`. The plugin repo has no UI.
**Depends on**: backend.md J5 (`GET /api/jobs`, Phase 1) and X2–X5 (`/api/export/v1/*`, Phase 3). Phase 2 has no UI change.

## Goal

Phase 1: a **Jobs** view that shows cost per job with the same semantics as `GET /api/jobs` (an unpriced call never shows as zero or complete). Phase 3: an **Export (v1)** panel in the same view that downloads jobs, tasks or llm-call events as CSV (same field set as the JSON export, formula-injection-safe) and links the JSON.

## Constraints (from CLAUDE.md, AGENTS.md, existing code)

- Vanilla JS SPA, no build step, no new library. Chart.js is already loaded; this lane adds **no chart** (a table is the right shape for per-job cost). If a chart is ever added, call `destroyChart(id)` before re-creating it (AGENTS.md gotcha).
- Follow the existing patterns in `static/app.js`: `api(path)`, `fmt.num` / `fmt.cost` / `fmt.date`, `TOKEN_TYPES` + `tokenCells(r)` for the four token columns (Input, Cache read, Cache write, Output, then the labelled sum), toolbar + `days-picker` buttons, pagination with ← / → buttons, `muted hidden` empty/error lines, `class="num"` for numeric columns. Match `style.css` tokens; add only the few rules the view needs.
- Navigation: add `<a href="#" data-view="jobs" class="nav-link">Jobs</a>` between Tasks and Settings, a `<section id="view-jobs" class="view hidden">`, and `if (view === 'jobs') loadJobs();` in the nav handler.
- Cell text: every value from the API is rendered with `textContent` (or a local escape helper used only in this view). Backend normalization bounds `job_ref`, `runtime`, `work_type` and project names, but `model` and similar fields are producer-supplied, so never interpolate them raw into `innerHTML`.
- The dashboard never shows prompt text, paths or hostnames (none are in these APIs).
- Wording rules from AGENTS.md "What the numbers mean": cost is a list-price estimate for subscription providers, not a bill; there is no job duration (first/last event times are not end-to-end job time); completion is not success.

---

## Phase 1 — Jobs view (AC1.6)

### User-Facing Changes

- New nav entry **Jobs** and view `#view-jobs`.
- Toolbar: heading "Jobs", runtime select (`#jobs-runtime`, "All runtimes" + `filters.runtimes`), work type select (`#jobs-work-type`, "All work types" + `filters.work_types`), days picker 7d / 30d (default) / 90d (`.jobs-day`), pagination (`#jobs-prev`, `#jobs-page-info`, `#jobs-next`).
- One info line under the toolbar (`#jobs-note`, `status-line`): "One row per launcher job. Costs sum priced calls only; a job with unpriced calls is incomplete, never zero. Times are first and last event, not job duration."
- Anomaly line (`#jobs-anomalies`, hidden when 0): "N task(s) received a second job id; the first one was kept."
- Table `#table-jobs`:

  | Column | Source | Rendering |
  |---|---|---|
  | Job | `job_ref` | monospace text |
  | Work type | `work_type` | text; `null` with `work_types.length > 1` → "mixed" + title listing them; `null` with none → "—" |
  | Runtime | `runtimes` | comma-joined |
  | Projects | `projects` | comma-joined |
  | Tasks | `task_count` | `fmt.num` |
  | Calls | `llm_request_count` | `fmt.num` |
  | Input / Cache read / Cache write / Output / Total (sum) | four token fields | `tokenCells(r)` |
  | Cost (USD) | `cost_usd`, `unpriced_count`, `cost_complete` | see cost rule |
  | Est. cost | `estimated_cost_usd` | `fmt.cost`, "—" when 0 |
  | First / Last | `first_event_at`, `last_event_at` | `fmt.date` |
  | Attempts | `attempts` | comma-joined, "—" when empty |
  | Conflicts | `conflict_count` | number; a warning badge when > 0 |

- **Cost rule (the AC1.6 semantics):**
  - `cost_usd === null` → "—" plus a badge `unpriced` (title: "N unpriced call(s); no priced call").
  - `cost_usd !== null` and `unpriced_count > 0` → "≥ $x.xxxx" plus a badge `N unpriced` (title: "Incomplete: unpriced calls are not included").
  - `cost_complete === true` → `fmt.cost(cost_usd)`.
  - Otherwise (partial/estimated calls, no unpriced) → `fmt.cost(cost_usd)` plus a badge `partial`.
  - The cell never renders `$0.0000` for a job whose calls are all unpriced.
- Empty state `#jobs-empty`: "No jobs in this window. Jobs appear for runs started through hermes.sh (and Claude Code hand-off runs) after this release." Error state `#jobs-error`: "Could not load jobs." (no server text shown).

### Pages and Components

| Path | Change | Notes |
|---|---|---|
| `static/index.html` | nav link `jobs`; `<section id="view-jobs">` with toolbar, note, anomaly line, empty/error lines, `#table-jobs` | ids above are the contract with tests-e2e |
| `static/app.js` | `let jobsState = {days: 30, runtime: '', workType: '', page: 1}`; `loadJobs()`; `renderJobs(data)`; `jobCostCell(r)`; nav hook; listeners for selects, day buttons, pagination | `page_size` 50 |
| `static/style.css` | badge variants for `unpriced` / `partial` / conflict warning if the existing `.badge` classes are not enough | reuse colours already defined |

### API Contracts Consumed

| Endpoint | Method | Request shape | Response shape |
|---|---|---|---|
| `/api/jobs` | GET | `?days=<7\|30\|90>&page=<n>&page_size=50[&runtime=<r>][&work_type=<w>]` | `{items: [{job_ref, runtimes[], work_type\|null, work_types[], attempts[], projects[], task_count, llm_request_count, prompt_tokens, completion_tokens, cache_read_tokens, cache_creation_tokens, priced_count, unpriced_count, cost_usd\|null, estimated_cost_usd, cost_complete, first_event_at, last_event_at, conflict_count}], total, page, page_size, filters: {runtimes[], work_types[]}, anomalies: {job_ref_conflicts}}` |

A 400 (`invalid_filter`) cannot happen from the UI (selects come from `filters`); treat any non-2xx as the error state.

### Acceptance Criteria (Phase 1)

- AC1.6: The Jobs view lists one row per `job_ref` from `/api/jobs` for the chosen window and filters, with task count, runtime(s), work type, attempts, the four token sums, cost, estimated cost, unpriced count and first/last event time.
- AC1.6-a: A job whose calls are all unpriced shows "—" with an `unpriced` badge, never `$0.0000`; a job with some unpriced calls shows "≥ $x" with an `N unpriced` badge; only a `cost_complete` job shows a plain cost.
- AC1.6-b: Runtime and work-type filters, the 7/30/90-day picker and pagination reload the table; empty and error states show fixed text.
- AC1.6-c: A non-zero `anomalies.job_ref_conflicts` shows the anomaly line; a row with `conflict_count > 0` shows the warning badge.
- AC1.6-d: `node --check static/app.js` passes; existing browser tests stay green.

---

## Phase 2 — no UI change

The Claude Code producer adds events with runtimes `claude-code@windows` / `claude-code@hermes`; they appear in the Jobs view's runtime filter and in existing views automatically. No frontend work.

---

## Phase 3 — Export (v1) panel (AC3.1, AC3.9)

### User-Facing Changes

- A panel **Export (v1)** at the bottom of the Jobs view (`#export-panel`):
  - dataset select `#export-dataset` (Jobs / Tasks / LLM calls → `jobs` / `tasks` / `events`);
  - `#export-from` and `#export-to` date inputs (UTC dates; `from` inclusive at 00:00Z, `to` exclusive at 00:00Z; default: last 30 days ending today+1);
  - button `#export-csv` "Download CSV";
  - link `#export-json` "Open JSON (first page)" pointing at `/api/export/v1/<dataset>?from=…&to=…` (updates when inputs change);
  - status line `#export-status` ("Fetched N rows", "Range must be at most 92 days", "Export failed: <error code>" using only the `error` code from the static body).
  - One sentence: "JSON is for machines, CSV for people. Empty cell = not measured; `null` = unknown; 0 = zero. Contract: docs/export-contract-v1.md."

### CSV generation (client-side, from the JSON export)

1. Fetch page 1 with `limit=1000`; follow `next_cursor` until `complete === true`. Stop with an error message if more than 100 000 rows (show "Too many rows; narrow the range").
2. Header = `envelope.fields` (identical field set and order as JSON — AC3.1).
3. Cell value per field: key absent → empty string; `null` → `null`; arrays → `;`-joined; booleans → `true`/`false`; numbers → `String(n)`; strings as-is.
4. **Formula-injection guard (AC3.9):** for every **string** cell (after step 3, including array joins), if it starts with `=`, `+`, `-`, `@`, tab (`\t`) or carriage return (`\r`), prefix it with a single quote `'`. Numbers are not prefixed (costs and tokens are never negative).
5. RFC 4180 quoting: wrap a cell in `"` if it contains `,`, `"`, `\r` or `\n`; double inner quotes. Lines end with `\r\n`. UTF-8 with a BOM (so spreadsheet apps read non-ASCII model names correctly).
6. Download through a `Blob` + temporary `<a download="ti-export-v1-<dataset>-<from>-<to>.csv">`; revoke the object URL afterwards.
7. Keep the guard and quoting in one small function `csvCell(value)` so the browser test and the mutation check target one site.

### Pages and Components

| Path | Change | Notes |
|---|---|---|
| `static/index.html` | `#export-panel` inside `#view-jobs` | ids are the tests-e2e contract |
| `static/app.js` | `exportUrl(dataset, from, to, cursor)`, `fetchAllExport()`, `csvCell(value)`, `toCsv(envelope, items)`, download handler, JSON link updater | no new globals beyond an `exportState` object |
| `static/style.css` | small layout rules for the panel if needed | |

### API Contracts Consumed

| Endpoint | Method | Request shape | Response shape |
|---|---|---|---|
| `/api/export/v1/{jobs,tasks,events}` | GET | `?from=<ISO Z>&to=<ISO Z>&limit=1000[&cursor=<opaque>]` | `{schema_version: 1, dataset, generated_at, period: {from, to}, fields[], staleness: {<runtime>: iso\|null}, coverage: {measured[], not_measured[]}, items[], next_cursor\|null, complete}`; errors: 400/503 `{error, schema_version}` |

### State Management

Plain module-level objects in `app.js` (`jobsState`, `exportState`), no persistence (no localStorage). The export fetch loop is cancelled (ignored results) if the user starts another export.

### Accessibility / Responsive Notes

- Selects and inputs carry `aria-label`s; the day picker is a `role="group"` with `aria-label="Time window"` (as in the Tasks view); the table sits in `.table-wrap` for horizontal scroll on narrow screens.
- Badges carry a `title` explaining the state (unpriced / incomplete / partial / conflict); colour is never the only signal (badge text says it).
- The export status line uses `role="status"` so the result is announced.

### Acceptance Criteria (Phase 3)

- AC3.1 (UI part): The Export panel downloads a CSV whose header equals the JSON envelope's `fields` for the chosen dataset and range, and links the JSON first page.
- AC3.9: A string cell starting with `=`, `+`, `-`, `@`, tab or CR is prefixed with `'` in the CSV; numeric cells are unchanged; quoting follows RFC 4180. Covered by a browser test.
- AC3.6 (UI part): absent → empty cell, `null` → `null`, 0 → `0` in the CSV.
- AC3.3 (UI part): a range over 92 days or `from >= to` shows a fixed message and makes no request; a server error shows only its `error` code.

## Out of Scope

- The PA proposals approve/reject view (former O9 #4) — no tab, no data.
- Charts for jobs; a job-duration display; per-originating-project cost for review jobs.
- Server-side CSV rendering (CSV is built in the dashboard from the JSON export).
- Any change to the Overview, Projects, Models, Complexity, Tasks or Settings views beyond the nav link.
