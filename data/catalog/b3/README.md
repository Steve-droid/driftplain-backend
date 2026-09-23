# B3 familiar benchmark imports — September 23, 2026

These nine reviewed manifests and the pinned SWE-bench adapter form the ten B3 families.
They are bounded **evidence inputs**, never provider configuration or evaluation code.
Steve approved B3 delivery on September 23, 2026. The frozen reviewer metadata records the
earlier transcription stage; it identifies the transcription author rather than claiming that
Steve independently audited each upstream table.

## Source contract and interpretation

`registry.json` is a byte-for-byte copy of portfolio B1's 19-family registry (SHA-256
`3ada3d03d3bc94518d9369e9279b2ea036ae1db6277ee193d10263e3c76f9495`). Only the ten B3 IDs
are enabled. The loader checks that digest. To change source metadata, review the change against
the normative portfolio manifest, then update the frozen copy and digest together.
Existing conflicting source/dimension metadata is rejected, not silently rewritten. A revision
that changes persisted metadata also needs an explicit reviewed data migration; changing only
the registry file is not sufficient to repoint an already imported source.

The accepted source artifact for a reviewed import is the cited JSON manifest itself. Its exact
bytes are retained in PostgreSQL and SHA-256-addressed. Each row separately records its upstream
URL, artifact hash and table/row locator. **A manifest hash is not an upstream report hash.**
Unknown publication dates remain null; benchmark release dates are not substituted for them.
Unknown settings carry an explicit reason and force a source-row-specific protocol group, so
missing methodology cannot establish comparability. Normalized percentages do not change the
upstream unit: ratio conversions, original values and uncertainty are retained in source data.

| Family | Reviewed evidence and coverage |
|---|---|
| GPQA Diamond | Authors' arXiv `2311.12022v1`, Table 5, Diamond column: three models, few-shot CoT. The pinned B1 `merged.csv` is question-level GPT-4 evidence, **not** a three-model score table. The cited author paper supplies the missing aggregate evidence; the original registry is preserved. |
| HLE | Official `lastexam.ai` Quantitative Results table, explicitly the final April 3, 2025 dataset; linked by that page to CAIS's dashboard. Four labels, o3-mini judge, accuracy and calibration error. The starred DeepSeek row uses text-only data. Tool policy and result publication date are not given. No HLE-Rolling/Diamond substitution. |
| MMLU-Pro | Pinned `f418b11` mini-leaderboard, first three rows, B1's five-shot CoT protocol; evaluator code is separately cited. No nearby-model alias matching. |
| AIME 2025 | OpenAI gpt-oss model card, Table 3: two models × three effort levels × tools/no-tools. The number of samples is unreported. Two models remain two models, not twelve. |
| FrontierMath Tier 4 v2 | Epoch's internal CSV, three successful private v2 runs, retaining run ID, model ID, ratio, task version, start time and standard error. The CSV supplies **standard error**, not a specified confidence interval; no 95% interval is invented. The bulk CSV observed on September 23 exceeds the B1 2 MB automatic-fetch limit, so only the bounded reviewed rows are accepted. |
| ARC-AGI-2 | Official evaluation/model metadata, three Base LLM semi-private rows. Source model ID and category remain in each protocol. Public sets, previews, competition systems and ARC-AGI-3 are not combined. |
| SWE-bench Verified | Exact official B1 JSON, 4,091,442 bytes, SHA-256 `83cd949a9582f4dd68b0a07148cd86ff0eae6a05b0f2d2298a8f3717baf89d2d`. All 180 Verified submissions. `checked=true`, false/explanatory false, and null stay distinct. `2+` is a lower bound, never converted into exactly two attempts. Agent versions are not misrepresented as evaluation-harness versions. |
| Terminal-Bench 4 | Three official Harbor `4-0-0` rows matched to pinned run manifests by agent/model/effort. Run manifests define dataset `v4.0.0` and five attempts but contain **no scores**. The Harbor response supplies accuracy and CI95 half-width. Bounds are accuracy ± published half-width; original fields are retained. Model release dates are not result publication dates. |
| MRCR v2 | Definition only, no rows. A new comparable table needs a reviewed manifest with context length and needle count. |
| OSWorld-Verified | Official team-verified workbook, `Eval Results` rows 2, 8 and 11: three screenshot-only, 15-step, 361-task observations. Run count is unreported and preserved as unknown. No 360-task or OSWorld 2.0 substitution. |

Source attribution, licenses and access limits remain in the registry and are persisted with
source metadata. No benchmark questions, answer keys, task repositories, trajectories or
executable third-party programs are bundled. The SWE fixture contains the official leaderboard
and per-instance result metadata, not task source code. The reviewed manifests contain bounded
reported facts and citations, not a redistribution of whole report publications.

## Running locally

Validation is the default and needs no database. From the backend checkout:

```sh
uv run python -m app.catalog.imports --source mmlu-pro --manifest data/catalog/b3/mmlu-pro.json
```

`--promote` explicitly writes to `DATABASE_URL`, after migrations have been applied. A local
reviewed manifest is trusted operator input, not an arbitrary public request. Reviewed alias
mappings require reviewer/date/citation metadata and an existing canonical model. Conflicting
reviewed mappings fail the entire import. The automatic SWE feed cannot supply alias mappings.

`--fetch` is supported only for the pinned structured SWE source; both validation and promotion
use the same bounded fetch policy. The immutable URL and expected digest must be reviewed and
updated together for a new revision. Changed web reports do not automatically become scored
manifests. Their monitoring/review workflow and scheduling are later operational work; B3 adds
no CronJob, startup hook, seed job or public write endpoint.
Local SWE JSON validation and promotion enforce that same pinned digest; different bytes cannot
claim the pinned revision's provenance.

The transaction includes snapshot, dimensions, aliases, observations, metrics and active pointer.
Failures roll back all candidate writes, then record a sanitized failure code/count separately.
A per-source PostgreSQL advisory lock covers fetch through final bookkeeping. Conditional headers
are saved only after accepted responses. Unchanged checks update operational timestamps without
rewriting the source's publication date or immutable evidence.
Replaying any previously accepted hash is a no-op, including a historical snapshot: it does
not roll back the active pointer or extend the old snapshot's retention period.

The existing public catalog still exposes history. `GET /catalog/v1/observations?q=...` adds
source-label search, including unresolved labels; existing cursor shapes remain valid when the
new filter is absent. Import state, raw bytes, conditional headers and holds are private.
Catalog evidence never creates or enables a provider deployment or CI runtime.

## Retention policy chosen September 23, 2026

Superseded B3 snapshots become eligible for retirement **365 days after supersession**.
Active snapshots and evidence on hold for user selections/CI runs remain indefinitely.
Unchanged checks do not extend this period. Legacy B2 evidence without B3 lifecycle records is
excluded. Unsuccessful source bodies are not stored. Identical accepted bytes are deduplicated.

B3 applies the policy through lifecycle timestamps, holds and a tested eligibility report:

```sh
uv run python -m app.catalog.imports --source mmlu-pro --retention-report
```

**Eligibility does not delete data.** B2's append-only snapshot/observation/metric triggers remain
intact and raw payloads get the same protection. Physical deletion requires a separately reviewed
maintenance migration that checks actual selection/run references; it is not implemented or
scheduled by B3. B6 must establish an evidence hold before adding those references. Until such
maintenance exists, eligible history stays stored. There is no claim of a 365-day storage cap.

A populated B3 downgrade refuses to discard import state or the sole retained manifest. Empty
upgrade/downgrade/upgrade and populated preservation are tested against disposable PostgreSQL.

## Local verification — September 23, 2026

- Full fake-LLM suite: **734 passed, 11 skipped**, using disposable PostgreSQL 16.
- After the final local-file provenance fix: **88 importer/fetch/migration tests passed**.
- Repository Bandit gate, focused Ruff checks, compileall and whitespace checks passed.
- The built wheel was installed outside the checkout and its bundled registry/manifests validated.

Self-review regression tests cover source-metadata failure bookkeeping, timestamps after lock
acquisition, sanitized connection failures, historical replay, pure parsing without file access,
populated downgrade protection and enforcing the pinned SWE digest for local imports.
