# Selected-run usage and cost — B15, September 24, 2026

`GET /projects/{id}/usage/v1?range=all&offset=0&limit=100` is a user-JWT owner read.
Ranges are all/7d/30d/90d, measured by ingestion time; offset is nonnegative and limit
is 1–100. Newest runs come first. PostgreSQL computes full-period totals and feedback;
only bounded run/result/config JSON is paged. Catalog APIs never join this data.

The envelope contains version, projectId, isExample, range, totals, feedback, runs,
offset, limit, total and limitations. Monetary values are exact USD Decimal strings,
not floats. `completeCost` and `partialCost` are separate nullable sums; no estimates
in a class means null. Counts identify complete/partial/unavailable/legacy and all
reported runs. These are estimates of reported CI work, not account spend or invoices;
unreported aborted requests may cost money. No feedback or gate enters cost math.

Each row identifies the **executed** revision, task/mode, provider/model, deployment and
runtime version; current project picks never label historical usage. `usage` retains
nullable provider/runner fields, `usageStatus` independently reports complete/partial/
unavailable captured counters, and `billing` stores version/basis/status/knownCost,
category token counts/rates/charges, immutable rateSnapshot and explicit reason codes.
OpenCode is always partial usage coverage at best because its zero-filled counters and
hidden retry layer cannot establish full provider consumption.

Reports, patch/base/artifact identities, named-test discovery/execution and baseline
identity evidence, diagnosis cause/uncertainty/next steps and original failure remain in
`taskResult`. Execution, validation and CI gate remain distinct. Full logs and patches
stay in CI. Findings use the existing owner-scoped findings/feedback APIs; feedback
shows accepted, rejected, rated and total, and is not recall or a quality guarantee.

## Rate authority and deterministic selection

`billing_rate` is an append-only operator-owned exact `execution_runtime` schedule.
It has a finite aware effective interval, dated source URL/observation, a rate version,
USD rates per million, service-tier applicability and a bounded explanation of the
reviewed tier evidence. Rates never come from benchmark observations or a user payload.
**No prices, runtimes or activation evidence are seeded by B15.** Missing evidence is
expected to yield unavailable cost until a separately approved operator change.

Reviewed tooling/migrations call `app.billing.rates.register_rate(db, runtime_id,
effective_at, valid_until, schedule)` within their own transaction. `Schedule` is the
strict input contract: nonnegative finite Decimal rates (up to nine decimal places),
1–10 increasing `upToInputTokens` thresholds ending with null, and category keys
`input`, `output`, `reasoning`, `cache_read`, `cache_write`, `cache_write_5m`,
`cache_write_1h`. Omitted prices are unknown, not free. `tierEvidence` must explain
applicability to the exact runtime/deployment and token categories. The `all` service
tier is allowed **only** for reviewed evidence that the same rates apply to every
possible service tier; it is not a way to assume standard pricing. There are no such
production schedules in this change. Fixture rates are explicitly synthetic.

At configuration revision creation, the latest effective, unexpired committed schedule
for that exact runtime is copied into `execution_revision.billing_snapshot`, including
rate/runtime IDs, interval and pin time. New schedules create rows and affect only new
revisions. Re-picks/preferences create new revisions. Missing snapshots remain null;
old revisions/runs are never repriced or backfilled. Reports pin their result to that
revision; expiry at ingestion yields unavailable cost even if some counts are present.
Thus a delayed report crossing expiry is conservatively unavailable. Editing a project
creates a new rate opportunity without altering earlier reports. This is a versioned
estimate policy, not a claim that frozen rates reproduce a provider invoice forever.

Native input context selects an input-length tier per request; Anthropic context includes
read/write caches. Multi-step OpenCode has no per-request lengths, so multi-tier schedules
remain unavailable. The current tier contract covers whole-request input-length bands,
not progressive marginal tiers, batch discounts, reserved capacity or account discounts.
Unsupported pricing must remain unavailable rather than approximate it silently.

## Native and normalized accounting

- OpenAI native input includes cached reads; subtract reads once. Output already contains
  reasoning, so never add reasoning again. Unexpected nonzero native write counts leave
  input unpriced because this profile defines no independent write tariff.
- Anthropic input excludes reads/writes. Add cache reads and separate 5-minute/1-hour
  writes. Output includes reasoning. A missing TTL split prevents a complete estimate.
- Google native input includes reads; subtract once. Candidate output excludes reasoning;
  add the separately priced reasoning count. Missing required categories stay unknown.
- OpenCode categories are already disjoint. Do not subtract caches or reasoning again.
  Anthropic write TTL is unavailable. Only tier-invariant, single-length-band schedules
  can price captured normalized categories; hidden retries and zero-filling always keep
  the estimate partial, never complete. Attempts, steps and calls are not HTTP requests.

Explicit zero needs no rate to contribute zero; unknown usage does. Known zero categories
alone cannot turn otherwise missing usage into a useful partial charge. Aborted/refused
runs retain available counts. Native v1 allows one request and zero retries; no retry
usage is invented. A reported total with unresolved categories prevents completeness.

## Additive telemetry and rollout compatibility

New configurations advertise `billingContractVersion: 1`. Native review/Other agents add
optional `providerUsage.serviceTier` and `reportedModelId` only when that capability is
present; absent values are omitted. Older backends/configurations retain their previous
payload shape. Older agents remain accepted by the new backend, with monetary estimates
unavailable when returned identity/tier cannot be established. Profile request parameters,
ceilings, pending verification and gate behavior are unchanged. No live transport runs.

OpenAI response `service_tier=default` maps to standard; Anthropic reads
`usage.service_tier`; Google reads `usageMetadata.serviceTier`. Known returned tiers are
preserved; missing/unknown values stay null. A schedule mismatch or unknown/mismatching
returned model makes native cost unavailable. Provider-returned identity is reported
telemetry, not cryptographic proof. OpenCode still does not report returned identity.

Official schema references reviewed September 24, 2026:
[OpenAI returned tiers](https://developers.openai.com/api/docs/guides/fast-mode),
[Anthropic service tiers](https://platform.claude.com/docs/en/api/service-tiers),
[Google usage metadata](https://ai.google.dev/api/generate-content#UsageMetadata).
These references establish telemetry semantics, not prices or verified model availability.

## History and migration

Migration `a0b1c2d3e4f5` is additive after `f9a0b1c2d3e4`. It creates the schedule table
and nullable revision/run snapshots. PostgreSQL rejects schedule updates/deletes and
run billing/attribution updates; revision immutability already covers the new column.
A populated downgrade refuses before destructive DDL. Empty reversal is tested.

Legacy `actual_cost`, `baseline_cost`, `savings`, feedback and APIs remain unchanged.
Explicit runs still leave those compatibility columns null. The new API exposes a legacy
selected-model amount only as `legacyCost`, with its input/output-only and attribution
limits, outside new totals. It never relabels a hypothetical comparison as measured savings.
Backend/migration first, compatible agent next, then frontend is the rollout order; actual
migration, schedule insertion, runtime activation and deployment need separate approval.
