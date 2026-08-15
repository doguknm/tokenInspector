# ADR-001: Privacy-Safe Project Identity and Inventory Separation

- Status: Accepted

## Context

Event-backed project aggregation was presented as a Projects list, which could be mistaken for a machine-wide repository inventory. Early producer attribution also normalized Git root directory names and accepted prompt markers by default. This allowed test prompts and folder names to create misleading project identities.

## Decision

Maintain two explicit datasets:

1. **Known Repositories:** bounded direct-child Git discovery under configured roots, including repositories with zero telemetry.
2. **Observed Activity:** aggregation over actual LLM request events by attributed project name.

Canonical identity prefers configured aliases and sanitized Git `origin` repository slugs, then falls back to the Git root directory name. The complete remote URL and absolute path are never stored or returned; inventory uses a hash-first workspace identifier.

Prompt marker attribution remains available only as explicit opt-in and is disabled by default.

## Rejected Alternatives

- Folder basename as the sole canonical identity: local folders can differ from repository names.
- Prompt marker enabled by default: ordinary examples and smoke tests can create phantom projects.
- One merged Projects table: filesystem presence would imply telemetry activity.
- Recursive scan of the full home directory: excessive privacy and performance scope.
- Persisting full paths/remotes for convenience: unnecessary sensitive metadata.

## Consequences

The UI and API must preserve inventory/activity semantics. Multiple local workspaces may share a canonical repository name while retaining separate workspace hashes. Producers without a trustworthy workspace signal use an explicit fallback rather than inventing a repository. Data cleanup requires trace/session provenance rather than broad project-name deletion.
