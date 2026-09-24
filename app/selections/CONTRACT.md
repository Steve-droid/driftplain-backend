# Explicit execution selections — B6, September 24, 2026

**B7 extension:** [Task/configuration/result contracts](../TASK-CONTRACT.md) adds typed
`taskConfiguration`, an explicit version-1 negotiation inside v2 config, shared result
vocabulary and owner-scoped metadata reads. The B6 selection/evidence rules below remain.

B6 adds `/execution/v1` with configuration `contractVersion: 2`. Public `/catalog/v1`
remains independent of runtimes, ownership and usage. Nothing imports or enables a runtime
on startup. The new runtime table intentionally starts empty: the dated B1 audit found
**zero reusable exact current task/profile verifications**. Synthetic tests are not activation
evidence. Existing `agent_runtime_config` rows and legacy projects continue unchanged.

## Wire contract

User JWT:

- `GET /execution/v1/candidates?task=ci_review&mode=single_call`: policy, groups, items, total,
  offset. Optional `q`, `group`, `language`, `proposeFix`, `offset`, `limit` (1–100).
  Each item identifies a canonical model, trusted runtime, hosting deployment and exact
  source observation/snapshot. Direct search is sufficient; no prior browsing state is needed.
- `POST /execution/v1/projects`: `{name, selection, reviewPreferences?}`. Selection is
  `{task, mode, runtimeId, observationId, method, group?, language?, proposeFix?}`.
  Unknown fields, URLs, provider IDs and client-supplied policies are rejected.
- `GET/PATCH /execution/v1/projects/{id}`: owner-scoped current selection/revision. PATCH
  can rename, edit preferences or explicitly re-pick. Re-pick makes a new selection and
  revision; preference changes make a new revision of the same selection. Legacy projects
  may transition only with a full explicit selection; its previous recommendation and
  baseline IDs are retained in nullable historical bridge columns. Example projects cannot transition.
- `GET /execution/v1/projects/{id}/revisions/{revisionId}`: owner-scoped immutable
  historical configuration, including after re-pick or runtime disabling.
- `POST /execution/v1/projects/{id}/ci-token`: reuses the existing project token mechanism;
  returns the token once. `POST .../ci-token/rotate` replaces it when lost. A transitioned project's existing token remains valid. Does not
  produce a Jenkins snippet or claim the currently published agent understands v2.

Project `X-CI-Token`:

- `GET /execution/v1/projects/{id}/agent-config`: `contractVersion: 2`, `projectId`,
  `executionRevisionId`, `selectionId`, `taskType`, `executionMode`, `capability`,
  `runtimeId`, `runtimeVersion`, canonical/deployment IDs, model route/auth-variable **name**,
  preferences and policy snapshot. It never returns credentials.
- `POST /projects/{id}/ci-runs`: adds optional `executionRevisionId` to the existing input.
  It is **required** for explicit projects. The revision must belong to this project and
  `model` must exactly equal its bare `providerModelId`. Store the executed task/revision,
  even when the project was subsequently re-picked or its runtime disabled. Old payloads
  continue working on legacy projects. A revision is configuration attribution, not proof
  that a client actually called a provider. B7 extends result/task schemas; B15 owns billing.

The new project has no recommendation option or baseline. Selected-model legacy billing is
not guessed: its cost/baseline/savings stay null until B15. Existing `/projects` list/detail
reads gain nullable option/baseline IDs plus `executionRevisionId`; existing legacy values
stay unchanged. The legacy create/recommendation workflow remains a compatibility surface
until its planned retirement in B17. It cannot mutate an explicit project back to legacy.
Legacy project PATCH may rename/edit explicit preferences through the same revision service;
selection/baseline/task changes must use the explicit API. Legacy agent-config and ci-setup
reject explicit projects instead of generating an incompatible old-agent command. B7–B14
own agent consumption and the new setup UI; B6 releases only these additive contracts.

## Policy and comparable groups

Policy `2026-09-24.1` pins exact catalog version labels from B3/B4, task/mode/capability,
input/output contract identifiers and optional benchmark/metric/direction. A full snapshot
is saved for every selection; ranked selections additionally save the exact result, group,
score and ranks. An unranked selection has no policy result and no recommendation claim.

- PR review: CodeReviewBench F1, Kodus replay, exact runner version, Haiku 4.5 judge,
  complete 30 PRs / 95 bugs, same reasoning configuration. Unknown settings never rank.
- Security: RealVuln 3.1.0 / ground truth 3.0.0 strict F3 is pinned but **no comparable
  score group is activated**. A future policy revision must admit an actually reviewed
  complete runner/prompt/result artifact; old 2.1 results cannot enter.
- Python test generation: TestGenEval Extra `e_at_1`, fixed Python one-attempt protocol.
  Node tests, diagnosis, its repair option, and Other have no primary ranking at launch.
- Other uses `custom_single_call` or `custom_opencode` support, never an inferred task fit.
  Diagnosis read-only/fix and Python/Node tests have separate support capabilities.

A group hashes snapshot, evaluator, benchmark version, known comparison configuration,
metric definition and metric coverage. Source-refresh snapshots stay separate. For review,
the importer-authored cross-model configuration fingerprint permits distinct provider routes
in the same group while retaining their actual identities. Any unknown methodology prevents
ranking. Other tasks use exact protocol fingerprints. Multiple primary metric scopes on one
observation remain unranked pending an explicit metric-choice contract; never choose a max.

The client explicitly chooses a group before getting `benchmark_ranked` items. With no group,
all supported observations remain valid unranked choices, and the API lists available groups.
Different observations for a model are distinct choices. Scores are decimal strings at stored
precision and preserve `reportedValue`. Higher/lower directions use competition ties (`1,1,3`)
and stable canonical/runtime/observation IDs. Prices and speed are absent from sorting.
`sourceRank` is only the positive integer actually reported as `source_data.rank`, otherwise
null. `sourceGroupRank` is separately calculated over the published comparable evidence,
including unsupported/unresolved rows. `rank` and `position` describe the supported group
before text-search filtering; `position` breaks display ties but `rank` does not. Group counts
are source-result counts, not inflated claims of distinct models.

A missing primary score, wrong-task evidence or unknown settings does not imply unsupported.
Any canonical source-backed observation with a published metric record (including an explicit
missing value) allows an unranked choice if exact task/mode support exists. Legacy backfills,
metadata-only models and unresolved source aliases do not establish new selection eligibility.
No runtime means an empty set; the legacy catalog fallback is never used here.

## Ownership, concurrency and lifecycle

Trusted runtime rows separate canonical model/deployment from task/mode/capability and exact
executable/provider/auth/version/verification metadata. Only reviewed operator migrations may
insert them; there is no public activation API. Catalog endpoint URLs are never execution
URLs. Route/provider/auth mapping is checked on save and config fetch. Identity/evidence is
immutable; enablement/status may change. A new verification or executable identity needs a
new runtime row. Supported provider protocol names alone never activate a row.

Composite foreign keys enforce project ownership, runtime/model identity, observation/model/
snapshot identity, revision/selection/project identity and run/revision/project identity.
Selections and revision snapshots reject UPDATE. Existing owner-authorized project deletion
retains cascade behavior; evidence and its conservative hold remain retained.

Project row locks serialize edits, config fetch and run ingestion. Runtime row locks serialize
save/fetch with disabling. A request already issued before disabling cannot be recalled;
subsequent config fetch fails, while reporting a finished old revision remains permitted.
An importer-compatible lifecycle upsert establishes the hold **before** selection insertion,
in the same transaction. A database trigger independently enforces the hold and source origin.
Failed choices roll back the project and holds. Refresh/source disappearance cannot rewrite
pinned evidence. Holds are deliberately indefinite; B6 adds no release/purge/retention job.

Migration `d7e8f9a0b1c2` is additive after `c6d7e8f9a0b1`. Existing rows retain null revision
bridges and their prior options/tokens/history. An empty B6 downgrade is reversible; a populated
runtime/selection/revision downgrade aborts before DDL. No production migration is authorized.

## B11 named-test setup extension

`GET /execution/v1/projects/{id}/ci-command` now accepts `test_generation` in addition to
Other. It is owner-scoped, checks the current enabled runtime, requires operator image
digests and the reviewed task environment, and adds `jenkinsStage` for named tests. Its
command exits nonzero on failed/unavailable tests and archives external job artifacts.
Python retains the TestGenEval Extra policy; Node remains unranked. No runtime row is seeded.
Historical executed revisions still accept results after re-pick/disable. Full picker and
preview UI remain B13/B14. See [test-generation support](../../agent/TEST-GENERATION-SUPPORT.md).

B12 adds project-token `POST /execution/v1/projects/{id}/failure-claims` and extends owner
`ci-command` with a failure-preserving diagnosis wrapper. Claims are permanent, unique per
project/build/stage, and bound to the executed immutable revision. See
[diagnosis support](../../agent/DIAGNOSIS-SUPPORT.md). No runtime is seeded or enabled.

## B13 exact search and named setup

`GET /execution/v1/candidates` adds optional positive `catalogModelId` and `observationId`
filters for Explorer-to-CI intent and exact revalidation. They filter after comparable-group
ranking; ranks/group counts retain their existing meaning. Neither field grants support or
changes public catalog queries. Wrong combinations return an empty eligible set.

Owner `ci-command` now also supports review and security. Review mounts its prepared diff
into the review image; security mounts a bounded exported source tree read only into the
scanner image, with the persisted CPU/memory/PID ceilings, read-only rootfs, dropped
capabilities and no engine socket. Both require operator-pinned image digests, return the
exact current revision and archive JSON while preserving a nonzero agent exit. Historical
pre-B7 configs must be edited into a new task-contract revision before command generation.
Other/tests/diagnosis keep their existing launcher and failure behavior. No migration, agent
consumer change, live verification or runtime activation is introduced by B13.

## B14 Other authoring and Jenkins setup

Other `ci-command` now adds a `jenkinsStage` while retaining `command`. The distinct
Custom task stage captures single-call JSON, or OpenCode JSON plus result/patch artifacts,
in job-private external scratch and archives them in `post { always { ... } }`. The shell
preserves nonzero generation/validation exits. Prompt text and configured validation argv
never enter the generated shell. Single-call containers apply persisted CPU/memory/process
ceilings as well as the existing read-only rootfs, mounts and agent-enforced time/token bounds.
Owner checks, operator digest requirements and exact enabled runtime checks remain unchanged.
No config/result version, migration, executor implementation or runtime activation changes.
