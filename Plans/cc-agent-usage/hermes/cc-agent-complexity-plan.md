# Claude Code Agent Identity and Per-Call Complexity — Read-Only Analysis and Implementation Plan

## Scope and conclusions

This report covers the producer, ingest, storage, task derivation, read APIs, and export. It does not propose an Agents dashboard implementation.

Citations use `B/` for the backend worktree and `P/` for the Hermes plugin worktree. Paths in citations and implementation targets are source-relative identifiers, not captured telemetry metadata. All implementation details below are **proposals**, unless explicitly described as current behavior. The supplied corpus measurements are treated as given; they were not re-measured.

### Recommended direction

1. **Separate an agent label from a run identifier.** Existing Claude Code child session/turn identifiers already distinguish runs; a new `agent` field should identify the agent category/name, subject to privacy policy. Current child identity is derived from session plus agent ID, not an agent name.  
   Evidence: `B/producers/claude_code/cc_events.py:88–95` — `"session_id": child`, `"turn_id": child`.

2. **Do not promise a named label for every run.** None of the four candidate sources is proven by these worktrees to supply a type/name across every backlog, resume, fork, and nested-run scenario. In particular, sidecar presence does not establish its label field or lifecycle guarantees.  
   Evidence: `B/Plans/o9-job-correlation-cc-producer/status.md:74` — “a `.json` sidecar next to it is never read”; `B/producers/claude_code/cc_transcript.py:127` reads `agentId`, not an agent-type field.

3. **Add a nullable event column in schema v13; derive task agent information from events.** Avoid consuming additional tag budget or duplicating agent state in `tasks`. Current tag limits are explicitly constrained and require measurement before expansion.  
   Evidence: `B/routes/events.py:32` — `TAG_BYTES_MAX = 1024`; `:220–221` — “at most 20 keys”; `B/docs/adr/003-job-correlation-contract.md:41` — “Measure again before adding a tag key.”

4. **Use a separately named numeric-only Claude Code complexity method.** Recommend `cc-input-size-v1`: five ordinal tiers based on normalized input usage including cache. This avoids inventing request-message counts or treating missing fork prompts as empty prompts. It is not equivalent to Hermes request-shape complexity.  
   Evidence: CC already extracts all three input categories at `B/producers/claude_code/cc_transcript.py:116–120`; Hermes requires three different inputs at `P/complexity.py:6–11`.

5. **Build `GET /api/analytics/agents` in goal 1**, with method-separated cells, unknown/unavailable coverage, distinct task counts, counted-event exclusions, and nullable priced cost. Goal 2 should only consume that contract.  
   Evidence for reusable semantics: `B/routes/jobs.py:34–38` — counted-event predicate; `B/routes/analytics.py:447–455` — nullable priced cost and completeness.

6. **Default coverage: from deploy on.** Do not replay old event IDs to enrich them. Any separately approved historical enrichment must be a backend maintenance operation with transactionally coupled export revision invalidation.  
   Evidence: `B/routes/events.py:389` — `on_conflict_do_nothing()`; `B/docs/adr/005-export-contract.md:43–47` — revision bump and `snapshot_expired`.

---

## A. Agent identity

### A1. Candidate sources and reliability

The current implementation reads hook metadata to locate transcripts and resolve project context. It does not consume agent-type metadata:

- `B/producers/claude_code/cc_hook.py:215–225`: reads `transcript_path`, `cwd`, and `agent_transcript_path`.
- `B/producers/claude_code/cc_hook.py:221`: `ctx = {"project": project, "project_source": source, "job": cc_config.job_tags()}`.
- `B/producers/claude_code/cc_events.py:3–6`: current exclusion includes “skill or agent names.”

| Source | Current evidence | Backlog without that run’s SubagentStop | Resume | Forks and nested subagents | Verdict |
|---|---|---|---|---|---|
| **SubagentStop `agent_type` / `agent_id`** | The recorded hook schema includes both, but current code does not read them. `B/Plans/o9-job-correlation-cc-producer/status.md:73` — “`agent_type` [is] never read.” | **Not sufficient alone.** It exists only on the relevant invocation unless previously persisted. A different hook must not lend its label to the drained run. | An existing persisted binding can be reused for the same run; availability of a fresh type value on every resumed invocation is **unknown from code**. | The target can be associated with its own hook metadata, but universal type delivery for forks/nested runs is **unknown from code**. | Useful, precisely bound enrichment source—not a universal source. `agent_id` identifies a run, not its type. |
| **The subagent transcript’s own first lines** | Reader extracts `agentId` from retained assistant records; filename suffix is a local fallback. `B/producers/claude_code/cc_transcript.py:127`; `B/producers/claude_code/cc_hook.py:110–111`. | Run ID remains recoverable when the transcript is available. A type/name field is **unknown from code**. | Same run identifier can support a stored binding; copied runs must retain normal dedup behavior. | A fork fixture begins with a context-reference record, not a task prompt. `B/tests/test_cc_events.py:139–150` — “fork, only tool results.” No nested type schema is evidenced. | Good for binding identity; not evidence of a readable agent label. Do not infer a type from `agentId`. |
| **Per-run sidecar** | The supplied corpus says every observed subagent run has one. Repository documentation says it is not read. `B/Plans/o9-job-correlation-cc-producer/status.md:74`. | **Best candidate for independent recovery**, provided a verified label field exists and the sidecar remains available. Those conditions are not established by the code. | Whether it is retained/copied/updated correctly is **unknown from code**. | Label fields and lifecycle behavior for forked/nested runs are **unknown from code**. | Preferred durable source to investigate, but **schema verification is a prerequisite**, not an assumed fact. |
| **Parent Agent tool-use input, paired to the result** | Current reader counts `tool_use` blocks and their IDs; it does not read their inputs. `B/producers/claude_code/cc_transcript.py:228–235` — `group.tool_ids.add(block["id"])`. | Could work only if the correct parent transcript and an unambiguous call/run association can be recovered. No such resolver exists. | Parent discovery and pairing after copied history are **unknown from code**. | Requires the immediate parent and exact spawning call, especially for multiple children in one turn. These associations are **unknown from code**. | Do not use as the goal-1 default. A parent `promptId` association establishes a turn, not necessarily a unique spawning tool call or agent label. |

The supplied 60/60 parent-prompt association does not prove a one-to-one mapping from a turn to an agent type. Current code deliberately uses that association for task correlation, not label recovery.  
Evidence: `B/producers/claude_code/cc_events.py:94–95` — `parent_session_id` and `parent_turn_id`; `B/Plans/o9-job-correlation-cc-producer/status.md:74` — “Parent link.”

#### Proposed resolution policy

Use a **run-local resolver**, not a hook-global label:

1. Main transcript under the supported layout: generate `main`.
2. Subagent: consult a previously validated, matching run binding.
3. Accept matching SubagentStop metadata for that exact target.
4. Consult a bounded, verified sidecar adapter when its schema has been established.
5. Otherwise emit `unknown`.
6. Do not parse parent tool inputs in goal 1.
7. Conflicting reliable sources must not silently overwrite one another: retain the established binding, increment a numeric conflict counter, and require investigation.

The distinction is important: this provides a safe bucket for every **emitted** run, not a guaranteed human-readable agent name for every run.

Current discovery also has a coverage boundary: it scans one session’s subagent directory and sibling transcripts, not an arbitrary recursive graph. Do not claim complete nested-run discovery merely by adding a label resolver.  
Evidence: `B/producers/claude_code/cc_hook.py:165–178` — “this session’s subagent files … then sibling transcripts”; `:203–204` — one bounded processing call per candidate.

### A2. Persisted `ctx`: exact keys and integration points

**Proposed additional keys:**

| Key | Value |
|---|---|
| `agent` | Sanitized label: `main`, `custom`, `unknown`, or an approved safe name |
| `agent_source` | Closed enum: `main`, `hook`, `sidecar`, `unknown` |
| `agent_run_key` | Existing pseudonymized run identity; never the raw agent ID or transcript location |
| `agent_policy_version` | Integer `1` |

No raw label, metadata object, tool input, or location belongs in `ctx`.

**Write/read points:**

1. **Build per-target context in `_run`.** Keep project/job context separate from agent context. Do not add the SubagentStop label to the single shared `ctx` and pass it to every target. Current code passes the same context to both targets.  
   Evidence: `B/producers/claude_code/cc_hook.py:221–231` — `process_file(run, target, ctx=ctx)`.

2. **Load and merge under the existing per-transcript lock in `process_file`.** Preserve a matching stored agent binding even when `_drain` supplies current project context. Today stored context is used only when `ctx is None`.  
   Evidence: `B/producers/claude_code/cc_hook.py:85–98` — `ctx = state["ctx"] if state else None`.

3. **Bind incoming hook metadata to the target run before using it.** Compare its run ID against available transcript metadata/local target identity. A mismatch must yield no label enrichment.

4. **Extend `_valid_ctx` explicitly.** It currently returns only three keys, so merely adding fields in the hook would silently discard them.  
   Evidence: `B/producers/claude_code/cc_state.py:94` — `return {"project": project, "project_source": source, "job": clean_job}`.

5. **Save agent context with every existing checkpoint**, including pending-budget and acknowledged-batch checkpoints.  
   Evidence: `B/producers/claude_code/cc_hook.py:115–117` — `cc_state.save(..., ctx=ctx)`; `:153–158` — batch/window checkpoint sites.

6. **Clear/re-resolve the binding on file replacement, truncation reset, or run-key mismatch.** Never carry an old run’s label into a new run at the same local location.  
   Evidence: `B/producers/claude_code/cc_hook.py:104–109` — `offset, turn = 0, None`.

7. **Accept old state without new keys.** Re-sanitize stored labels under current policy before transmission; disabling custom-name permission must also affect pending backlog.

8. Preserve state bounds and atomicity.  
   Evidence: `B/producers/claude_code/cc_state.py:21–22` — `MAX_STATE_FILES = 512`, `MAX_STATE_BYTES = 4096`; `:124` — `os.replace(tmp, path)`.

### A3. Taxonomy and value shape

| Category | Proposed representation | Evidence/limit |
|---|---|---|
| Main thread | `main` | Producer-owned structural label; retain existing `role="primary"` separately. `B/producers/claude_code/cc_events.py:99`. |
| Built-in types | Exact value from a versioned, verified closed registry | **The closed list is unknown from code.** Do not invent a list from product familiarity. Until verified, no source value is automatically trusted as built-in. |
| User-defined agent | Exact metadata label only if approved by A4 | Whether the value is a definition basename, frontmatter name, or another identifier is **unknown from code**. Do not read definitions or infer names from their locations. |
| Plugin-defined agent | Preserve only a verified, approved canonical identifier | Actual upstream namespace syntax, including whether it resembles `plugin:<name>`, is **unknown from code**. |
| Nested/forked subagent | Its own resolved label | Do not inherit the parent’s label, add hierarchy suffixes, or use the opaque run ID as the label. |
| Recognized non-built-in but not permitted | `custom` | Deliberate privacy collapse. |
| No usable label source | `unknown` | Different from `custom`: unavailable evidence rather than suppressed name. |
| Producer does not measure this field | `null` in ordinary read APIs; absent in export | Keeps legacy/Hermes absence distinct from an explicitly unresolved CC label. |

**Proposed safe-ID regex**, with an independent maximum of 64 characters:

```regex
^(?=.{1,64}$)(?:[A-Za-z][A-Za-z0-9_-]*|plugin:[A-Za-z][A-Za-z0-9_-]*)$
```

The `plugin:` alternative is a proposed accepted representation, not a claim about upstream syntax. Until source syntax is verified, do not manufacture it.

Reserve `main`, `custom`, and `unknown` for producer-generated meanings. A custom source name equal to a reserved value must not acquire that meaning.

Do **not** reuse project slug normalization: it replaces invalid characters and can turn a private string into a seemingly harmless identifier.  
Evidence: `B/producers/claude_code/cc_attribution.py:28` — `_INVALID_LABEL_RE.sub("-", ...)`. Project attribution fixtures test that normalization, not an agent-name privacy policy: `B/tests/test_cc_attribution.py:96–108`.

### A4. Exact custom-name privacy rule

**Proposed producer configuration:**

```text
agent_name_mode = "deny"          # "deny" or "allowlist"
agent_name_allowlist = []        # exact strings
```

**Proposed backend equivalents:**

```text
TOKEN_INSPECTOR_AGENT_NAME_MODE
TOKEN_INSPECTOR_AGENT_NAME_ALLOWLIST
```

Backend defaults are also `deny` and an empty list.

**Exact rule:**

1. Producer-generated structural labels are accepted only for their defined meaning.
2. An exact member of the verified built-in registry may pass unchanged.
3. Every other source label is custom.
4. A custom label passes unchanged only when:
   - mode is `allowlist`;
   - it matches the regex and length bound;
   - it is an exact allowlist member;
   - it is not a reserved value;
   - it is not a detected local machine identifier.
5. Otherwise emit literal `custom`.
6. Do not trim, slugify, extract a basename, decode, or salvage substrings.
7. Apply the rule at producer resolution, state load, ingest, and read/export serialization.
8. Never echo a rejected value in errors, logs, counters, or state.

**Default recommendation: literal `custom`, not an unsalted hash.** It avoids adding a correlation identifier and a new hashing-secret lifecycle. A pseudonym option can be a later, separately approved policy.

A shape test alone cannot detect every hostname: a single-label hostname may look exactly like a safe custom name. Therefore the allowlist is **mandatory** for custom pass-through, not optional in this recommendation. Even an allowlist requires the operator not to approve sensitive identifiers.

Existing code already distinguishes shape validation from local-host rejection for project labels; that is useful precedent, not proof that arbitrary agent names are safe.  
Evidence: `B/producers/claude_code/cc_attribution.py:122–124` — `name not in hosts`; `B/routes/events.py:235–240` — validation errors omit input.

**Required privacy canaries—without printing their values:**

- Absolute-location-shaped name.
- URL-shaped name.
- Email-shaped name.
- Dotted hostname.
- Single-label hostname not allowlisted.
- Detected local hostname even when mistakenly allowlisted.
- Safe-shaped unapproved custom name.
- Reserved-label collision.
- Oversized/non-string value.
- Previously permitted name replayed after policy is disabled.

For each, assert the original value is absent from POST bytes, state bytes, logs, validation errors, event reads, task reads, analytics, and export. Existing tests already use this byte-level style.  
Evidence: `B/tests/test_cc_events.py:114–115` — `assert not any(c in body for c in canaries())`; `B/tests/test_cc_state_client.py:75–79` — state-byte privacy assertions.

### A5. Hermes equivalent

There is a **partial equivalent**, not a proven Claude-style named-agent taxonomy:

- `_subagent_start` receives `child_role`.
- It stores that value in child session state.
- `_session_context` exposes it as `role`.
- The event builder emits `role`, not `agent`.

Evidence:

- `P/__init__.py:399–408` — `"role": kwargs.get("child_role")`.
- `P/__init__.py:60–64` — child role, otherwise `"primary"`.
- `P/mapping.py:159–165` — complexity, role, hierarchy, and parent fields.

A future Hermes implementation could sanitize `child_role` into the same `agent` field, but the complete runtime meaning/value set is **unknown from these worktrees**. A delegate tool’s name is not an equivalent source: tool names describe tool events.  
Evidence: `P/mapping.py:287–298` — `event_type="tool_call"` and `"tool_name"`.

**Goal-1 recommendation:** do not expand Hermes telemetry behavior. Document: **“Agent label is not available in the current Hermes event contract; `child_role` is a possible future source.”** Preserve absent/null agent values rather than pretending Hermes emitted `main`. Its existing complexity remains unchanged.

---

## B. Per-call complexity for Claude Code

### B1. Numeric inputs actually available

“Not reading text” needs a precise distinction: the producer already JSON-decodes records containing content. Numeric usage fields can be consumed without inspecting content values; computing character length requires examining the decoded string’s length, though not retaining or interpreting its text.

| Candidate input | Available now? | Required change / limitation |
|---|---|---|
| Current task/user-message character length | **No numeric length field is extracted.** | Requires new length-only inspection of string/text blocks and checkpointed task context. `Record` has no such member. `B/producers/claude_code/cc_transcript.py:33–45`. |
| Message count in the turn | **Not accumulated.** | Requires a defined counting rule and persistent counters. JSONL line count is not message count: assistant lines are streaming fragments. `:3–7`; `:190–202`; `:213–225`. |
| Exact request/conversation message count | **Unknown from code.** | A transcript is not shown to be an exact serialization of each API request. Do not rename a transcript-turn count as API message count. |
| Input usage excluding cache | **Yes.** | `usage["input_tokens"]`; `B/producers/claude_code/cc_transcript.py:117`. |
| Cache-read input usage | **Yes.** | `cache_read_input_tokens`; `:118`. |
| Cache-creation input usage | **Yes.** | `cache_creation_input_tokens`; `:119`. |
| Effective input usage including cache | **Derivable now.** | Sum the three input categories once. Mapping explicitly says Anthropic input excludes cache. `B/producers/claude_code/cc_events.py:70–74`. |
| Tool-result count | **Recognizable, not accumulated.** | `_user_kind` recognizes tool-result blocks, but user processing then moves on. `B/producers/claude_code/cc_transcript.py:75–82,190–202`. |
| Tool-result content characters/bytes | **Not available as a retained metric.** | Needs block-specific length measurement. Whole JSONL `size` includes metadata/encoding and is not tool-output size. `:85–105,169–174`. |
| Tool-use count | **Yes, but not tool-result size/count.** | Counts distinct response tool-use IDs plus unnamed blocks. `:124,228–235`. |

Existing missing-cache behavior is pinned as zero. New code must preserve that mapping rather than treating prompt usage as already cache-inclusive.  
Evidence: `B/tests/test_cc_events.py:39–45` — missing cache fields produce `(0, 0)`.

### B2. What is the “user message”?

**Main turn:** the latest actual prompt associated with the active turn—not the most recent user-typed record indiscriminately. Tool results and meta records are also represented as user records.

**Subagent:** the run’s initial actual user/task prompt, when present. Whether its text is byte-for-byte identical to the parent’s task argument is **unknown from code**. A fork may have no standalone task prompt.

Evidence:

- `B/producers/claude_code/cc_transcript.py:75–82` distinguishes `tool_result`, `meta`, and `prompt`.
- `:194–201` updates turn keys separately from that classification.
- `B/tests/test_cc_events.py:139–150` explicitly covers a fork “without a prompt line.”

If a later method needs length, compute Unicode character length locally from supported text components and retain only the integer. Do not count serialized JSON bytes, tool-result content, or parent tool arguments as the user prompt. Missing prompt representation must be **unknown**, not zero.

**The recommended goal-1 method below does not require this new parsing**, which makes it applicable to forked and backlog calls without reconstructing prompt context.

### B3. Chosen method: `cc-input-size-v1`

**Choice:** use the same ordinal tier set, 1–5, under a **new method name**. Do not claim cross-producer calibration.

Hermes `request-shape-v1` currently adds points at:

- User-message characters: 256, 1,024, 4,096.
- Message count: 8, 24.
- Approximate input tokens: 16,000, 64,000.
- Point-to-tier mapping: 0 → 1; 1–2 → 2; 3–4 → 3; 5–6 → 4; 7 → 5.

Evidence: `P/complexity.py:22–39`. Its builder obtains these from request-time metadata: `P/mapping.py:83–90`. Tests pin representative results: `P/tests/test_mapping.py:6–10`.

Claude Code has **observed usage**, not those same request-time inputs. Copying the Hermes method name would therefore misstate provenance.

#### Proposed exact algorithm

For a finalized real-model call:

```text
I = input_tokens + cache_read_input_tokens + cache_creation_input_tokens

tier 1:       0 <= I <   4,000
tier 2:   4,000 <= I <  16,000
tier 3:  16,000 <= I <  64,000
tier 4:  64,000 <= I < 128,000
tier 5: 128,000 <= I

complexity_method = "cc-input-size-v1"
```

- Output usage, tool arguments, text, price, and elapsed time do not enter the score.
- Use the final grouped usage, not each streaming line.
- Keep synthetic-record exclusion unchanged.
- Missing cache fields follow existing zero semantics.
- Add a parser-side validity flag so malformed required input usage does not become a falsely measured tier 1; retain the event’s existing handling, but leave complexity unscored.

Evidence for integration: `B/producers/claude_code/cc_transcript.py:108–121` builds grouped usage; `:217–222` excludes synthetic/no-usage records; `:236–246` controls finality/checkpoints.

#### Threshold justification and limits

**None of the numerical thresholds can be calibrated from the supplied aggregate counts.**

- 16,000 and 64,000 reuse numeric landmarks already present in Hermes, but that does **not** establish equivalent tiers.
- 4,000 and 128,000 are proposed outer boundaries, not corpus-derived findings.
- The supplied call/streaming/tool-result counts justify grouping and avoiding raw line counts; they do not provide the distribution of input size.
- The supplied synthetic count supports retaining exclusion, not a complexity threshold.

Before freezing the method, obtain **counts only**: a histogram of effective input usage over deduplicated finalized real-model calls, missing/invalid input-field counts, and counts near each proposed boundary. This can be computed from numeric usage records or authorized metadata-only event exports, without inspecting message content or reporting identifiers.

The table above is an exact **provisional specification**, subject to the user’s threshold decision. If later changed after deployment, introduce a new method version rather than silently changing `cc-input-size-v1`.

### B4. Task `start_complexity`

Preserve the existing rule:

> Earliest eligible scored LLM event for that task, ordered by call time, then stored event ID.

It is not “first received event,” not maximum complexity, and not a session-wide score.

Evidence:

- `B/task_store.py:64–75` selects an approved LLM candidate.
- `:269–274` — `:at < start_complexity_event_at`, then event-ID tie-break.
- `B/tests/test_task_ingest.py:68–77` proves late arrival of an earlier event updates the start score.

Consequences:

- Main: earliest eligible call of that turn/task.
- Subagent: earliest eligible call of the run, because one run maps to one child task.
- If the first call is unscored, the first **eligible scored** call wins; document that qualification.
- Historical, already-deduplicated copied calls do not migrate into the resumed/forked task.

Evidence: `B/producers/claude_code/cc_events.py:91` — “one subagent run = one child task”; `B/docs/adr/004-cross-source-dedup-authority.md:34` — “first arrival owns the project and task.”

### B5. Method validation and enumeration changes

| Location | Current behavior | Required change |
|---|---|---|
| `EventIn.complexity_method` | Optional bounded string, **not an enum**. `B/routes/events.py:132–133`. | No enum expansion needed. Add acceptance/pairing tests for the new method; preserve old producers and existing generic method acceptance. |
| Storage mapping | Directly assigns both fields. `B/routes/events.py:360–361`. | No new complexity column or tag required. |
| Model | Optional string, length 32. `B/models.py:93–94`. | New name fits the existing contract. |
| Task allowlist | Only `request-shape-v1`. `B/task_store.py:23`. | Add `cc-input-size-v1`. |
| Task candidate return | Hard-coded `return "request-shape-v1"`. `B/task_store.py:73–74`. | **Return the actual approved resolved method.** Expanding the set alone would mislabel CC tasks. |
| Complexity matrix | `method: str = "request-shape-v1"`; parameterized equality; methods discovered from data. `B/routes/analytics.py:487,497–502`. | Already accepts the new value. Keep default unchanged; add tests for CC selection and method discovery. |
| Other complexity endpoints | No method parameter in current signatures. `B/routes/analytics.py:270–272,305–312,355–359`. | Add optional `method` filtering without changing existing response shapes/default behavior. Document that unfiltered legacy endpoints may pool methods and are not the Agents contract. |
| Export enumeration | `COMPLEXITY_METHODS = ("request-shape-v1",)`. `B/routes/export.py:71`. | Add the new method. |
| Export emission | Emits method only if enumerated. `B/routes/export.py:257–258`. | Cover new name and unknown-method omission with tests. |
| Export contract | Documents only `request-shape-v1`. `B/docs/export-contract-v1.md:163–164`. | Document both names and their non-equivalence. |

---

## C. Storage, read side, and contract

### C1. Event storage: choose a column

**Recommendation:** nullable `token_events.agent`, not a reserved tag.

Reasons:

- Agent is a first-class grouping dimension.
- Column validation and indexing are explicit.
- No additional event-tag keys are needed for agent or complexity.
- It avoids repeating the ADR-003 tag-budget exercise and adding more JSON extraction to aggregation.

Current model has `role` and `tags_json`, but no agent field.  
Evidence: `B/models.py:59–63`. Current complexity columns already exist at `:93–94`.

#### Proposed schema-v13 migration

In `models.py`, add nullable `agent: Optional[str]` with maximum length 64, preferably at the end of the event model so fresh/upgraded column ordering remains predictable.

In `migrations.py`:

- Set `LATEST_SCHEMA_VERSION = 13`.
- Register `(13, _m13)`.
- Use the existing additive-column helper.
- Create the index specified in C3.
- Conservatively invalidate existing export cursors once for the migration.

Conceptual migration SQL:

```sql
ALTER TABLE token_events ADD COLUMN agent VARCHAR(64);

CREATE INDEX ix_token_events_call_time_agent
ON token_events (
    COALESCE(occurred_at, recorded_at),
    agent,
    complexity_method,
    complexity,
    model
)
WHERE event_type = 'llm_request';

UPDATE export_state
SET revision = revision + 1
WHERE id = 1;
```

The actual migration must use idempotent helper/index creation because fresh databases are first created from model metadata.

Evidence: `B/database.py:85–90` — `create_all` before migrations; `B/migrations.py:376–411` — registry and applied-version handling; `B/database.py:43–58` — explicit transactional DDL.

#### Proposed rollback

Create `scripts/rollback_v13.sql`:

```sql
DROP INDEX IF EXISTS ix_token_events_call_time_agent;
ALTER TABLE token_events DROP COLUMN agent;

UPDATE export_state
SET revision = revision + 1
WHERE id = 1;

DELETE FROM schema_migrations WHERE version = 13;
```

Add:

```text
STEPS[13] = ("rollback_v13.sql", 12)
APP_FOR_VERSION[12] = an application artifact whose latest schema is 12
```

Run through the existing exact-source-version transactional runner, not as standalone SQL.  
Evidence: `B/scripts/rollback_schema.py:22–28,62–75`.

This rollback removes the new agent column/index, not token usage or event identity. Existing complexity columns can still hold the new method string; older export code will omit that unfamiliar method. Document this code-rollback compatibility limitation explicitly.  
Evidence: `B/models.py:93–94`; `B/routes/export.py:257–258`.

### C2. Task agent: derive from events

**Do not add `tasks.agent` in goal 1.**

For the task’s counted LLM events:

1. Collect distinct non-null `agent` values.
2. No values: agent unavailable.
3. Exactly one value: return it.
4. Multiple values: return `agent: null` and `agent_conflict: true`.
5. Include `agent_unavailable_calls`, counting null agent values, so partial coverage is not concealed.

`unknown` and `custom` remain explicit values; do not silently replace old unknown calls with a later name.

Use:

- All current counted events for the ordinary Tasks API.
- Only `ingest_seq <= as_of` counted events for export.

Do not derive task identity from a parent task or apply its label to children.

Evidence: task uniqueness is `(project_name, session_id, turn_id)` at `B/models.py:110`; export task join uses that tuple at `B/routes/export.py:113–114`; snapshot-only membership is required at `B/docs/adr/005-export-contract.md:38–39`.

Add these fields to Tasks list/detail builders. Existing start-complexity fields are already exposed there.  
Evidence: `B/routes/tasks.py:140–198`, particularly `:188–189`.

### C3. Index and served query

**Proposed index:** `ix_token_events_call_time_agent`, defined in C1.

It serves the new endpoint’s bounded event-time scan:

```sql
WHERE event_type = 'llm_request'
  AND COALESCE(occurred_at, recorded_at) >= :from
  AND COALESCE(occurred_at, recorded_at) < :to
```

Optional filters then restrict agent, method, model, project, and runtime; grouping is by runtime, agent, method, tier, model, and UTC day.

The leading time expression supports the common all-agent window. It is not claimed to eliminate grouping work or optimally serve every selective-agent query. Verify with representative `EXPLAIN QUERY PLAN` tests before adding another index.

Existing indexes primarily cover project/recorded time, task correlation, and dedup/export sequence.  
Evidence: `B/models.py:15–33`.

### C4. Read API for the future Agents tab

**Build in goal 1:**

```text
GET /api/analytics/agents
```

#### Query parameters

| Parameter | Proposed contract |
|---|---|
| `from` | Required timezone-qualified ISO-8601, normalized to UTC; inclusive |
| `to` | Required; exclusive; must be later than `from`; maximum span 92 days |
| `project` | Optional exact normalized project name |
| `runtime` | Optional repeatable member of existing `RUNTIMES` |
| `agent` | Optional repeatable sanitized label |
| `agent_missing` | Optional `true` selects null/unavailable agent; mutually exclusive with `agent` |
| `method` | Optional repeatable registered method name |
| `complexity` | Optional repeatable integer 1–5 |
| `include_unscored` | Boolean, default `true`; null-complexity rows are otherwise retained |
| `model` | Optional repeatable exact model filter; bounded count and string length |

Additional bounds: at most 100 values per repeated filter; maximum 20,000 returned cells. If the cell limit is exceeded, return a static `413 result_too_large`; never silently truncate. No pagination in this first aggregate contract.

Validate filters with static errors that do not echo raw values. No new authentication dependency; retain the service’s existing read-side boundary.

The range pattern follows export; static errors follow the existing jobs/export design.  
Evidence: `B/docs/export-contract-v1.md:23–37`; `B/routes/jobs.py:134–148`.

#### Eligibility and time semantics

- Count stored LLM events, not transcript lines or tasks joined multiplicatively.
- Exclude the dedicated JEV evaluator project/role pair.
- Exclude `attribution_invalid`.
- Keep unpriced calls.
- Do not exclude null agent or null complexity by default.
- Time basis: `COALESCE(occurred_at, recorded_at)`, UTC day.
- Keep **method** and **runtime** in every cell key, even when not filtered.
- Produce all fields in one consistent read snapshot.

Reuse the jobs/export eligibility semantics rather than `_llm_filter` alone. The latter currently only checks recorded time and event type.  
Evidence: `B/routes/jobs.py:34–38`; `B/routes/analytics.py:43–44`.

#### Response shape

The following is an **illustrative shape**, not observed data:

```json
{
  "contract_version": 1,
  "period": {
    "from": "<UTC timestamp>",
    "to": "<UTC timestamp>"
  },
  "time_basis": "occurred_at_or_recorded_at",
  "tier_source": "llm_call",
  "task_count_semantics": "distinct_contributing_tasks",
  "methods": ["request-shape-v1", "cc-input-size-v1"],
  "coverage": {
    "agent_unavailable_calls": 0,
    "agent_unknown_calls": 0,
    "agent_custom_calls": 0,
    "complexity_unscored_calls": 0
  },
  "totals": {
    "calls": 1,
    "tasks": 1,
    "calls_without_task": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "priced_calls": 0,
    "unpriced_calls": 1,
    "estimated_calls": 0,
    "priced_cost_usd": null,
    "estimated_cost_usd": null,
    "cost_complete": false,
    "latency_calls": 0,
    "avg_process_time_ms": null,
    "ttft_calls": 0,
    "avg_ttft_ms": null
  },
  "cells": [
    {
      "runtime": "<existing runtime enum>",
      "agent": "main",
      "complexity_method": "cc-input-size-v1",
      "complexity": 1,
      "model": "unknown",
      "day": "<UTC calendar date>",
      "calls": 1,
      "tasks": 1,
      "calls_without_task": 0,
      "prompt_tokens": 0,
      "completion_tokens": 0,
      "cache_read_tokens": 0,
      "cache_creation_tokens": 0,
      "priced_calls": 0,
      "unpriced_calls": 1,
      "estimated_calls": 0,
      "priced_cost_usd": null,
      "estimated_cost_usd": null,
      "cost_complete": false,
      "latency_calls": 0,
      "avg_process_time_ms": null,
      "ttft_calls": 0,
      "avg_ttft_ms": null
    }
  ],
  "complete": true
}
```

#### Metric definitions

- `calls`: `COUNT(*)` of eligible events.
- `tasks`: `COUNT(DISTINCT task_ref)` of actually joined tasks.
- `calls_without_task`: eligible events without a joined task.
- Token fields: independent category sums; prompt excludes cache.
- `priced_cost_usd`: sum of priced costs; **null if no priced calls**.
- `estimated_cost_usd`: separate sum for partial/estimated/legacy observations; null if no such observations.
- `cost_complete`: true only when all contributing calls are priced.
- Latency/TTFT: average over reported values only, with denominators; unknown is null.
- Top-level task count is recomputed distinctly, not the sum of cell task counts.
- A task spanning methods, tiers, models, or days may appear in multiple cells.

The current matrix already documents non-additive task membership and nullable cost/latency behavior.  
Evidence: `B/routes/analytics.py:429–455,490–494`.

Do not infer Claude Code latency from transcript timestamp gaps: the current producer field allowlist does not report process time or TTFT.  
Evidence: `B/producers/claude_code/cc_events.py:18–23`.

#### Composition with existing endpoints

- `/complexity-matrix`: continue using a selected method; add CC method tests.
- Existing role-based endpoints: role remains a separate structural dimension, not an agent substitute.
- `/api/jobs`: remains whole-job aggregation.
- `/api/tasks`: adds task agent derivation and already exposes start complexity.
- `/api/events`: exposes the validated event column.
- Export remains the stable external-consumer contract.

Do not promise direct reconciliation with existing analytics totals: many use **recorded time** and lack the jobs/export exclusion predicate. The new endpoint must state its different, explicit semantics.  
Evidence: `B/routes/analytics.py:43–44`; `B/routes/jobs.py:34–43`; `B/routes/events.py:532–534`.

### C5. Export v1 versus v2

**Recommendation: additive v1**, subject to checking consumers’ handling of unfamiliar method values.

The contract explicitly permits new fields and requires consumers to ignore them.  
Evidence: `B/docs/export-contract-v1.md:7–10` — “Adding a field is not a breaking change.”

#### Exact additions

| Dataset | Field/change | Values |
|---|---|---|
| Events | Add `agent` | Approved safe label, `main`, `custom`, or `unknown`; absent when not measured |
| Events | Extend `complexity_method` | Existing `request-shape-v1` plus `cc-input-size-v1`; retain omission of unsupported methods |
| Tasks | Add `agent` | Single distinct measured value; null when multiple values; absent when none measured |
| Tasks | Add `agent_conflict` | Boolean |
| Tasks | Add `agent_unavailable_calls` | Non-negative integer |
| Tasks | Add `start_complexity` | Integer 1–5 or null |
| Tasks | Add `start_complexity_method` | Approved method name or null |
| Jobs | No scalar agent addition | A job can contain several agents; event/task exports supply the detail |

Derive exported task agent and start-complexity fields from **snapshot events**, not unrestricted live event queries. For exported start complexity, apply the same earliest-eligible rule within the snapshot.

Update `EXPORT_FIELDS`, `_EV`, task aggregation/selection, and item builders. Current field allowlists and builders do not expose these task fields.  
Evidence: `B/routes/export.py:41–59,125–142,164–172,290–317`.

The in-repository CSV consumer obtains its columns from `envelope.fields`; it does not pin agent/complexity field names.  
Evidence: `B/static/app.js:1062–1065` — `const fields = envelope.fields`.

**Compatibility caveat:** the current method value set is documented as closed. Ignoring unknown fields is not the same as accepting unknown enum values. External consumer behavior is **unknown from code**. If a required consumer rejects a new method value and cannot be updated compatibly, use v2 rather than silently violating its expectations.

#### Revision rule

- New inserts advance `last_seq`; they do not expire existing snapshots.
- A maintenance write changing stored exported data must increment `export_state.revision` **in the same transaction**.
- Agent backfill, complexity backfill, correction, or deletion must follow this rule.
- No-op maintenance should not invalidate cursors.
- The proposed v13 migration/rollback conservatively invalidates old cursor chains.
- Derived read-side fields must never trigger writes.

Evidence: `B/routes/events.py:381–397`; `B/docs/adr/005-export-contract.md:43–49,69–70`; `B/routes/export.py:422–424` — revision mismatch returns `snapshot_expired`.

### C6. Backfill and coverage

**Default: from deploy on; no automatic backfill.**

More precisely: new fields are attached to calls **first inserted by the upgraded producer/backend**, including previously uninserted backlog. This is not necessarily “calls whose occurrence timestamp is after deploy.”

Duplicate replay cannot enrich existing rows: duplicate events do not rerun task derivation.  
Evidence: `B/routes/events.py:435–438,461–466`.

Potential separately approved backend-only derivations:

1. **Main label:** set `agent='main'` only for unambiguously tagged CC primary events with null agent. This uses existing producer/role metadata, not transcripts.
2. **Named subagent label:** **no backfill**. Existing rows lack the necessary name.
3. **Input-size complexity:** the proposed arithmetic can be reconstructed from stored normalized input categories. However, old rows no longer reveal whether zeros came from valid measurements or normalization of missing/invalid usage. Treat this as **metadata-derived historical scoring**, not recovery of the original observation. Do not silently enable it.
4. If historical scoring is approved, recompute affected task starts with the shared candidate rule, preserve all event IDs/usage, and bump revision atomically.

Evidence: normalized categories are stored at `B/routes/events.py:335–338`; normalization discards invalid numeric distinctions at `B/producers/claude_code/cc_transcript.py:71–72`; current idempotent insert is non-updating at `B/routes/events.py:385–407`.

---

## D. Step plan

### D1. Ordered integration plan

No step below was executed as an implementation.

| Step | Exact targets | Required tests / acceptance |
|---|---|---|
| **1. Resolve contract gates** | Proposed `docs/adr/006-agent-identity-and-cc-complexity.md` | Record agent-label versus run-ID semantics; verified sidecar key/type schema; built-in registry evidence; privacy defaults; method/range decision; historical coverage. Do not claim sidecar completeness without evidence. |
| **2. Add label policy and fixtures** | New `producers/claude_code/cc_agent.py`; new backend `agent_identity.py`; `producers/claude_code/cc_config.py`; new `tests/fixtures/agent_identity_vectors.json` | Main, approved built-in, denied custom, allowlisted custom, reserved collisions, all privacy canaries, invalid config, policy revocation. Producer remains stdlib-only. |
| **3. Bind labels per run and persist safely** | `producers/claude_code/cc_hook.py`, `cc_state.py`, `cc_events.py`; extend `tests/test_cc_hook.py`, `tests/test_cc_state_client.py`, `tests/test_cc_events.py`; new `tests/test_cc_agent.py` | Main/subagent/nested/fork label isolation; matching/mismatching hook IDs; own-subagent and sibling backlog; no SubagentStop for drained run; restart; old state; replacement/truncation; missing/corrupt/oversized sidecar; conflicting sources. |
| **4. Add numeric complexity** | New `producers/claude_code/cc_complexity.py`; `cc_transcript.py`, `cc_events.py`; new `tests/test_cc_complexity.py`; extend `tests/test_cc_transcript.py` | Every boundary immediately below/at threshold; all five tiers; cache-inclusive input; output independence; malformed usage; missing cache; synthetic exclusion; streaming/fork/resume/backlog fixtures. |
| **5. Add schema v13 and ingest** | `models.py`, `migrations.py`, `routes/events.py`, `scripts/rollback_schema.py`; new `scripts/rollback_v13.sql`, `tests/test_migration_v13.py`, `tests/test_agent_ingest.py` | Fresh/migrated parity, index existence/query plan, old client compatibility, single/batch normalization, no raw value echoes, transactional rollback, exact version guards, cursor invalidation, unchanged dedup. |
| **6. Enable task starts and task read fields** | `task_store.py`, `routes/tasks.py`; `tests/test_task_ingest.py`, `tests/test_migration_v10.py`; new task-agent tests | Actual CC method preserved; earlier-event arrival; ties; unapproved methods excluded; tool events cannot set starts; mixed/missing agent derivation; child does not inherit parent label. |
| **7. Implement the Agents endpoint** | `routes/analytics.py`; preferably shared counted-event helper used by the new code; new `tests/test_agents_analytics.py`; extend `tests/test_complexity_matrix.py` | Per-agent/method/tier/model/day cells; distinct tasks; unknown buckets; category sums; all-unpriced null cost; mixed pricing; missing latency; evaluator/invalid-attribution exclusion; static validation errors; result bounds; event-time boundaries. |
| **8. Extend export v1** | `routes/export.py`, `docs/export-contract-v1.md`; `tests/test_export_contract.py`, `tests/test_export_pagination.py`, `tests/test_docs_export.py` | New field allowlists; method enumeration; privacy canaries; absent/null semantics; task snapshot isolation; concurrent new ingest; mutation expiration; no-op maintenance; generic CSV field compatibility. |
| **9. Update documentation and compatibility notes** | Files listed in D3; plugin `README.md` only for symmetry/availability documentation | Documentation tests cover every exported field; no claim that named agents or CC latency are universally available. |
| **10. Authorized integration verification and deployment** | Backend first, then producer | Run affected and full suites only in an authorized writable test environment. Verify metadata-only payloads, bounds, duplicates, and counted totals. No dashboard work in goal 1. |

Existing regression anchors:

- Streaming/finality: `B/tests/test_cc_transcript.py:22–105`.
- Fork identity: `B/tests/test_cc_events.py:139–166`.
- Backlog drain: `B/tests/test_cc_hook.py:464–494`.
- State privacy: `B/tests/test_cc_state_client.py:60–90`.
- Stdlib boundary: `B/tests/test_cc_stdlib.py:12–34`.
- Migration parity: `B/tests/test_migration_v12.py:74–88`.
- Export field allowlists: `B/tests/test_export_contract.py:117–146`.
- Snapshot mutation: `B/tests/test_export_pagination.py:100`.
- Existing analytics response compatibility: `B/tests/test_complexity_matrix.py:114–117`.

**No new tag-size expansion test is required for the chosen column design**, because no new tags are added. Keep the existing cap tests unchanged. If the user chooses tags instead, measurement and a worst-case fixture test become prerequisites, per `B/docs/adr/003-job-correlation-contract.md:41`.

### D2. Mutation targets: assertions that must go red

These are proposed assertions, not claims that tests have run.

| Control deliberately broken | Exact assertion target |
|---|---|
| Main label generation removed | `assert main_event["agent"] == "main"` |
| Current hook label applied to another target | `assert drained_event["agent"] == expected_drained_run_agent` |
| Hook/run binding check removed | `assert mismatched_hook_event["agent"] == "unknown"` |
| Nested child inherits parent label | `assert nested_event["agent"] == expected_nested_agent` |
| Fork is treated as a new prompt-based identity | `assert fork_event["session_id"] == fork_event["turn_id"] == expected_run_id` |
| Stored agent keys dropped by `_valid_ctx` | `assert loaded["ctx"]["agent"] == saved["ctx"]["agent"]` |
| Reset keeps stale binding | `assert replacement_event["agent"] != old_run_agent` |
| Policy revocation ignored during backlog | `assert replayed_event["agent"] == "custom"` |
| Default custom permission accidentally enabled | `assert sanitize(custom_name, default_policy) == "custom"` |
| Safe shape bypasses mandatory allowlist | `assert sanitize(safe_unlisted_name, allowlist_policy) == "custom"` |
| Path/URL/email/hostname value leaks | `assert all(canary not in encoded_surface for canary in CANARIES for encoded_surface in SURFACES)` |
| Reserved name can spoof main | `assert sanitize_custom("main", policy) == "custom"` |
| Sidecar failure blocks telemetry | `assert emitted_call_ids == expected_call_ids` |
| Bounded/fail-open behavior broken | `assert exit_code == 0 and stdout == stderr == b"" and elapsed < test_bound` |
| Agent/complexity additions bypass output allowlist | `assert set(event) <= ALLOWED_FIELDS` |
| Cache input omitted from score | `assert score_input_size(0, 16000, 0) == 3` |
| Output usage influences score | `assert score(same_input, output_a) == score(same_input, output_b)` |
| Threshold boundary is off by one | `assert tiers_for_boundary_vectors == [1, 1, 2, 2, 3, 3, 4, 4, 5]` |
| Invalid usage silently becomes tier 1 | `assert malformed_event.get("complexity") is None` |
| Streaming lines become multiple calls | `assert len(events) == finalized_real_group_count` |
| Synthetic records become calls | `assert synthetic_client_ids.isdisjoint(emitted_client_ids)` |
| Event ID changes with label or complexity | `assert old_client_event_id == enriched_client_event_id` |
| Task method remains hard-coded | `assert task["start_complexity_method"] == "cc-input-size-v1"` |
| Arrival order determines task start | `assert task["start_complexity_event_id"] == earliest_eligible_event_id` |
| Tool event sets task start | `assert start_after_tool == start_before_tool` |
| Task agent copied from parent | `assert child_task_agent == derive(child_counted_events)` |
| Method dimensions pooled | `assert set(cell_methods) == expected_separate_methods` |
| Task counts summed across cells | `assert totals["tasks"] == unique_contributing_task_count` |
| Unknown agent/complexity rows dropped | `assert totals["calls"] == known_calls + unknown_calls` |
| Unpriced usage becomes free | `assert all_unpriced["priced_cost_usd"] is None and all_unpriced["unpriced_calls"] == all_unpriced["calls"]` |
| Estimated cost counted as priced | `assert priced_cost == sum_priced_only` |
| Missing latency treated as zero | `assert no_latency["avg_process_time_ms"] is None and no_latency["latency_calls"] == 0` |
| Evaluator/invalid-attribution exclusion removed | `assert result_with_excluded_rows == result_without_excluded_rows` |
| New method omitted from export | `assert exported_event["complexity_method"] == "cc-input-size-v1"` |
| Snapshot task label reads later events | `assert continued_snapshot_task_agent == first_page_snapshot_task_agent` |
| Revision bump omitted on maintenance | `assert old_cursor_response.status_code == 409` |
| Duplicate replay rewrites enrichment | `assert stored_row_after_duplicate == stored_row_before_duplicate` |
| Schema rollback guard bypassed | `assert wrong_source_rollback_fails and database_state == before` |
| Upgrade retry mutates again | `assert second_upgrade_state == first_upgrade_state` |

The unchanged identity, state, task-ordering, and snapshot invariants are grounded respectively in `B/tests/test_cc_events.py:48–66`, `B/tests/test_cc_state_client.py:131–171`, `B/tests/test_task_ingest.py:68–85`, and `B/docs/adr/005-export-contract.md:25–49`.

### D3. Documentation changes and ADR-006

| Document | Required update | Current anchor |
|---|---|---|
| Backend `README.md` | Agent availability, new method, endpoint, schema v13, deploy-onward coverage | `B/README.md:364–379` currently describes Hermes complexity and legacy scoring |
| `AGENTS.md` | Privacy policy; column ownership; schema/rollback; counted API semantics; no inferred Hermes labels | `B/AGENTS.md:157–169` — current complexity distinction |
| `CLAUDE.md` | New method and column; maintain tag cap and revision requirements | `B/CLAUDE.md:40–49` |
| `ARCHITECTURE.md` | Run-local resolution, state binding, event-column/read derivation, read-side contract | `B/ARCHITECTURE.md:19–26,74–85` |
| `CHANGELOG.md` | Data-layer feature only; no Agents page claim; coverage limitations | `B/CHANGELOG.md:13–14,28–32` |
| Producer `README.md` | Revise “agent names never sent” into exact gated policy; list new fields/config; unknown sources and state behavior | `B/producers/claude_code/README.md:42–57` |
| Export contract | Add fields, extend method value set, document snapshot task derivation and compatibility | `B/docs/export-contract-v1.md:125–194` |
| Hermes plugin `README.md` | Current agent field unavailable; `child_role` is a future candidate; complexity unchanged | `P/README.md:5–16,110–124` |
| ADR-004 | Clarify that hook/sidecar metadata may enrich a transcript-derived event but never create a call | `B/docs/adr/004-cross-source-dedup-authority.md:26–34` |

**ADR-006 is warranted.** It should record:

- Label versus run identity.
- Source binding, precedence, conflicts, and unavailable-source fallback.
- Custom-name privacy defaults and approved value rules.
- Column rather than tag; task derivation rather than duplicate storage.
- New numeric method and lack of cross-producer calibration.
- Agents read contract and counted-event semantics.
- Export compatibility and historical-enrichment policy.

### D4. Risks and unknowns

1. **Sidecar label schema is unknown from code.** Existence is not proof of a name/type field or durability across all lifecycle cases.
2. **Built-in, custom, and plugin naming taxonomy is unknown from code.** Do not ship a purported complete built-in registry without evidence.
3. **Exact parent-tool pairing is absent.** The observed parent-turn relationship does not authorize guessing a spawning call.
4. **Nested discovery and immediate-parent topology are not established universally.** Current drain is bounded and non-recursive; current child parent fields derive from transcript session/prompt metadata.  
   Evidence: `B/producers/claude_code/cc_hook.py:165–204`; `B/producers/claude_code/cc_events.py:88–96`.
5. **First arrival owns copied calls.** A fork/resume does not receive another attribution of already-stored calls, even if its new label differs.  
   Evidence: `B/docs/adr/004-cross-source-dedup-authority.md:32–34`.
6. **Input size is not semantic difficulty.** The new method must not be compared directly with Hermes tiers or JEV scores.  
   Evidence for the existing distinction: `P/complexity.py:12–15`; `B/AGENTS.md:169`.
7. **Threshold calibration is absent.** Supplied totals do not contain numeric-feature distributions. Clarify corpus denominators before making coverage percentages; none are inferred here.
8. **Safe-shaped names can still be sensitive.** Default deny and explicit approval matter more than the regex.
9. **Late label discovery cannot repair existing events through replay.** Task conflict/coverage fields must reveal partial attribution rather than conceal it.  
   Evidence: `B/routes/events.py:385–407,435–438`.
10. **Export enum expansion may affect strict consumers.** External consumer behavior is unknown from code.
11. **Existing analytics have different time/exclusion semantics.** Reusing their totals uncritically would produce misleading comparisons.  
    Evidence: `B/routes/analytics.py:43–44`; `B/routes/jobs.py:34–43`.
12. **Metadata reads must remain bounded.** Sidecar resolution must not turn a short hook into recursive discovery or unbounded rescanning.  
    Evidence: `B/producers/claude_code/cc_hook.py:21–23` — hook/send bounds and drain-list cap.

### D5. OPEN DECISIONS — answer before implementation

| Decision | Concrete scenario | Options | Recommendation |
|---|---|---|---|
| **1. Required identity completeness** | A backlog run has no stored binding and never delivers its own SubagentStop. Sidecar label schema is not verified. | Ship `unknown` fallback; or block named-agent support until content-free source-schema/lifecycle evidence is supplied. | **Allow honest `unknown` coverage, but gate the sidecar adapter on verified schema.** Do not claim named labels for every run. |
| **2. Custom-name privacy** | A safe-looking custom name may identify an internal system. | Literal `custom`; stable pseudonym; exact approved names. | **Default deny → `custom`; opt-in exact allowlist only.** No automatic slugification or unsalted hashes. |
| **3. Complexity meaning and thresholds** | Forks may lack an explicit task prompt, and transcript counts are not request counts. | Numeric input-size method; or richer transcript-shape method requiring new parsing/checkpoint semantics. | **Choose `cc-input-size-v1`**, separate from Hermes, and approve/calibrate the provisional thresholds before freezing v1. |
| **4. Schema tradeoff** | Agent must be aggregated, but tags have a measured budget and tasks may have partial labels. | Reserved event tag; event column plus task column; event column plus task derivation. | **Schema-v13 event column, derived task fields.** |
| **5. Historical coverage** | Old events have stable IDs and no agent names. | Deploy-onward only; metadata-only main-label/input-size enrichment; transcript-based reconstruction. | **Deploy-onward by default.** Permit only a separately approved backend metadata operation; no transcript reconstruction. |
| **6. Export compatibility** | A consumer may treat the documented method set as exhaustive. | Additive v1 with tolerant consumers; v2 for strict consumers. | **Additive v1 after compatibility confirmation.** If a required strict consumer cannot adapt, choose v2. |
| **7. Aggregation semantics** | A task spans days/models/tiers; a late call arrives after its occurrence day. | Event-time cells with distinct contributing tasks; ingest-time cells; task-start cohorts. | **Event-time cells**, distinct tasks per cell and globally recomputed totals. Keep task-start cohorts confined to the existing task export semantics. |

---

## Files cited

### Backend

- `B/producers/claude_code/cc_hook.py:21–23,85–131,153–178,203–232`
- `B/producers/claude_code/cc_attribution.py:28,122–124`
- `B/producers/claude_code/cc_config.py:29–34,97–112`
- `B/producers/claude_code/cc_state.py:21–22,81–116,119–149`
- `B/producers/claude_code/cc_transcript.py:3–10,33–45,71–105,108–130,169–246`
- `B/producers/claude_code/cc_events.py:3–6,18–24,35–37,70–110`
- `B/producers/claude_code/README.md:42–57`
- `B/routes/events.py:32,81–145,219–240,335–361,381–407,435–438,461–466,532–534`
- `B/models.py:15–33,59–63,93–102,105–151`
- `B/migrations.py:15,348–411`
- `B/database.py:43–58,85–90`
- `B/task_store.py:23,55–75,269–274`
- `B/routes/tasks.py:140–198`
- `B/routes/analytics.py:43–49,270–289,305–324,355–369,429–455,487–502`
- `B/routes/jobs.py:34–43,134–148`
- `B/routes/export.py:41–71,113–172,257–258,290–317,422–424`
- `B/scripts/rollback_schema.py:22–28,62–75`
- `B/static/app.js:1062–1065`
- `B/docs/adr/003-job-correlation-contract.md:39–47`
- `B/docs/adr/004-cross-source-dedup-authority.md:26–38`
- `B/docs/adr/005-export-contract.md:25–56,69–70`
- `B/docs/export-contract-v1.md:7–10,23–37,125–194`
- `B/Plans/o9-job-correlation-cc-producer/status.md:73–84`
- `B/README.md:364–379`
- `B/AGENTS.md:157–169`
- `B/CLAUDE.md:40–49`
- `B/ARCHITECTURE.md:19–26,74–85`
- `B/CHANGELOG.md:13–14,28–32`
- `B/tests/test_cc_attribution.py:96–108`
- `B/tests/test_cc_events.py:39–82,114–115,139–166`
- `B/tests/test_cc_transcript.py:22–105`
- `B/tests/test_cc_hook.py:464–494`
- `B/tests/test_cc_state_client.py:60–90,131–171`
- `B/tests/test_cc_stdlib.py:12–34`
- `B/tests/test_task_ingest.py:68–85`
- `B/tests/test_migration_v12.py:74–88`
- `B/tests/test_complexity_matrix.py:114–117`
- `B/tests/test_export_contract.py:117–146`
- `B/tests/test_export_pagination.py:100`
- `B/tests/test_docs_export.py:10–27`

### Hermes plugin

- `P/complexity.py:6–39`
- `P/mapping.py:83–90,159–165,287–298`
- `P/__init__.py:60–69,399–408`
- `P/README.md:5–16,110–124`
- `P/tests/test_mapping.py:6–10`
