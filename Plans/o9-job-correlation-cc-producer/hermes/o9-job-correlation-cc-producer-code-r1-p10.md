# Hermes code review r1 — part 10 of 10

TAG: `o9-job-correlation-cc-producer-code-r1-p10` · prompt: plugin + shared files — Hermes plugin diff and the S1–S4 launcher/skill changes.

FINDINGS:
F1 [HIGH] job.py::_read_env; __init__.py::_subagent_start; tests/test_job_tags.py::test_plugin_payload_sentinel — Shape validation does not enforce AC1.9’s value-level privacy boundary — scenario: TOKEN_INSPECTOR_WORK_TYPE=privatehost passes unchanged into outgoing tags; a resolved parent name of zz-canary-host.internal passes unchanged into parent_project_name. Both can carry hostnames. The sentinel instead supplies punctuation-heavy work types and appends /x to the parent hostname, testing only rejected shapes. Backend normalization cannot prevent disclosure in the plugin’s outgoing payload — fix: normalize work_type to the closed vocabulary before emission; define a privacy-safe project identity rule shared with backend task identity, and test hostname/text canaries that satisfy the allowed syntax.
F2 [MEDIUM] S1/S2/S3 send job-env blocks; S3 devir-baslat — Optional job variables are omitted from export statements but never cleared from the runner’s inherited environment — scenario: a remote runner inherits TOKEN_INSPECTOR_JOB_ATTEMPT=7 or TOKEN_INSPECTOR_WORK_TYPE=code, while the new launch supplies no corresponding HERMES_* value or supplies an invalid one; the plugin emits the inherited value for the new job. devir-baslat overwrites work type but retains an inherited attempt — fix: unset all three TOKEN_INSPECTOR_JOB_* context variables before exporting the validated launch context; test absent and invalid inputs with a pre-populated runner environment.

UNSURE:
- AC1.7’s supplied plugin test uses project x for both parent and child; it verifies propagation, not actual A→B attribution. Cross-project integration, arrival-order handling and unchanged O10 tests cannot be verified from this part.
- AC1.2/AC2.3 launcher integration and deployment ordering are not executable evidence here. S2/S3 are explicitly pending approved deployment, not findings.
- AC1.4’s plugin test measures a locally reproduced cleaner rather than the backend implementation. Actual backend acceptance and fixture parity require the other parts; the recorded J2 measurement is accepted.
- The remaining dedup, export snapshot/cursor, migration/rollback, repair transaction, CC hook latency/concurrency and installer controls are outside the supplied implementation scope.
- Review used only supplied code plus an in-memory check of literal lengths and regex acceptance. No repository tests, remote checks or PEGA calls were run; no files were changed.

VERDICT: Changes needed for value-level privacy and inherited job-context isolation; the remaining cross-lane guarantees are unverified in this part.
