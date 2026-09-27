# Frontend — Task-Level Telemetry Pilot (Tasks view)

**Lane**: frontend
**Tool**: Claude Code — Opus 5.5, direct (no handoff); load the `frontend-design` skill before this lane
**Status**: not started
**Brief**: ./2026-09-27-summary.md
**Revision**: r1 (2026-09-27), plan-review round 1 fixes applied (items 25, 26, 27, 28, 29)
**Revision**: r2 (2026-09-27), plan-review round 2 fixes applied (items 10, 11, 12, 13). **Plan LOCKED** — later changes go to the status.md Drift Log only.

## Goal

Add a minimal **Tasks** view to the existing vanilla-JS dashboard. The view lists tasks with:
- deterministic request-shape-v1 start complexity;
- observed intensity;
- completion and hierarchy status;
- the JEV difficulty score, confidence and probabilities once scored;
- a JEV evaluator status line and one Chart.js chart.

There is no build step, no framework and no prompt text anywhere. Stale responses are ignored, every empty state is defined, and all API strings are escaped.

## Existing Dashboard Conventions (must follow)

- **Files:** `static/index.html` (nav `<a href="#" data-view="<name>" class="nav-link">`, views `<section id="view-<name>" class="view hidden">`), `static/app.js` (all logic) and `static/style.css`.
- **Chart.js 4.4.3** is already loaded from the CDN. Add no other libraries or bundler.
- **Navigation:** the handler in `app.js` hides `.view` elements and calls a loader per view. Add `if (view === 'tasks') loadTasks();`.
- **Helpers to reuse:** `api(path)` (throws on non-OK, with the response text as the message), `fmt.num`, `fmt.cost` (`'—'` for null), `fmt.ms`, `fmt.date`, `destroyChart(id)`, the global `charts` map and `CHART_COLORS`.
- **Theme:** text `#e2e8f0`, muted `#8892a4`, grid `#2a2d3a`, warning `#f59e0b`. Classes: `.toolbar`, `.chart-box`, `.table-wrap`, `.card`, `.badge`, `.hidden`.
- **Escaping (item 29):** add `esc(s)`, which replaces `& < > " '` with entities. Every API-provided string inserted via template strings must go through `esc()`, **in element content and in attribute values** (e.g. `title="${esc(task_id)}"`, `data-ref="${esc(task_ref)}"`). This covers `project_name`, `task_id`, `session_id`, `parent_task_id`, `root_task_id`, `provider_used`, legend labels, `error_type`, `labeler`, `status`, `completion` and `hierarchy_status`. Never pass API strings to `eval`, `Function`, `setAttribute('on…')`, inline handlers or `innerHTML` without `esc()`. Chart.js labels are canvas-rendered and safe.
- **CI:** `node --check static/app.js` must pass.

## User-Facing Changes

- New nav link **Tasks**, between "Complexity" and "Settings".
- New section `#view-tasks` containing:
  - filters;
  - the JEV status line;
  - a scatter chart with a cap note;
  - a paginated table with defined empty and out-of-range behaviour;
  - a detail panel.
- No change to the other views.

## Pages and Components

| Path | Change | Notes |
|---|---|---|
| `static/index.html` | Nav link `<a href="#" data-view="tasks" class="nav-link">Tasks</a>` | Before Settings |
| `#view-tasks .toolbar` | `<h2>Tasks</h2>`; day buttons 7/30/90 (default 30; ids `tasks-days-7/30/90`); `<select id="tasks-project">` (first option "All projects", value `""`); checkbox `#tasks-scored-only` "Scored only"; checkbox `#tasks-root-only` "Root tasks only" | Any change resets to page 1 and reloads the table and the chart |
| `#tasks-jev-status` | One text line | See "JEV status line" |
| `#tasks-error` | `<p class="muted hidden">` | Shows the failing request's message; other parts stay intact |
| `#chart-tasks-scatter` in `.chart-box` | Chart.js `scatter` | x = `start_complexity` (1–5), y = `jev.display_score`; one point per item with both. Data from `GET /api/tasks?evaluated=true&page_size=200&page=1` plus the same days/project/root filters. Axes 0.5–5.5, titles "request-shape-v1 (1–5)" / "JEV difficulty (1–5)", chart title "Start complexity vs JEV difficulty" |
| `#tasks-chart-cap` | `<p class="muted hidden">` under the chart | **Item 25.** When the chart response's `total > items.length`, show `Showing latest 200 of <total> scored tasks`; otherwise hide it |
| `#tasks-chart-empty` | `<p class="muted hidden">` | "No scored tasks in this window." when the chart has 0 points; the canvas is hidden |
| `#tasks-chart-error` | `<p class="muted hidden">` | **r2 item 12. Chart-only failure.** If the chart request fails while the table request succeeds, all of the following happen:<br>• show "Could not load chart.";<br>• destroy the previous chart (`destroyChart('tasks-scatter')`) and hide the canvas, so no stale points remain;<br>• hide `#tasks-chart-cap` and `#tasks-chart-empty`;<br>• leave the table, pagination and `#tasks-error` untouched.<br>A later successful chart load hides the error |
| `#table-tasks` | Columns: Last seen · Project · Task · RS-v1 · JEV · Conf. · Tokens · Cost · Tools · Wall time · Completion · Prompt | See "Cell rendering" |
| `#tasks-prev` / `#tasks-page-info` / `#tasks-next` | Pagination, page size 50 | **Item 27.** If `total > 0`: `Page <page> of <ceil(total/50)> (<total> tasks)`, with Prev disabled on page 1 and Next disabled on the last page. If `total === 0`: the text is `No tasks`, both buttons are disabled, and "Page 1 of 0" never appears. **Out of range (r2 item 10):** if the response for page `p` has `items.length === 0` and `total > 0` and `p > ceil(total/50)`, set `tasksPage = ceil(total/50)` and reload **exactly once**. Example: total 120 and page 4 → one reload of page 3, which shows rows 101–120 with Next disabled and Prev enabled. **Re-entering the Tasks view** through the nav link keeps the current page and filters (it does not reset to page 1); only filter changes reset the page |
| `#tasks-empty` | `<p>` "No tasks in this window." | Shown when `total === 0`; the table body is cleared and hidden, so no stale rows remain |
| `#task-detail` | Panel below the table (`role="region"`, `aria-label="Task detail"`) | Loads `GET /api/tasks/{task_ref}`, **never** with `include_prompt`. Contents are listed under "Detail panel". Close button `#task-detail-close`. **Close is authoritative (r2 item 11):** it increments `taskDetailGen`, hides the panel and destroys `'task-probs'`. Any detail response, success or error, that arrives after Close is discarded, and the panel stays closed |

### Cell rendering (`#table-tasks`)

| Column | Rule |
|---|---|
| Last seen | `fmt.date(last_seen_at)` |
| Project | `esc(project_name)` |
| Task | `esc(task_id)` truncated to 24 chars (truncate first, then escape); `title` attribute holds the escaped full id. Prefix `↳ ` when `hierarchy_status === 'child'` |
| RS-v1 | `C<n>`, or `—` |
| JEV | `jev.display_score.toFixed(1) + '/5'`, or muted `not scored` |
| Conf. | `jev.confidence.toFixed(2)`, or `—` |
| Tokens | `fmt.num(total_tokens)` |
| Cost | `fmt.cost(cost_usd)`: `—` when null, never `$0.0000`. Append an `unpriced` badge when `unpriced_count > 0` |
| Tools | `fmt.num(tool_call_count)` |
| Wall time | `fmt.ms(wall_time_ms)` |
| Completion | `session end` / `next task` / `inferred` / `open` (from `completion`) |
| Prompt | `retained` / `expired` / `purged` / `—` (from `prompt_state`) |

Rows carry `data-ref="${esc(task_ref)}"`, `tabindex="0"` and `cursor: pointer`. Click or Enter opens the detail.

### JEV status line (`#tasks-jev-status`)

- If `enabled === false`: `JEV: disabled`. When `disabled_reason` is `ingest_token_missing` or `invalid_budget_config`, append ` (config error: <esc(disabled_reason)>)` in warning colour.
- If enabled: `JEV: enabled — today $<spent_today_usd.toFixed(4)> of $<budget_day_usd>, <calls_today>/<max_calls_per_day> calls`.
  - Append ` — running` when `running`.
  - Append ` — budget ceiling reached` in warning colour when `last_error_type === 'budget_exceeded'`.
  - Append ` — rate-limited, retry after <fmt.date(last_run.retry_not_before)>` when `last_error_type === 'deferred_rate_limited'`.
- If the status request fails: `JEV: status unavailable`, and the table/chart still render.

### Detail panel (`#task-detail`)

- **Header fields:** task id, project, session, hierarchy status, parent/root ids, first/last seen, completion + completed at, wall time, llm requests, tool calls, errors, retries, tokens (prompt / completion / cache read / cache creation / total), cost, estimated cost, unpriced count, RS-v1 + method, and prompt state (`retained until <fmt.date(prompt_expires_at)>` / `expired` / `purged` / `not captured`).
- **JEV block:** display score, confidence, provider, rubric version, evaluated at, plus a horizontal bar chart `#chart-task-probs` of the 5 probabilities labelled `L1`…`L5`. If there is no JEV result, show `not scored`.
- **Tables:** `children` (task id, RS-v1, JEV = `jev_raw_score + 1` or `not scored`) and `evaluations` (evaluator, status, score or label shown +1, confidence, provider, attempts, error type, evaluated at). All values escaped.
- **404:** the panel shows "Task not found." and nothing else. **Other errors:** "Could not load task detail." plus the escaped message.

## API Contracts Consumed

All calls are same-origin read-only GETs. The frontend calls **no** write endpoint and never sends `include_prompt`.

| Endpoint | Method | Request shape | Response shape |
|---|---|---|---|
| `/api/tasks` | GET | `days` (7/30/90), `project` (omitted for "All"), `evaluated=true` (when "Scored only" is on, and always for the chart), `root_only=true` (when "Root tasks only" is on), `page`, `page_size` (50 for the table, 200 for the chart) | `{"items":[TaskItem], "total":int, "page":int, "page_size":int, "projects":[string]}`. Ordered by `last_seen_at` desc, `task_ref` asc (stable). A page past the end returns `items: []` with the real `total` |
| `/api/tasks/{task_ref}` | GET | Path `task_ref` | `TaskItem` + `children`, `evaluations`, `prompt_length`, `prompt_truncated`, `prompt_redaction_version`. No `prompt_text` key. 404 `{"detail":"…"}` if unknown |
| `/api/tasks/evaluator-status` | GET | — | `{"enabled":bool, "disabled_reason":string\|null, "credentials_present":bool, "running":bool, "current_run":object\|null, "last_run":{"run_id","status","stop_reason","retry_not_before","finished_at"}\|null, "spent_today_usd":number, "spent_month_usd":number, "calls_today":int, "budget_day_usd":number, "budget_month_usd":number, "max_calls_per_day":int, "last_error_type":string\|null}` |

`TaskItem` (embedded contract):

```json
{
  "task_ref": "32-hex", "project_name": "string", "task_id": "string",
  "session_id": "string|null", "parent_task_id": "string|null", "root_task_id": "string|null",
  "hierarchy_status": "root|child|unknown", "child_count": 0,
  "first_seen_at": "ISO…Z", "last_seen_at": "ISO…Z", "wall_time_ms": 12345,
  "completion": "session_end|next_task|inferred|open", "completed_at": "ISO…Z|null",
  "llm_request_count": 4, "tool_call_count": 7, "error_count": 0, "retry_count": 0,
  "prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "total_tokens": 0,
  "cost_usd": 0.0123, "estimated_cost_usd": 0.0, "unpriced_count": 0,
  "start_complexity": 3, "start_complexity_method": "request-shape-v1",
  "prompt_state": "retained|expired|purged|none", "has_prompt": true, "prompt_purged": false, "prompt_expires_at": "ISO…Z|null",
  "jev": {"raw_score": 2.2, "display_score": 3.2, "confidence": 0.83,
          "probabilities": [0.0, 0.0, 0.8, 0.2, 0.0], "legend": ["…","…","…","…","…"],
          "provider_used": "typesafe-ai", "rubric_version": "difficulty-v0", "evaluated_at": "ISO…Z"},
  "human_label_count": 0
}
```

- `jev` is `null` when not scored.
- `cost_usd` is `null` when the task has no priced events.
- Detail extras:
  - `children` (r2 item 13): `[{"task_ref":str, "task_id":str, "hierarchy_status":"child", "start_complexity":int|null, "jev_raw_score":number|null, "first_seen_at":str}]`, ordered by `first_seen_at` asc. `jev_raw_score` is the child's latest ok JEV raw score. Display `jev_raw_score + 1`, or `not scored` when null. `children_truncated: true` means only the first 200 are shown; add the note "(first 200 shown)";
  - `evaluations`: `[{"id","evaluator","rubric_version","status","label","labeler","raw_score","display_score","confidence","probabilities","provider_used","cost_usd","http_attempts","error_type","evaluated_at"}]`.

## State Management

Module-level variables, matching the existing pattern:
- `tasksDays = 30`
- `tasksPage = 1`
- `tasksProject = ''`
- `tasksScoredOnly = false`
- `tasksRootOnly = false`

Nothing is persisted.

**Stale-response protection (item 26).** Keep three generation counters: `tasksTableGen`, `tasksChartGen` and `taskDetailGen`. Each loader increments its counter before fetching, captures the value, and **discards the response (success or error) if the counter has moved on** by the time it resolves. A slow earlier filter or detail request can never overwrite newer state. **Closing the detail panel also increments `taskDetailGen`** (r2 item 11), so a response still in flight when Close is clicked is ignored. On a fetch failure, the previously rendered rows are cleared rather than silently kept, and the error line says which part failed.

Chart keys: `'tasks-scatter'` and `'task-probs'`, destroyed via `destroyChart` before re-render. The project `<select>` is repopulated from `projects`, keeping the selection if it is still present. The backend's `projects` **ignores the project and other filters** and depends only on `days` (r2 item 13), so selecting a project never removes the other options.

## Accessibility / Responsive Notes

- Filters use real `<button>` / `<select>` / `<input type="checkbox">` elements with `<label>`s.
- Pagination buttons get `aria-label="Previous page"` / `"Next page"`.
- Rows get `tabindex="0"`, and Enter acts as a click.
- The detail panel is `role="region"` with a `<button>` close.
- `.table-wrap` provides horizontal scroll. Charts use `responsive: true`.
- **Privacy:** no prompt text is requested or rendered, and there is no "include prompt" toggle.

## Acceptance Criteria

- **AC7b:** The Tasks nav link opens the view, and the other views still work.
- **AC7c:** The table shows tasks newest first, 50 per page, in stable order (tied timestamps never repeat or skip rows across pages). Pagination text and button states match `total`.
- **AC7d:** JEV shows `display_score` with one decimal and `/5`, plus confidence to 2 dp, or `not scored`. RS-v1 shows `C<n>` or `—`.
- **AC7e:** Cost shows `—` when `cost_usd` is null, and an `unpriced` badge when `unpriced_count > 0`.
- **AC7f:** The project, Scored only, Root tasks only and day filters narrow the list and reset to page 1.
- **AC7g:** Click or Enter opens the detail (intensity, completion, hierarchy, JEV + 5-bar chart, children, evaluations). Close hides it. An unknown task shows "Task not found."
- **AC7h:** The scatter plots one point per scored task with RS-v1 among the latest 200. `Showing latest 200 of <total> scored tasks` appears when the cap is hit. The empty message appears when there are no points.
- **AC7i:** The status line reflects disabled / config error / enabled + spend / running / budget ceiling / rate-limited-deferred / unavailable.
- **AC7j:** No prompt or note text is ever in the DOM. Hostile strings in any API field render as literal text, in content and in attributes, with no script execution.
- **AC7k:** Empty results show `No tasks` / "No tasks in this window." with no "Page 1 of 0". An out-of-range page clamps to the last page.
- **AC7l:** When responses arrive out of order after rapid filter or detail changes, the view always reflects the latest selection.
- **AC7n (r2 item 11):** After Close, a delayed detail success or error never reopens or alters the panel.
- **AC7o (r2 item 12):** A chart-only failure shows "Could not load chart." and clears the stale chart, and the table stays correct.
- **AC7p (r2 items 10, 13):**
  - An out-of-range page (e.g. page 4 when the total is 120) reloads page 3 exactly once, with the correct rows and button states.
  - The project selector keeps all projects while a project filter is active.
  - The child table renders from the documented `children[]` shape.

## Out of Scope

- Label entry, JEV trigger and purge controls.
- Prompt display.
- Composition charts.
- A full-population chart endpoint (capped at 200 by design).
- A build step or new libraries.
- Changes to existing views.
