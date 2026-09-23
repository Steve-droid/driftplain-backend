# Public explorer additions — September 24, 2026

B5 adds compatible read metadata to `/catalog/v1`; it does not change schema, imports,
model identity, runtime configuration, selection policies or immutable observations.

- Observations keep the historical default. `view=active` opts into the importer active
  pointers and binds cursors to that view. Legacy/unmanaged snapshots are available in
  history, with unknown status. Failed refreshes retain last-good active evidence.
- `snapshotStatus`, `citationUrl`, `coverageNote`, `runner`, `runnerVersion` and
  `protocolConfiguration` expose evidence meaning. Configuration was already public on
  benchmark detail. Only the coverage string is projected from importer notes; raw notes,
  source payloads, review identities and arbitrary source data are not serialized.
- `sourceUrl` retains the existing snapshot-artifact meaning (including content-addressed
  URNs). `citationUrl` identifies the observation's cited upstream result. Artifact SHA-256
  is never labeled a hash of the cited paper unless they are actually the same artifact.
- Benchmark list/detail adds `collection`, `versionLabels`, `metricUnits` and `sources`.
  Collection is a server filter with query-bound pagination. Authored B1 explanations and
  collection metadata live in `presentation.py`; persisted authored copy takes precedence.
- `/sources` is a bounded paginated public projection: citation/attribution, active snapshot,
  retrieval/publication/check dates and `ok|failed|unknown` refresh status. It never exposes
  conditional headers, error details, raw payloads, holds or lifecycle records. No scheduler
  is implied and no elapsed-time threshold invents a stale/failed state.

The UI uses exact benchmark/version/protocol/evaluator/snapshot/metric/coverage scope for
comparisons. Unknown provenance isolates observations. No automatic maximum, cross-source
aggregation, fuzzy identity resolution, synthetic rank or runtime-support badge is added.
Runtime eligibility and selections remain B6 scope. Public routes require no JWT or project.
