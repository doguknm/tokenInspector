A. Information architecture

CP1 was executed against the mandated read-only goal-1 pins. Those commits do not contain the supplied goal-1 contract implementation. The user selected current `feat/cc-agent-usage` HEAD as CP2's baseline; therefore these pin findings are historical and must be rechecked before implementation.

- GET /api/analytics/agents is not found in the pinned commits. The analytics router contains the existing aggregation endpoints and ends with /complexity-matrix; no Agents route is present. Router registration does not add a separate Agents router. Evidence: tokenInspector/routes/analytics.py:16-551; tokenInspector/main.py:89-96.
- The event model has no agent column, and the ingest model has no agent field. Unknown ingest fields are ignored. Evidence: tokenInspector/models.py:13-102; tokenInspector/routes/events.py:81-152.
- Task responses contain start_complexity and start_complexity_method, but agent, agent_conflict and agent_unavailable_calls are not found in the pinned commits. Evidence: tokenInspector/routes/tasks.py:163-196.
- The Claude Code producer explicitly excludes agent names and does not emit agent or complexity fields. Its existing pseudonym function is for correlation identifiers, not the proposed agent-label contract. Evidence: tokenInspector/producers/claude_code/cc_events.py:1-6,18-37,65-110.

Therefore, a fully working Agents feature cannot be delivered against exactly these pins by a dashboard-only job. Do not implement a substitute agent aggregation from roles, exports or task hierarchy. Do not silently implement goal 1 inside this job.

The plan below distinguishes:
- “Pinned”: verified existing implementation, with citations.
- “Contract”: the user-supplied section 3, a target interface whose implementation is not found in the pinned commits.
- “Proposed”: new dashboard behaviour, not a claim about existing code.

Recommended execution boundary: first resolve the missing goal-1 prerequisite. If the pins must remain unchanged, implement only a contract-driven UI with an honest unavailable state and explicitly mocked browser coverage; do not call that a complete feature.

Placement and structure

Proposed nav order:
Overview → Agents → Projects → Models → Complexity → Tasks → Jobs → Settings.

Use one new section, view-agents, and a data-view="agents" link. This matches the existing section/navigation structure and loader dispatch. Evidence: tokenInspector/static/index.html:14-20,27,62,113,125,177,224,279; tokenInspector/static/app.js:48-65.

Above the fold:
1. Heading and period picker.
2. Runtime, project, model, agent and complexity-method filters.
3. Exact UTC interval, scope and coverage warning.
4. KPI cards, including four separate token cards.
5. Start of the sortable agent table.

Below:
- Agent × call-tier charts.
- Start-complexity task panel, explicitly unavailable until backed.
- Token-type stacks and cache-read-share table.
- Daily calls by agent.
- Inline drill-down region, opened beside/below the table with focus moved to its heading.

Desktop wireframe:

```text
+----------------------------------------------------------------------------+
| Overview | AGENTS | Projects | Models | Complexity | Tasks | Jobs | Settings|
+----------------------------------------------------------------------------+
| Agents                              Period: [7d] [30d*] [90d] [Custom]       |
| Runtime [All] Project [All/input] Model [All] Agent [All] Method [All]        |
| Last 30 days | exact UTC from -> to | scope / loading / coverage status      |
| Labels start at goal-1 deployment; older events have no label. [full note]   |
+------------------+------------------+------------------+--------------------+
| Calls            | Distinct tasks   | Priced USD       | Unlabeled calls %  |
| Agents seen      | Unpriced calls   | Estimated calls  | Coverage counts    |
+------------------+------------------+------------------+--------------------+
| Prompt tokens    | Completion       | Cache-read       | Cache-creation     |
+----------------------------------------------------------------------------+
| Agents — selected period | sort [Calls descending] | table page controls     |
| Agent | Calls | Tasks | P | C | CR | CW | /call | /task | USD | ...          |
| main  | ...   | ...   |                 [low sample]              [Details] |
| unknown ...                                                                |
| No label ...                                                               |
+-------------------------------------+--------------------------------------+
| Calls by agent × tier               | Tokens by agent × tier               |
| separate panels for each method    | four explicit token-type datasets    |
+-------------------------------------+--------------------------------------+
| Tasks by START tier                 | Token types by agent + cache share   |
| [Unavailable: missing aggregation] | labelled data table                  |
+----------------------------------------------------------------------------+
| Daily calls — top 5 agent buckets + Other | accessible daily data table     |
+----------------------------------------------------------------------------+
| Detail: selected agent / method / tier                         [Close]       |
| Models | Tiers | Runtime mix | Projects unavailable | [View Jobs by runtime]|
+----------------------------------------------------------------------------+
```

Narrow-width wireframe:

```text
+--------------------------------------+
| Token Inspector                      |
| horizontally scrollable tab links   |
+--------------------------------------+
| Agents                               |
| [7d] [30d*] [90d] [Custom]            |
| Runtime [All]                        |
| Project [All/input]                  |
| Model [All]                          |
| Agent [All]                          |
| Method [All — separate scales]       |
| Exact UTC range / status            |
| Coverage note                       |
+------------------+-------------------+
| Calls            | Distinct tasks    |
| Priced USD       | Unpriced calls    |
| Agents seen      | Unlabeled share   |
| Prompt tokens    | Completion tokens |
| Cache-read       | Cache-creation    |
+--------------------------------------+
| Sort [Calls descending]             |
| Agent table [horizontal scrolling]  |
| sticky agent name / Details button  |
+--------------------------------------+
| Calls × tier — method name           |
| [Chart] [Show data table]            |
+--------------------------------------+
| Tokens × tier — method name          |
| [Chart] [Show data table]            |
+--------------------------------------+
| Start-tier tasks: unavailable        |
+--------------------------------------+
| Token types / cache share            |
+--------------------------------------+
| Daily calls / daily data table       |
+--------------------------------------+
| Inline detail / Close / Jobs link    |
+--------------------------------------+
```

Reuse theme variables, cards, chart boxes, table wrappers, numeric/subtext styling and textual badges; add Agents-specific responsive rules and hidden-state rules. These primitives exist at tokenInspector/static/style.css:3-18,56-80,133-164,166-181.

Visible coverage note, attributed to the supplied contract rather than a verified deployment:
“Agent labels and Claude Code input-size tiers are available only from the goal-1 deployment onward. Older events have no label. hermes-agent events have no agent label. Short hermes -z jobs may lose their final approximately 2 seconds of events.”

Do not invent a deployment date or backfill historical labels.


B. Filters and URL state

G6 confirmed: existing navigation toggles DOM sections, and startup directly calls loadOverview(); URL routing is not found in the pinned commits. Evidence: tokenInspector/static/app.js:48-65,558-559.

Use URLSearchParams, history.replaceState and popstate; no dependency.

Proposed URL schema

| Parameter | Allowed value / default | Contract request mapping |
|---|---|---|
| view | Existing tab keys plus agents; default overview | Not sent |
| period | 7, 30, 90, custom; default 30 for Agents | Resolves from/to; never send days |
| from | UTC ISO datetime; required for custom | from |
| to | UTC ISO datetime; required for custom | to |
| runtime | Repeated members of RUNTIMES; absent = all | Repeated runtime |
| project | One exact project name; absent = all | project |
| model | Repeated exact model strings; absent = all | Repeated model |
| agent | Repeated exact contract agent values; absent = all | Repeated agent |
| agent_missing | Only true is serialized; absent = false | agent_missing=true |
| method | Repeated request-shape-v1 or cc-input-size-v1; absent = all | Repeated method |
| complexity | Optional repeated integers 1–5, populated by drill-down | Repeated complexity |
| include_unscored | true or false; default true | Always send explicitly |

The runtime allowlist is exactly RUNTIMES at tokenInspector/routes/events.py:37. Implement from that code-defined set without renaming its wire values; display infrastructure-neutral labels such as “Claude Code on the build machine.” Infrastructure-identifying literals are intentionally not reproduced here.

Agent validation must not invent a cleaner. The contract requires round-tripping _clean_agent, but that function is not found in the pinned commits. UI-generated options preserve the exact returned value; deep-link values remain untrusted and must receive authoritative server validation once the prerequisite exists.

Period semantics

- Proposed default: 30 rolling days, ending at one captured UTC “now.”
- A preset captures one to value and computes from by subtracting the preset number of days. Store both ISO bounds in the URL.
- Label: “Last 30 days, ending {to UTC}”; always display the exact interval.
- Hard reload reuses serialized bounds instead of shifting the period.
- Pressing a preset again or Refresh captures a new interval.
- Custom controls use explicit UTC datetimes, not implicit local dates.
- Require valid, timezone-qualified bounds, from < to and duration ≤92 days.
- Proposed interval convention: from inclusive, to exclusive. This boundary convention is not established by the supplied Agents contract or pinned implementation; resolve it before live acceptance.
- Existing views use days-based queries and 7/30/90 controls. Evidence: tokenInspector/static/index.html:131-135,229-233; tokenInspector/static/app.js:402-406,973-975.

Option population and stale filters

The contract has no project/model/agent facet arrays. Do not invent data.projects or data.filters.

- Project: exact-value text input with Apply/Clear; no automatic project discovery required.
- Model and agent: suggestions from a separate period-only Agents response, with all non-period filters omitted. Distinguish null agent from literal unknown.
- Runtime/method: fixed contract options; absence of observed calls does not invalidate them.
- If the complete period-only response proves a selected model/agent has vanished, clear it, replace the URL, announce the clear, and issue the corrected main request once.
- Never clear a filter merely because its already-filtered result is empty.
- If option discovery fails or exceeds the cap, retain selections as explicit options. Do not infer staleness.
- Project is never cleared from Agents cells: they have no project dimension under the supplied contract.

This adapts, rather than blindly copies, the existing stale-filter/reissue convention at tokenInspector/static/app.js:507-518,629-637,660-664.

Reload and race behaviour

- Move initial view activation to the end of initialization, after all tab state objects exist.
- One activateView function handles startup, clicks, popstate and the Jobs handoff.
- replaceState canonicalizes committed filters without adding a history entry for every change. Use pushState only for explicit tab/drill navigation where Back should return to the prior view.
- popstate parses the entire state, updates controls and reloads without writing history.
- Invalid URL state produces fixed validation text; do not silently substitute an unfiltered request.
- agentsState owns gen, controller, frozen query, response, sorting and pagination.
- Abort the previous request and increment gen on filter change, navigation away and detail close.
- Main, option-discovery and detail requests have independent generation checks. A stale response must never change options, KPIs, errors, charts or detail content.


C. KPI row

The fields below are supplied-contract fields, not verified response fields in the pins.

Let P mean the visible period phrase, including exact bounds. All KPIs use the same frozen filter snapshot.

| Exact label template | Source/formula | Empty / partial behaviour |
|---|---|---|
| “Calls, {P}” | totals.calls | 0 on successful empty response |
| “Distinct contributing tasks, {P}” | totals.tasks only | 0 when empty; never sum cell tasks |
| “Prompt tokens, {P}” | totals.prompt_tokens | 0 when empty |
| “Completion tokens, {P}” | totals.completion_tokens | 0 when empty |
| “Cache-read tokens, {P}” | totals.cache_read_tokens | 0 when empty |
| “Cache-creation tokens, {P}” | totals.cache_creation_tokens | 0 when empty |
| “Priced cost, USD, priced calls only, {P}” | totals.priced_cost_usd | Null → “— No priced calls”; never convert null to zero |
| “Unpriced calls, {P}” | totals.unpriced_calls | 0 when empty; displayed beside priced cost |
| “Agent labels seen, distinct values, {P}” | Distinct non-null cell.agent excluding literal unknown | 0 when empty; count pseudonyms as labels, not people |
| “Unlabeled call share, % of filtered calls, {P}” | 100 × coverage.agent_unavailable_calls / totals.calls | No calls → “— No calls”, not 0% |

Cost subtext:
- “{priced_calls} priced; {unpriced_calls} unpriced; {estimated_calls} estimated calls.”
- Show estimated_cost_usd separately as “Estimated cost, USD”; never add it to priced cost.
- cost_complete=false means “Incomplete pricing,” not truncated analytics.
- A genuine zero priced cost is valid if priced_calls > 0.
- Calls without task: show totals.calls_without_task beneath the task KPI.

The existing dashboard keeps token types separate and has null-safe numeric formatters, but the new formatter must use the contract’s priced_calls/null semantics rather than copying a positive-cost heuristic. Evidence: tokenInspector/static/app.js:12-30,156-160,892-900.

G2 resolution:
- Denominator is totals.calls for the same filtered response, not labeled calls, token count or a broader period.
- agent_unavailable_calls: null-label calls, per supplied contract.
- agent_unknown_calls: literal unknown-label calls, distinct from null.
- agent_custom_calls: the literal-custom counter described in G3; code verification is not found in the pinned commits. Display “Calls labelled ‘custom’,” never “All custom-agent calls.”
- complexity_unscored_calls: null-complexity calls.
- These counters are not mutually exclusive categories. A call can be both unlabeled and unscored.
- On a successful no-call response, counters must be zero; percentages are undefined and shown as “— No calls.” An inconsistent payload is an error, not a reason to fabricate corrected values.

G3 label policy:
- main → “Main thread.”
- null → “No label.”
- unknown → “Unknown agent.”
- a-<16 lowercase hex characters> → “Pseudonym · {value}”; never present it as a real name.
- custom → “custom — literal label; not a custom-agent total.”
- Other values → exact escaped label; do not assert “verified built-in” or “allowlisted custom” without classification metadata. The verified name list and cleaner are not found in the pinned commits.


D. Charts

Shared rules

Use native Chart.js bar/line charts only. The pinned page loads Chart.js 4.4.3, and existing chart constructions use bar, line, doughnut and scatter. Evidence: tokenInspector/static/index.html:8; tokenInspector/static/app.js:105-110,128-129,253-254,423-425,712-714.

Reuse CHART_COLORS and TOKEN_TYPES colours; text/grid colours come from computed CSS variables. Evidence: tokenInspector/static/app.js:2,12-17; tokenInspector/static/style.css:3-18.

Every chart:
- Has a visible title naming metric, unit and period.
- Has a semantic HTML data table, keyboard-accessible “Show data” button and canvas accessible name.
- Uses names, numeric values and axis labels in addition to colour.
- Uses real categories, never a one-number doughnut/gauge.
- If only one meaningful scalar would remain, shows the data table instead of drawing a chart.
- Retains calls/tokens when cost is unpriced; pricing absence is not zero usage.
- Destroys the prior chart before rebuilding, following tokenInspector/static/app.js:39-41.
- Shows no chart on request failure or incomplete response.

D1. Agent × call-tier calls and tokens

Question: which agent labels contribute calls and each token type at each call-complexity tier?

Construction:
- Separate panel pair for each method returned in cells.
- Calls: stacked bar; x = C1…C5 plus Unscored; y = calls; dataset per agent bucket.
- Tokens: stacked bar; x = agent/tier category; y = tokens; four TOKEN_TYPES datasets.
- Method is part of every aggregation key. Never merge C1 from different methods.
- Null method gets a separate “No complexity method” data table, not an inferred scale.

Exact title templates:
- “Calls by agent and call tier — {method} — {P}.”
- “Tokens by type, agent and call tier — {method} — {P}.”

G4: when all methods are selected, show “Separate, non-comparable tier scales.” Overall calls/tokens may aggregate methods, but no tier mean, shared scale ranking or pooled tier series is permitted.

G8: no heatmap plugin. Native stacked bars plus their matrix table satisfy the requirement.

Non-colour channels:
- Agent dataset names, explicit C1–C5 labels, numeric table and method titles.
- Token stack segment names and four numeric token columns.
- Table cells provide keyboard drill-down; canvas clicking is optional.

Empty/partial:
- No calls for a method → “No calls for this complexity method.”
- Unscored data stays visible; null tier is not C1.
- Mixed pricing adds a note but does not alter call/token datasets.

D2. Tasks per tier by start complexity

Question: where did distinct contributing tasks start, rather than which tiers their later calls reached?

Current decision: render an unavailable panel, not a fabricated chart:
“Tasks by start complexity are unavailable: the Agents contract does not include task-start buckets.”

The Tasks API has start fields, but selects tasks by last_seen_at, supports different filters and aggregates their events without the Agents interval. It cannot be silently substituted for this filtered chart. Evidence: tokenInspector/routes/tasks.py:72-91,188-189,201-249.

If a separately approved backend aggregation later exists:
- Native bar chart, one panel per start_complexity_method.
- x = start C1…C5 plus Unscored; y = distinct contributing tasks.
- Numeric start-tier table is the non-colour equivalent.
- No stacking by agent unless cross-agent task duplication is explicitly represented.
- No new route name or response field is assumed by this plan.

D3. Token-type stacks per agent and cache-read share

Question: what token categories account for each agent’s usage, and how much input comes from cache reads?

- Horizontal stacked bar; y = agent; x = tokens.
- Four datasets: prompt, completion, cache-read, cache-creation.
- Use TOKEN_TYPES colours; keep full category names in the legend.
- Companion table columns: the four counts and “Cache-read share, % of input tokens.”

Formula:
cache_read_tokens / (prompt_tokens + cache_read_tokens + cache_creation_tokens) × 100.

Completion tokens are excluded from that denominator. Zero input denominator → “— No input tokens.”

Non-colour channels: agent names, labelled segments/tooltips and exact table values. The share is a numeric column, not a one-number chart.

Empty/partial: retain No label and Unknown agent rows. Zero-token calls still appear in the calls table; show “No recorded tokens” instead of an all-zero token chart.

D4. Daily calls by agent

Question: how does observed call volume vary across the period?

- Native line chart; x = response day; y = calls.
- Select top 5 agent buckets by total calls over the whole response, ties by canonical key.
- Sum remaining buckets into one “Other agent labels” series.
- Keep ranking stable across days.
- Null and unknown participate normally; neither is discarded.
- Distinguish series with line dashes/point styles as well as colours.
- Use stable agent-colour assignment shared with D1.
- Daily data table includes every plotted series and exact counts.

Do not label day boundaries as UTC until the Agents time/day semantics are verified. The proposed UTC request interval does not prove the missing endpoint’s bucketing implementation.

Empty/partial: fill interior absent days with zero only after a complete successful response and verified day-boundary semantics. For a single-day/single-scalar result, show the table instead.


E. Sortable table

G1 correction: additive metrics are computable from contract cells; distinct task rollups and error rates are not generally computable from those cells.

For each exact agent key, including separate null and unknown buckets:
- Sum calls, calls_without_task, each token category and pricing counters.
- Priced cost = sum non-null cell.priced_cost_usd only when summed priced_calls > 0; otherwise null.
- Top model = model with greatest summed calls, ties lexicographically.
- Runtime breakdown is computable because runtime is a cell dimension.
- Distinct tasks cannot be recovered by adding cell.tasks: the same task can contribute on several days, models and tiers.
- Error numerator/status is absent from the supplied contract.
- Latency is not wholly missing: latency_calls, avg_process_time_ms, ttft_calls and avg_ttft_ms permit count-weighted averages of reported cell means, potentially affected by server rounding.

Proposed table caption:
“Agent usage — {P}. Tokens are separate categories. Task-derived metrics may be unavailable. Mixed methods are not a shared tier scale.”

| Header | Unit / formula | Availability |
|---|---|---|
| Agent | Exact label bucket | Always; button opens detail |
| Calls | calls; sum cells.calls | Sortable |
| Tasks, distinct | distinct tasks | See task rule below |
| Prompt tokens | tokens; sum prompt_tokens | Sortable |
| Completion tokens | tokens; sum completion_tokens | Sortable |
| Cache-read tokens | tokens; sum cache_read_tokens | Sortable |
| Cache-creation tokens | tokens; sum cache_creation_tokens | Sortable |
| Prompt / call | tokens/call; prompt / calls | Null if no calls |
| Completion / call | tokens/call; completion / calls | Null if no calls |
| Cache-read / call | tokens/call; cache-read / calls | Null if no calls |
| Cache-creation / call | tokens/call; cache-creation / calls | Null if no calls |
| Prompt / task | task-attributed prompt tokens / distinct tasks | Usually unavailable |
| Completion / task | task-attributed completion tokens / distinct tasks | Usually unavailable |
| Cache-read / task | task-attributed cache-read tokens / distinct tasks | Usually unavailable |
| Cache-creation / task | task-attributed cache-creation tokens / distinct tasks | Usually unavailable |
| Priced cost, USD | Nullable priced sum | Pair with pricing counters |
| Unpriced calls | sum unpriced_calls | Sortable |
| Priced cost / task, USD | task-attributed priced cost / distinct tasks | Usually unavailable |
| Error rate, % of calls | error calls / calls ×100 | Unavailable |
| Top model, by calls | model argmax; show its call count | Sortable by model name |
| Sample | “Low sample: fewer than 5 calls” | Proposed call-based rule |

Task rule:
- For the normal multi-agent table, show “— Not available from cells” for distinct tasks and task-derived metrics. Disable those sort controls.
- When the response is explicitly scoped to exactly one agent or agent_missing=true, totals.tasks supplies that row’s exact count.
- For that single-agent scope, per-task token/cost arithmetic is allowed only when totals.tasks > 0 and totals.calls_without_task == 0. Otherwise the task-attributed numerator is missing.
- Never substitute “sum of cell task counts,” “average of per-cell averages” or whole-task totals from /api/tasks.

Optional exact per-agent task counts can be obtained by issuing one contract request per agent and reading each totals.tasks. Recommendation: use this only on detail opening, not an unbounded request fan-out to make every table column sortable.

Default sort: Calls descending, then canonical agent key ascending. Null metrics sort last in either direction; numeric comparisons use unrounded values. Use header buttons and aria-sort. Client-side pagination: 25 rows, without silently dropping remaining agents.

Low-sample mark: textual badge in the Agent cell plus tr.low-sample. The proposed threshold is call-based, deliberately different from the existing Complexity threshold of fewer than 5 tasks. Existing threshold and styling evidence: tokenInspector/routes/analytics.py:386,457; tokenInspector/static/app.js:468-472. Do not present a call-based mark as task-based evidence.


F. Drill-down

Open a labelled inline region from:
- Agent row’s Details button.
- Agent × tier data-table cell.
- Model entry inside a detail region.

Show a breadcrumb of active period/runtime/project/model/agent/method/tier scope. Close restores focus and invalidates pending detail requests.

Requests and content:
1. Agent detail:
   - GET /api/analytics/agents with frozen parent filters and exactly that agent, or agent_missing=true.
   - Show exact detail totals, distinct contributing tasks, calls without task, token splits, pricing coverage.
2. Tier-cell detail:
   - Same request plus one method and one complexity.
   - Keep the named method prominent.
3. Models:
   - Group detail cells by model for calls, tokens and priced cost.
   - Do not sum model task counts into a distinct task total.
4. Tiers:
   - Group by method and complexity; null method/tier remain explicit.
5. Runtime mix:
   - Group additive detail metrics by runtime.
6. Projects:
   - Show the selected project if there is one.
   - Otherwise: “Per-project breakdown unavailable from this response.”
   - No call to /by-project is allowed to masquerade as an agent-filtered breakdown.
7. Main/subagent:
   - Show “Main-labelled calls,” “Other labelled calls,” “Unknown agent” and “No label” with shares of detail calls.
   - Do not rename all non-main labels “subagents.” Exact structural main/subagent share is a gap without role/hierarchy provenance in the contract.

G5 confirmed, with an important qualification: /api/jobs accepts days, runtime, work_type, page and page_size. It has no agent, project, model, method or from/to filter; runtime filtering retains entire jobs. Evidence: tokenInspector/routes/jobs.py:138-164; whole-job time semantics at tokenInspector/routes/jobs.py:1-6,81-84.

Honest Jobs action:
- One “View Jobs for this runtime” button per valid runtime; never arbitrarily choose among several runtimes.
- Set Jobs runtime, clear unrelated work_type, reset page to 1.
- Carry 7/30/90 preset duration when applicable.
- For custom Agents intervals, use Jobs’ default 30-day view and say so before navigation.
- Persist the handoff as view=jobs, jobs_runtime and jobs_days; these are proposed dashboard URL keys, not extra backend parameters.
- Display: “Runtime filter only. Jobs contain whole-job totals; agent, project and exact interval filters are not carried.”
- No-runtime data gets no misleading filtered Jobs button.

Precise job drill-through needs a separate backend job defining agent/project/interval filtering and whole-job versus matching-call totals.


G. Missing aggregations

| Requirement / gap | Options | Recommendation |
|---|---|---|
| Entire Agents API, event agent storage and goal-1 producer mapping | Correct prerequisite/pins; or ship unavailable UI shell | Block full acceptance. Implementation is not found in the pinned commits; see A |
| Per-agent calls/tokens/pricing/model/runtime rollups | Client reduction of contract cells; new backend rollups | Client reduction; no additional aggregation needed once contract exists |
| Per-agent distinct tasks | Per-agent filtered totals requests; server rollup; leave unavailable | Detail request now; dedicated aggregation later for globally sortable task column |
| Per-agent token/task and cost/task | Incorrect all-call numerator; task-attributed sums; restrict to no-orphan single-agent scope | Restrict safe case; backend aggregation for complete table |
| Error rate | Reuse unrelated aggregate; guess from missing data; provide error numerator | Unavailable; separate backend aggregation required |
| Start-tier task chart | Reinterpret call tiers; scrape differently filtered Tasks API; add aligned task-start aggregation | Unavailable; separate backend aggregation required |
| Per-project agent breakdown | Fan out over a trustworthy project universe; add project grouping | Backend grouping recommended; project dimension absent from contract cells |
| Verified built-in versus allowlisted-custom classification | Guess from names; publish authoritative classification metadata | Display exact unclassified labels until metadata exists |
| Main-thread versus actual subagent share | Approximate from non-main labels; provide explicit attribution | Show labelled-main/non-main coverage only; backend attribution for structural share |
| Exact Jobs drill-through | Runtime-only navigation; add richer Jobs filters | Runtime-only with warning now; backend job for precision |
| Filter facets under exact Agents scope | Derive model/agent suggestions from complete base cells; add facets | Derive suggestions; project text input; future facets optional |
| Latency rollups | Weighted reported means; exact server sums | Optional detail only, labelled approximate if means are rounded |
| Deployment timestamp / completeness loss | Invent a date or correct missing usage; publish deployment/coverage metadata | Static warning only; no numeric recovery |
| Contract metadata literal values and interval boundaries | Guess; obtain implemented contract/test evidence | Prerequisite verification; not found in the pinned commits |

Existing aggregations are not interchangeable substitutes:
- /by-model and /by-project use days and have no agent filtering.
- /by-role groups role, not agent.
- /complexity-matrix has errors and task-attributed averages but no agent/runtime/absolute-period filtering.
Evidence: tokenInspector/routes/analytics.py:103-118,157-159,194-218,429-457,482-513.

Response-field handling for the target contract:
- contract_version: validate against the restored implementation; its expected literal is not found in the pinned commits.
- period.from/to: verify against requested normalized bounds and display them.
- time_basis, tier_source, task_count_semantics: show safely as provenance; do not invent their values.
- methods: observed-method information, not a reason to merge scales.
- coverage: dedicated counters and unlabeled-share calculation.
- totals: authoritative global counts, costs and averages.
- cells: additive chart/table inputs keyed by runtime, agent, complexity_method, complexity, model and day.
- complete: require true before treating data as complete.
- latency_calls/avg_process_time_ms and ttft_calls/avg_ttft_ms: preserve measurement denominators.
- estimated_calls/estimated_cost_usd: separate from priced usage.
- cost_complete: pricing completeness only.

No proposed backend field or route is silently added to the goal-1 contract.


H. States

Loading:
- Show “Loading agent usage…” in role=status; mark results aria-busy.
- Clear old numeric results and charts, or hide them as stale. Never display old results under new filter labels.
- Preserve controls and the coverage note.

Empty:
- A valid, complete response with zero calls shows “No calls for these filters and period.”
- Count/token KPIs are zero; percentages and averages are undefined.
- No rows and no misleading charts.
- This is distinct from a missing endpoint.

Partial coverage:
- Some unpriced calls: retain usage, show priced-only amount plus unpriced and estimated counts.
- Some unlabeled calls: retain No label bucket and coverage counters.
- Some unscored calls: retain Unscored/no-method information.
- Some cells with complete=true is a valid filtered result, not automatically truncation.
- complete=false: no normal result render. Show a fixed incomplete-result error.

Errors, fixed text:
- 400: “Invalid agent filters or period.”
- 413: “Too many agent cells. Narrow the period or filters.”
- Missing endpoint: “Agent analytics is unavailable in this backend.”
- Network/other HTTP/malformed payload: “Could not load agent analytics.”
- Unsupported contract: “Agent analytics contract is not supported.”

Never display response text, exception messages or arbitrary error codes. Use textContent for dynamic plain text and esc() for HTML interpolation. Existing escaping and closed Jobs-error patterns: tokenInspector/static/app.js:573-575,960-985.

Latest request wins:
- Check generation after fetch, after JSON parsing, before every render and in catch/finally.
- AbortController is an optimization, not the correctness mechanism.
- A stale error cannot erase newer success.
- Closing detail, leaving Agents or changing filters invalidates all affected work.
- Option discovery cannot reset filters after a newer user selection.

Chart library unavailable:
- Keep KPIs and semantic tables usable.
- Show “Charts unavailable; data tables are available.”
- Do not let chart initialization break navigation.


I. Browser tests

G10 confirmed:
- Shared server is session-scoped and seeds a demo DB.
- Browser is module-scoped.
- Jobs tests use their own module-scoped server, ingest seeding, per-test contexts and fabricated responses.
Evidence: tokenInspector/tests/browser/conftest.py:25-59; tokenInspector/tests/browser/test_jobs_view.py:1-4,46-105,201-237.

Proposed module: tokenInspector/tests/browser/test_agents_view.py.

Fixtures/helpers:
- agents_server(tmp_path_factory): isolated DB and server; no shared-demo mutation.
- page(browser, agents_server): fresh context, route cleanup, console/pageerror capture.
- _seed_agents: ingest-based integration data only after the prerequisite exists; assert acknowledgements and actual API response.
- _metrics, _cell, _agents_body: explicit supplied-contract fixture factories, no extra response fields.
- _open_agents, _settled_agents, _agent_row, _chart_data.
- _capture_queries using a proper repeated-query parser.
- _install_out_of_order_fetch: delayed response that ignores abort.
- Fixed browser clock for preset-range assertions.
- Neutral project names; no copied infrastructure-specific test data.

Mocked responses prove UI behaviour only. They must not be reported as proof that the pinned backend implements Agents.

| Test | State and data | Exact red assertion |
|---|---|---|
| test_agents_navigation_reload | Deep link with all filter classes | Agents visible, Overview hidden, first main query matches URL |
| test_agents_preset_period | Fixed clock; 7/30/90 controls | Exact from/to for each preset; no days parameter sent |
| test_agents_custom_period_validation | Valid custom, reversed, invalid and >92-day bounds | Invalid forms send no request and show fixed validation text |
| test_agents_filters_wire_roundtrip | Parameterize runtime/project/model/agent/method, repeated values, missing agent and tier | Parsed request equals committed state; agent and agent_missing never coexist |
| test_agents_popstate | Two navigation states, then Back | Controls, URL and request restore the same previous scope |
| test_agents_stale_options | Complete period-only options omit selected model | Selection cleared; corrected request sent once; URL agrees |
| test_agents_empty_preserves_valid_filter | Empty filtered result, selected value still in base options | Filter retained; empty state visible; no broadening request |
| test_agents_kpis_and_token_splits | Contract fixture: prompt 10, completion 30, cache-read 20, creation 70; 10 calls | Four separate values visible; labelled sum, if shown, is 130; prompt/call is 1 |
| test_agents_distinct_tasks_not_cell_sum | Two cells each task count 1, totals.tasks=1 | Global task KPI is 1, never 2; multi-agent table does not claim summed distinct counts |
| test_agents_unpriced_never_zero | All-unpriced, mixed, estimated-only and genuine priced-zero fixtures | All-unpriced text contains “No priced calls,” not “$0.0000”; mixed row exposes unpriced count; genuine priced-zero is allowed |
| test_agents_coverage_labels | Null, unknown, pseudonym, literal custom; unavailable=2 of 10 calls | No label and Unknown agent both present; share is 20%; pseudonym labelled as pseudonym |
| test_agents_low_sample | Separate 4-call and 5-call rows | First row has low-sample class and textual badge; second does not |
| test_agents_sort_numeric | Multi-row unequal metrics and null costs | Correct numeric order, aria-sort updated, null costs last |
| test_agents_methods_and_tiers_separate | Both methods, overlapping tier numbers, multiple tiers and null method | Each chart names its method; dataset values preserve method/tier keys; null method remains separate |
| test_agents_cache_share | 10 prompt, 20 cache-read, 70 creation, 30 completion | Share is 20%; changing completion alone does not change it |
| test_agents_daily_top_other | More than five agent buckets across multiple days | Exactly top five plus Other; each daily plotted sum equals fixture daily calls; null/unknown not lost |
| test_agents_unavailable_metrics | Valid contract cells without error/start/project fields | Unavailable explanations visible; no invented error rate, start-tier chart or project totals |
| test_agents_detail_scope | Open agent then method/tier cell | Detail request contains exact scope; details show response totals.tasks |
| test_agents_jobs_handoff | One/multiple runtime detail, preset/custom periods | Chosen runtime sent to /api/jobs; agent/project/method absent; whole-job warning visible |
| test_agents_empty | Complete zero-call response | Empty text visible, table rows zero, chart instances absent |
| test_agents_closed_errors | 400/413/404/500/malformed with body canary | Fixed expected message; canary absent from page content |
| test_agents_latest_request_wins | Old delayed response ignores abort; new response resolves first | New row/KPI/chart/options remain after old response resolves |
| test_agents_late_error_and_close | Old rejected request; detail closed before response | New state retained; detail stays closed |
| test_agents_incomplete_response | complete=false | No result table/chart rendered; incomplete-result text visible |
| test_agents_accessibility_and_narrow | Narrow viewport, keyboard navigation, single-scalar and chart-library failure | No document-level horizontal overflow; controls labelled; data tables usable; no one-number canvas |
| test_agents_escape_labels | Malicious-looking agent/model strings in fixture | Literal text visible; no injected element and no execution sentinel |
| test_agents_live_contract_gate | Real isolated backend, no route interception | Require successful documented Agents response before full-feature acceptance |
| test_agents_cross_view_regression | Navigate all tabs | No pageerror/console error; existing views still load |

Against the current pins, the live-contract gate is expected to expose the missing prerequisite. Do not skip it and claim full delivery.


J. Mutation targets

| Break X | Test Y must go red |
|---|---|
| Unpriced null rendered as $0 | test_agents_unpriced_never_zero |
| Estimated cost included in priced cost | test_agents_unpriced_never_zero |
| Genuine priced zero labelled unpriced | test_agents_unpriced_never_zero |
| Low-sample badge or row class hidden | test_agents_low_sample |
| Any filter omitted from request | test_agents_filters_wire_roundtrip |
| Repeated filters collapsed to one value | test_agents_filters_wire_roundtrip |
| Null-agent filter sent as literal unknown | test_agents_filters_wire_roundtrip |
| Period picker sends days instead of from/to | test_agents_preset_period |
| Deep link reload opens Overview | test_agents_navigation_reload |
| popstate leaves stale controls | test_agents_popstate |
| Empty result clears a still-valid filter | test_agents_empty_preserves_valid_filter |
| Cleared stale option does not reissue | test_agents_stale_options |
| Empty state absent | test_agents_empty |
| Error text echoes server body | test_agents_closed_errors |
| Slow older response overwrites newer response | test_agents_latest_request_wins |
| Stale error clears newer success | test_agents_late_error_and_close |
| Closed detail reopens on late response | test_agents_late_error_and_close |
| Chart title omits complexity method | test_agents_methods_and_tiers_separate |
| Tiers or methods pooled into one series | test_agents_methods_and_tiers_separate |
| Null tier assigned C1 | test_agents_methods_and_tiers_separate |
| Cache share denominator includes completion or excludes creation | test_agents_cache_share |
| Cell tasks summed into global task total | test_agents_distinct_tasks_not_cell_sum |
| Unknown or No label row dropped | test_agents_coverage_labels |
| Literal custom counter presented as all custom usage | test_agents_coverage_labels |
| Other series loses lower-ranked agents | test_agents_daily_top_other |
| Numeric sort uses formatted strings | test_agents_sort_numeric |
| Jobs link implies agent/project filtering | test_agents_jobs_handoff |
| complete=false rendered as complete | test_agents_incomplete_response |
| A one-number chart is drawn | test_agents_accessibility_and_narrow |
| Label escaping removed | test_agents_escape_labels |
| Mocked endpoint mistaken for live implementation | test_agents_live_contract_gate |


K. Decisions (answered by user)

All six CP1 decisions were answered by the user on 2026-10-03 and recorded in `Plans/cc-agent-usage/status.md` under “Decisions, goal 2”. The answers select current goal-1 HEAD for CP2, a separately scoped backend aggregation for metric gaps, a dedicated per-agent distinct-task rollup, a low-sample threshold of fewer than 5 calls, runtime-only Jobs navigation with an explicit warning, and neutral agent labels without inferred role classification.

The additional user decisions resolve the four questions presented after the initial CP1 report: use current `feat/cc-agent-usage` HEAD, complete missing backend aggregations before full acceptance, add a dedicated per-agent distinct-task aggregation, and define low sample as fewer than 5 calls.


L. Open risks / not-found items

1. GET /api/analytics/agents, _clean_agent, agent coverage counters, agent storage, cc-input-size-v1 implementation and the named goal-1 plan file are not found in the pinned commits. Relevant implementation boundaries: tokenInspector/routes/analytics.py:16-551; tokenInspector/routes/events.py:81-152; tokenInspector/models.py:13-102.

2. G1–G4 cannot be confirmed as existing backend behaviour. They can only be evaluated against the supplied contract. In particular:
   - Additive client aggregation is feasible under that contract.
   - Distinct tasks and errors are not recoverable from cell counts alone.
   - Literal-custom and agent pseudonym semantics are not verified by the pinned producer.
   Evidence for the differing producer: tokenInspector/producers/claude_code/cc_events.py:1-37,65-110.

3. The plugin does emit request-shape complexity and does not add an agent label in its event mapping. Evidence: token_inspector/mapping.py:135-187. Do not infer that all historical complexity methods are null: request-shape complexity already appears in this pinned plugin.

4. The approximately two-second loss warning is supplied operational context, not a measured guarantee established here. The pinned plugin has a two-second default flush interval, bounded shutdown flush/join and an atexit callback. Evidence: token_inspector/config.py:14-17; token_inspector/sink.py:358-374,395-408; token_inspector/__init__.py:455-469. Exact loss probability/window is not found in the pinned commits.

5. G9 is only partially confirmed: textual badges and numeric subtext exist, but not every existing chart has a displayed legend. Evidence: tokenInspector/static/app.js:136,309,440-442,468-479. The new tab must provide its own accessible data equivalents rather than assuming canvas legends suffice.

6. Agent contract_version literal, time_basis value, tier_source value, task_count_semantics value, interval boundary convention and day timezone are not found in the pinned commits. Do not fill them with plausible constants.

7. Exact built-in names and allowlisted-custom classification are not found in the pinned commits. Pseudonym-looking strings must not be treated as real names or unique people.

8. Cross-request snapshots are unspecified by the supplied Agents contract. Main results, option discovery and detail totals may differ if ingestion continues; never sum them together or claim atomic consistency.

9. Implementation targets, after resolving the agreed backend gaps:
   - `static/index.html`: Agents navigation and view; existing anchors are cited above.
   - `static/app.js`: view activation, URL state, Agents loader and renderers; existing dispatch is cited above.
   - `static/style.css`: Agents-scoped responsive, focus and hidden-state rules; existing primitives are cited above.
   - `routes/analytics.py`: separately approved, contract-tested exact rollups for missing metrics.
   - Proposed browser test module: `tests/browser/test_agents_view.py`, plus focused analytics endpoint tests.
   - No plugin changes. Preserve goal-1 data contract; extend only through an approved additive aggregation contract.

The current goal-1 branch HEAD is the selected CP2 baseline. Recheck the goal-1 API against that HEAD before build: the CP1 historical pins predate goal-1 and do not prove what is present now. Full acceptance remains blocked on implementing and testing the agreed missing backend aggregations. No implementation or test execution is claimed.
