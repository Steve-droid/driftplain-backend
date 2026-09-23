# B4 independent and CI-specific evidence — September 23, 2026

B4 enables the remaining nine entries of the **unchanged 19-family B1 registry**.
`../b3/registry.json` remains byte-identical to the normative portfolio manifest.
`contracts.json` separately records the actual supported acquisition mode, fetch location,
immutable digest when applicable, operational byte limit and reviewed launch scope. It
never changes the upstream audit's `importMode` or persisted source identity in place.

## Inputs and coverage

| Source | Actual import and evidence boundary |
| --- | --- |
| DeepSWE v1.1 | Official `/artifacts/v1.1/leaderboard-live.json`, a single structured aggregate artifact. Launch scope is **gpt-6-astra xhigh, claude-fable-5 xhigh, deepseek-v4-pro max**, each 113 tasks × four passes / 452 scored attempts. The full raw feed is retained. Its other configurations include exclusions and a 111-task row; they are not silently accepted as complete launch evidence. Missing scoped configurations, changed identities/efforts, fewer attempts/tasks or changed CI methods reject the batch. No trial files are fetched or claimed to have been independently audited. |
| LiveCodeBench v5 | Reviewed manifest reproduces the official v5 table formula over inclusive **2024-07-01–2025-02-01**, using its per-question percentages. The 7,443,275-byte upstream `v5.json` is cited by hash, not bundled or fetched automatically under the 3 MB reviewed-input limit. Contamination flags follow the official page's release-date rule. Two upstream IDs share `DeepSeek-V3`; the official display-label score aggregation is retained with distinct route/contamination configurations, not resolved into a canonical model. |
| LiveBench | Exact **2026-06-25** score CSV plus category/task JSON from site revision `19e766a5de4de07d672ed5bf9f0a69ceed1d39bf`. Both complete original files, their URLs, hashes and sizes live in one deterministic base64 envelope. No latest-window substitution. Task scores, category means and the mean of category means are separately scoped observations. Exact model/effort labels remain unresolved. The mutable website has newer rows even for this release; these are not mixed into the frozen revision. |
| CodeReviewBench | Official detailed `src/lib/data/leaderboard.json` at `531297bf50e5f065e3888d7e07b55dcacbf8df64`, **13 rows**. This contains judge, harness, provider route and uncertainty omitted by the compact public API. Eleven rows have 30 PRs / 95 bugs; two 29-PR rows with 91/93 bugs remain browseable and ineligible. F1, recall and precision are explicit percentages. A shared comparison-group fingerprint includes dataset, runner/version, mode, judge, coverage and reasoning settings; provider routes remain separately recorded. This marker does not override B3 unknown-setting isolation or establish runtime/policy eligibility. Run-to-run stdev and recall intervals remain attributed source data, never fabricated F1 intervals. `costBasis` includes list-price estimates, so these costs are not advertised as measured API spend. |
| RealVuln | Exact pinned manifest, benchmark **3.1.0 / ground truth 3.0.0**, 140 repositories. **No observations**: no verified matching complete general-purpose LLM strict-F3 artifact. The README version is not authoritative. Legacy ingestion/data remains untouched; no SAST product becomes a base model. |
| TestGenEval | Complete B1 CSV, ten labels, **Extra `e@1`**, percent higher-better. GPT-4o = **30.4**, not Full `f@1` = 31.9. Other columns survive verbatim. CSV schema, duplicate labels and scales are validated. Later valid source removals create a new snapshot without deleting old evidence. No successor aliases. |
| SWT-Bench | Four B1-reviewed observations at `330a649a764fab2fadaea632776eeae87272f74b`; Verified, agent identity and unittest/reproduction modes remain separate. Supplementary bug-reproduction evidence only. |
| LogDx-CI | Three reviewed preprocessing methods × **one** Sonnet 4.6 debugger, `real-agent-v1`, v1.2; diagnosis and confident-error values stay ratios 0–1. The 35-case denominator is preserved. Historical provider-error tuples were injected as zero-score abstentions, not removed from the denominator; the citation retains that exclusion policy. Single-shot debugger families are outside this launch manifest. |
| CI-Repair-Bench | Four B1-reviewed paper labels, Table 2 of `2604.27148v2`, **567 instances / 103 repositories**, first-attempt full-CI pass. Applied-patch counts stay auxiliary source data. The citation hash identifies the exact reviewed B1 transcription, not the paper HTML; original paper URL remains in source data. Labels are unresolved; no repair/diagnosis recommendation. |

### Provenance and refresh semantics

`fixture_sha256` records the bytes inspected on September 23, 2026 (TestGenEval reuses
September 22's exact CSV). It is **not** an immutable pin on a mutable feed. Immutable
GitHub inputs use `sha256` and both file and fetch paths enforce it. New immutable
revisions need a reviewed contract update; polling them cannot discover a new branch head.
Changing persisted source/dimension metadata still needs a reviewed migration as in B3.

DeepSWE and TestGenEval support mutable structured refreshes within their fixed semantics.
LiveBench is a complete pinned multi-file import; CodeReviewBench/RealVuln are pinned
single-file imports. LiveCodeBench/SWT/LogDx/CI-Repair require explicit reviewed manifests;
there is no claim of automatic score refresh for them.

Every acquisition is bounded separately and in total. Current B4 operational limits are
at most 3 MB for reviewed manifests and 2 MB for structured artifacts; LiveBench's complete
envelope is 8,343 bytes. These fit the existing **8 MB** raw-payload invariant. B1's optional
10 MB ceilings are not promises that the fetcher/storage accepts 10 MB. No migration is
needed. Raw accepted mutable feeds and bundles are stored transactionally with their hashes;
immutable retrievable single-file artifacts retain their exact pinned URI and digest.
A failed file or parser leaves the last-good snapshot active. Historical replay is a no-op.

The public metric contract retains percent, ratio, USD, tokens and steps, with explicit
finite nonnegative domains and nullable missing observations. Means may be fractional.
DeepSWE's reported USD/token/step means and explicitly labeled 95% interval are preserved;
Driftplain does not infer costs from token prices. Unknown result publication dates remain
null; artifact generation timestamps, run dates and benchmark release dates stay distinct.

## Operator command

Validation is the default, with no database or network required:

```sh
uv run python -m app.catalog.imports --source testgeneval --manifest data/catalog/b4/testgeneval.csv
uv run python -m app.catalog.imports --source livebench-2026-06-25 --manifest data/catalog/b4/livebench-2026-06-25.json
```

`--fetch` obtains only the reviewed allowlisted locations above. `--promote` explicitly
writes to `DATABASE_URL`; normal server startup does not import. The same immutable,
per-source-locked transaction and failure bookkeeping from B3 apply. No scheduling,
benchmark execution, LLM, provider activation, policy edits, production import or deployment.
B16 owns scheduling manifests; enabling them remains a separate operational action.

All runtime inputs ship in the wheel and backend image. Licenses/attribution/access limits
remain in the B1 registry; these are bounded result facts, not redistributed task datasets,
answer keys, source repositories or executable evaluation programs.

## Verification — September 23, 2026

- TDD red phase: 19 failed / 19 passed before B4 implementation.
- Focused importer/fetch/migration/API checks: **189 passed**.
- Complete backend/agent fake-LLM suite on task-owned disposable PostgreSQL 16:
  **836 passed, 11 skipped**; existing TestClient deprecation warning only.
- Bandit high/high, focused Ruff, compileall and whitespace checks passed.
- Installed wheel validated all nine B4 inputs outside the checkout.
- Read-only bounded fetch validation passed for DeepSWE, LiveBench, CodeReviewBench,
  RealVuln and TestGenEval. No database promotion, paid call or benchmark execution.
