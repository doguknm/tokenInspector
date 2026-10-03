<!-- Read-only review via hermes chat (model/provider named by the user for this goal), session 20261003_080418_27a25e; worktrees backend 08f28ce, plugin 7a6d094 (detached, removed afterwards). -->

# O9 deploy runbook review r1

## Findings

F1 — HIGH — status.md:227–229, Rollback 1–3
Problem: Mandatory hook removal is limited to a “code-only” rollback below Phase 2, leaving the schema-to-v10 rollback path uncovered. Uninstalling also does not terminate hook processes already running.
Scenario: Hooks are healthy, but the operator rolls the schema back to v10 and starts the previous backend. Following the literal instructions permits leaving hooks installed even though that backend lacks Phase 2 handling and the schema rollback removes cross-project CC deduplication.
Fix: Replace Rollback 1 with: “Before any rollback to backend code below Phase 2, whether code-only, schema rollback, or backup restoration, uninstall hooks on both machines using the same absolute O9 installer path and settings file used at installation. Prevent new hook launches and allow existing hook invocations to finish before downgrading.”
Linux:
```sh
python3 "<O9-backend>/producers/claude_code/install.py" --settings "<settings-path>" --uninstall --apply
python3 "<O9-backend>/producers/claude_code/install.py" --settings "<settings-path>" --uninstall --dry-run
```
Windows: use `python` instead of `python3`. Require the second command to report zero remaining owned entries; allow the 10-second hook timeout backstop to elapse and verify no hook remains active. Then restore the plugin before the backend. Retain the existing stopped-service, O9-checkout-first schema rollback sequence. Evidence: install.py:32–51,183–225; README.md:52; scripts/rollback_v11.sql:4–8.

F2 — MEDIUM — status.md:216,219,227, steps 5/8 and Rollback 1
Problem: The Linux repair and installer commands use `python`, which is absent on this machine. Bare `install.py --uninstall --apply` is neither a complete path nor a reliable executable command.
Scenario: Step 5 fails with command-not-found before producing repair counts; the Linux half of step 8 fails similarly. Copying the rollback shorthand does not uninstall anything.
Fix: From the deployed backend checkout, use:
```sh
python3 scripts/repair_task_parents.py --db "$DB_PATH"
# Only after separate repair approval:
python3 scripts/repair_task_parents.py --db "$DB_PATH" --apply

python3 producers/claude_code/install.py --dry-run
# Only after that machine's installer approval:
python3 producers/claude_code/install.py --apply
```
Use the full uninstall commands in F1. Windows installer commands correctly use `python`. Explicitly bind the deployment shell’s `DB_PATH` to the service’s configured absolute database path; systemd’s environment is not automatically inherited by that shell. The repair and rollback scripts are stdlib-only, so they do not require activating the service venv. All three scripts’ `--help` commands were executed successfully with `python3 -B`; their documented flags exist.

F3 — MEDIUM — status.md:213, step 2
Problem: Step 2 specifies the correct backup API and checks but gives no executable procedure or source-snapshot definition for “counts equal the source.” Comparing a completed online backup with fresh counts from an actively ingesting source can reject a valid backup.
Scenario: Ingest commits between the backup and subsequent source counts. Integrity passes, but the counts differ; the runbook does not distinguish that race from a defective backup or define a stop rule.
Fix: Use one pinned read transaction for both source counts and the online backup, then verify the destination against those counts. For the approved deployment—not this review—the following is an executable replacement:
```sh
export DB_PATH='<absolute service DB_PATH>'
export BACKUP_PATH="$HOME/backups/token-inspector-pre-o9-$(date -u +%Y%m%dT%H%M%SZ).db"
python3 -B - <<'PY'
import os, sqlite3
from pathlib import Path
from contextlib import closing

srcpath = Path(os.environ["DB_PATH"]).resolve()
dstpath = Path(os.environ["BACKUP_PATH"]).resolve()
assert srcpath.is_file()
dstpath.parent.mkdir(parents=True, exist_ok=True)

def counts(c):
    return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("token_events", "tasks"))

with dstpath.open("xb"):
    pass
with closing(sqlite3.connect(srcpath.as_uri() + "?mode=ro", uri=True)) as src:
    src.execute("BEGIN")
    expected = counts(src)
    with closing(sqlite3.connect(dstpath)) as dst:
        src.backup(dst)
    src.rollback()
with closing(sqlite3.connect(dstpath.as_uri() + "?mode=ro", uri=True)) as bak:
    assert bak.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert counts(bak) == expected
print("backup_verified token_events=%d tasks=%d" % expected)
PY
```
Stop before step 3 on any nonzero exit; a leftover destination from a failed attempt is not a verified backup. Keep it local. This preserves the requested strict equality without assuming ingest is idle. The separate startup backup is not a substitute for both row-count checks: it checks `token_events` and schema version, not `tasks` (migrations.py:23–47).

F4 — MEDIUM — status.md:215,221–223, steps 4/9
Problem: The live checks are not reproducible as written: jobs return `runtimes`, not singular `runtime`; export requires actual timezone-qualified bounds, and both endpoints paginate. Step 4 also abbreviates the meta state names instead of stating the actual Boolean and reason fields.
Scenario: Checking `.runtime` on a valid jobs row yields null. A date-only export range returns 400; inspecting only the first export page can miss the newly generated CC events because events are ordered by ascending ingest sequence.
Fix: Use the following requests and exact assertions. `$TI_URL` is a privately configured base URL without a trailing slash; `$FROM` and `$TO` are fixed bounds surrounding the observed run, in `YYYY-MM-DDTHH:MM:SSZ` or timezone-offset ISO format, with `FROM < TO` and a span no greater than 92 days.

Step 4:
```sh
curl -fsS "$TI_URL/api/meta" |
  jq -e '.schema_version == 12
    and .task_prompt_capture == false
    and .task_prompt_capture_disabled_reason == "not_enabled"
    and .jev_enabled == false
    and .jev_disabled_reason == "not_enabled"
    and .task_prompt_allowlist == "empty"'

curl -fsS "$TI_URL/api/jobs?days=1" |
  jq -e 'has("items") and has("total")'

curl -fsS --get "$TI_URL/api/export/v1/events" \
  --data-urlencode "from=$FROM" --data-urlencode "to=$TO" |
  jq -e '.schema_version == 1 and .dataset == "events"'
```

AC1.2, selecting the observed job:
```sh
curl -fsS --get "$TI_URL/api/jobs" \
  --data-urlencode days=1 --data-urlencode runtime=hermes-agent \
  --data-urlencode work_type=review --data-urlencode page_size=200 \
  --data-urlencode "page=$PAGE" |
  jq --arg j "$JOB_REF" \
    '{total,page,page_size,items:[.items[] | select(.job_ref == $j) |
      {job_ref,runtimes,work_type,work_types,projects}]}'
```
Start at `PAGE=1`; follow pages through `total`. Require the selected row’s `runtimes` to contain `hermes-agent` and `work_type` to equal `review`.

For AC2.2 and AC2.3, fetch export pages:
```sh
curl -fsS --get "$TI_URL/api/export/v1/events" \
  --data-urlencode "from=$FROM" --data-urlencode "to=$TO" \
  --data-urlencode limit=1000 |
  jq '{schema_version,complete,next_cursor,
    items:[.items[] | select(.producer == "claude-code-hook") |
      {runtime,producer,work_type,job_ref,project_name,
       cache_read_tokens,cache_creation_tokens}]}'
```
While `complete` is false, repeat with the same bounds and `--data-urlencode "cursor=$CURSOR"`, taking `CURSOR` from the preceding `next_cursor`. Stop/restart the observation on an expired snapshot; do not mark an incomplete scan successful.

For AC2.2 require `runtime == "claude-code@windows"`, the expected `project_name`, and both separate numeric cache fields. For AC2.3 require `runtime == "claude-code@hermes"`, `work_type == "devir"`, the observed devir `job_ref`, and `project_name == "pegadocrag"`. Equal or zero cache values are not failures: “distinct” means separate fields, not necessarily unequal measurements. No prompt-content field is in the export allowlist.

Evidence: routes/meta.py:9–20; routes/jobs.py:111–117,138–179; routes/export.py:38–48,159–162,201–236,353–358,394–473. `from` and `to` are required in practice; `limit` and `cursor` are optional. `/api/jobs?days=1` is valid.

F5 — MEDIUM — status.md:217, step 6a
Problem: The launcher suite is not, by itself, proof that patches reached the intended owner files: absent launchers skip, and an existing unpatched S2/S3 launcher is patched only in a temporary copy. Thus a green result can still leave the real launchers unchanged.
Scenario: The suite runs from a detached backend checkout whose sibling directory lacks the owner repositories, or against a different unpatched owner tree. It skips or tests temporary patched copies rather than verifying the deployed files.
Fix: Add this gate: “Apply each patch from its actual Windows owner repository root, using an absolute patch path; stop on nonzero exit. Point the test at that same owner-parent directory and the real skill file. Before running it, verify all three real launchers exist and contain `TOKEN_INSPECTOR_JOB_REF`; after running it, reject any skipped launcher case.”

Owner-root commands:
```sh
git apply --check "<backend>/Plans/o9-job-correlation-cc-producer/shared-patches/S2-PEGADocRag-hermes.sh.patch"
git apply "<backend>/Plans/o9-job-correlation-cc-producer/shared-patches/S2-PEGADocRag-hermes.sh.patch"
```
Use S3’s filename in the other owner root. Then, from the Windows backend checkout in PowerShell:
```powershell
$env:O9_LAUNCHER_ROOT = "<owner-parent>"
$env:O9_HERMES_SKILL = "<absolute-hermes.md-path>"
python -B -c "import os,pathlib; r=pathlib.Path(os.environ['O9_LAUNCHER_ROOT']); assert all((r/n/'scripts/hermes.sh').is_file() and b'TOKEN_INSPECTOR_JOB_REF' in (r/n/'scripts/hermes.sh').read_bytes() for n in ('AIFromScratch','PEGADocRag','PEGADocRagAgent'))"
python -m pytest -q -rs tests/test_launcher_env_contract.py
```
Stop if the assertion, tests, or required no-skip check fails. Once the marker is present, the suite really selects the real file, not the patch copy (tests/test_launcher_env_contract.py:23–31,85–100).

F6 — MEDIUM — status.md:212,218–224, steps 1/7–9
Problem: “Unchanged” prompt count and “no new rejects” have no recorded baseline, and step 9 checks plugin counters but omits the independently failing CC producers. There is also no explicit failure gate preventing promotion after a failed prerequisite or partial live check.
Scenario: An installer succeeds but the Windows producer cannot reach the backend. Hooks still exit successfully and silently; plugin rejects remain unchanged, while CC `failed_posts` rises. Alternatively, a final zero count is asserted without a pre-deploy observation.
Fix: Add: “Before changes, record only the non-null prompt count and numeric producer-counter baselines. Repeat after the corresponding live run. Stop advancement on any failed prerequisite; do not install the plugin/hooks after a failed backend check. Missing AC evidence remains pending, never passed. New CC rejects/failed posts or unexpected prompt retention halt acceptance and trigger investigation or the approved rollback.”

For the approved deploy, the content-free count query is:
```sql
SELECT COUNT(*) AS nonnull_task_prompts
FROM tasks
WHERE prompt_text IS NOT NULL;
```
Run it through a read-only connection before and after deployment; require zero both times. Do not substitute the task-list `has_prompt` filter, which excludes expired/purged rows. Check CC `rejected` and `failed_posts` in each machine’s producer `counters.json`, as well as plugin counters; preserve only numeric evidence.

The JEV idle prerequisite can be checked without direct DB access:
```sh
curl -fsS "$TI_URL/api/tasks/evaluator-status" |
  jq -e '.running == false and .current_run == null'
```
Keep the separate queue/process-idle checks and gateway-restart approval. Evidence: routes/tasks.py:229–231,382–410; producers/claude_code/cc_state.py:25–27; producers/claude_code/README.md:52–55; CLAUDE.md:192–195.

## Checked and OK

- Reviewed the requested backend `08f28ce` and plugin `7a6d094`; both detached worktrees were clean before and after review. No production DB, service, installer apply, owner launcher, or PEGA system was accessed.
- Forward deployment order is correct: backend restart/check precedes plugin installation and hook installation; the backend tag cap is 1024 bytes (status.md:214–219; routes/events.py:31–32,229–231).
- The dependency expectation is correct: `git diff ddc7552 08f28ce -- requirements.txt` returned no differences.
- Startup calls the verified online backup before migration. Its actual filename is `<DB_PATH basename>.bak-v10-<YYYYMMDDTHHMMSSZ>` beside the source DB—not under `~/backups` and without a newly appended `.db` extension. Verification checks integrity, event count, and source schema version; failures abort startup (database.py:68–87; migrations.py:23–74).
- Migration DDL and ingest-sequence healing share the explicit migration transaction. Code-only rollback compatibility is supported by nullable `ingest_seq`, a defaulted `job_ref_conflicts`, and a partial sequence index; later startup assigns missing sequences above the stored high-water (database.py:42–58,85–90; migrations.py:293–295,324–373).
- Nothing in the runbook enables capture/JEV or sets an ingest credential. Backend defaults and plugin defaults are off, with empty allowlists; actual production flag/allowlist state still requires the deploy-time check (status.md:208–219; features.py:139–164; plugin config.py:33–37).
- `/api/meta` exposes the schema, Boolean capture/JEV states, their disabled reasons, and `task_prompt_allowlist`. Its schema value is the code constant, not a direct database-version query; healthy startup/migration evidence is therefore also required (routes/meta.py:9–20; database.py:68–95).
- Installer merge, idempotence, exact-command uninstall, preservation of foreign hooks/keys, and summary privacy passed in-memory checks. It targets only Stop, SubagentStop, and SessionEnd; output contains operation counts/settings path, not a settings diff or foreign commands (install.py:28,41–110,228–236).
- Installer apply takes the shared OS lock before reading/planning and holds it through backup and replacement. Windows uses byte-zero `msvcrt` locking; Linux uses `fcntl.flock`. It checks the source hash, reserves backups exclusively, and replaces atomically. Default/explicit dry-run does not write; `--settings` selects the target file (install.py:130–225,246–260). Native Windows execution and filesystem apply were not performed.
- S2/S3 quoting is sound for the introduced values: work type is alphanumeric, attempt is decimal, and the constructed job IDs contain only digits/hyphens with the optional devir prefix. Neither `|` nor `&` can enter the new sed replacement through those inputs; eight patch-derived validation/sed cases passed in memory. The remote quoted heredoc preserves runner-time substitutions (S2 patch:6–28; S3 patch:6–28,35–48).
- The supplied successful owner-root `git apply --check` results are consistent with the patch paths; no additional owner access was attempted. The launcher suite stubs remote execution and checks send/devir env contracts (tests/test_launcher_env_contract.py:103–163).
- Schema rollback CLI, version guards, SQLite ≥3.35 requirement, reverse order, and matching application-artifact guidance agree with the runbook. The separate `--to 11` then `--to 10` commands are valid, and must run before moving away from O9 (scripts/rollback_schema.py:22–28,41–86; status.md:229).
- `systemctl --user restart token-inspector.service` matches the supplied unit. `hermes gateway restart` is a valid CLI command; the external-shell and separately approved idle-window instructions are appropriate. Neither restart was executed (status.md:215,218; Hermes hermes_cli/subcommands/gateway.py:130–144; hermes_cli/gateway.py:3358–3428).

