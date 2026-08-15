# Changelog

All notable user-visible and operational changes are documented here.

## Unreleased

### Added

- Privacy-first single and batch lifecycle ingest with correlation metadata.
- Concurrency-safe idempotency using project-scoped client event IDs.
- Requested/resolved/pricing model separation, model aliases, unpriced-model visibility, and recost support.
- Cache-read, cache-creation, and reasoning token dimensions.
- Deterministic `request-shape-v1` complexity analytics.
- Bounded Git repository inventory independent of event-backed activity.
- Known Repositories and Observed Activity separation in the Projects dashboard.

### Changed

- Production SQLite storage moved outside the repository and runs with additive migrations, backups, WAL, and busy timeout.
- Project identity now prefers canonical sanitized Git remote repository names over local directory names.
- Prompt marker attribution is disabled by default in the Hermes plugin.
- Unknown pricing is reported as unpriced rather than zero cost.

### Security

- Raw prompt and tool-argument capture remain disabled by default.
- Prompt payload is capped when explicitly enabled.
- Inventory exposes a hash-first workspace identity instead of absolute paths or complete Git remote URLs.
