# Failure diagnosis and optional repair — B12, September 24, 2026

Both profiles are **pending exact live verification**. Nothing here enables runtime rows,
deploys an image, authorizes provider calls or claims live support. GPT-5.6 Sol, Claude Sonnet 5
and Gemini 3.7 Flash each have separate read-only and fix profiles (six integrations).
OpenCode remains 1.18.20 with the B9 pinned archive/binary hashes, fixed direct-provider
routes and low-effort options. Neither profile ranks models; LogDx-CI and CI-Repair-Bench
remain supplementary evidence. Review/security/Other/test support cannot activate diagnosis.

## Configuration and failure inputs

`ci_failure_diagnosis` defaults to `diagnosis_readonly`; `proposeFix: true` requires a fresh
eligible `diagnosis_fix` selection. The option is not another task or leaderboard. Existing
B7 historical metadata remains readable. New executable configuration requires:

```json
{
  "inputs": {"diff": false, "files": ["src/app.py"]},
  "diagnosis": {"stage": "Unit tests", "logArtifact": "upstream.log"}
}
```

`stage` is one upstream stage (100 characters, plain name); Driftplain stages are forbidden.
`logArtifact` is one literal CI artifact path under `AGENT_INPUT_ARTIFACTS`. Other named artifacts
and diff inputs are rejected for diagnosis. Source files are literal selections from the exact
`AGENT_BASE_COMMIT`, never the dirty checkout. Full base/file limits from B10 still apply.
`DRIFTPLAIN_FAILED_STAGE`, `DRIFTPLAIN_UPSTREAM_STATUS` and `DRIFTPLAIN_UPSTREAM_EXIT_STATUS`
supply the captured original outcome; `BUILD_TAG`/`MODELMATCH_BUILD_ID` is the stable build identity.
Commit, stage, exit status and original status persist independently of agent outcome.

Successful, cancelled, unmatched/agent stages, infrastructure without a failed command exit,
and missing/empty/oversize logs make no model call. They emit a skipped reason. The log must
be a regular, nonlinked UTF-8 file of at most min(maxFileBytes, 65,536) bytes. The launcher
redacts known selected-provider/CI credential values and common authorization, credential,
JWT/key, URL-userinfo and private-key patterns, then retains at most 8,192 UTF-8 bytes.
The API redacts again. Full raw logs stay in Jenkins, never in the API or model prompt.
Redaction is not a proof that arbitrary confidential text is safe: maintainers select suitable
artifacts. Credential-bearing writable source is refused rather than silently rewritten.

## Durable pre-invocation claim

Project CI-token `POST /execution/v1/projects/{id}/failure-claims` takes an exact
`executionRevisionId` and `failure` context. The server checks current eligibility and
configured failure inputs, commits an insert with unique `(project_id, build_id, stage)`, then
returns `claimed` and a new `claimId` only to the winner. Simultaneous deliveries, model
re-picks and configuration revisions cannot create a second claim for that failure. Unknown
responses, launcher crashes, model errors and delivery failures consume the invocation.
There is **no lease, takeover, automatic retry or reset endpoint**. This intentionally favors
at-most-once invocation over guaranteed diagnosis delivery. A new actual build has a new ID;
never manufacture a new identity to retry a consumed failure.

Migration `f9a0b1c2d3e4` adds `diagnosis_claim` after `e8f9a0b1c2d3`, with project/revision
composite ownership, unique failure identity and an update-rejection trigger. Populated
claims block downgrade before DDL. Owner-requested project deletion retains subtree cascade.
No production migration was performed. Claims contain bounded redacted context, no credentials
or full logs. Result ingestion binds claim, build, stage, commit, excerpt and executed revision;
old results remain reportable after an edit or runtime disable. Result idempotency cannot
substitute for this pre-call claim.

## Analysis and repair isolation

Both modes reuse B10's exact-commit exporter, trusted Docker launcher, bounded OpenCode event
stream and cleanup. Read-only analysis exposes only selected redacted files on a read-only
workspace mount, with a read-only mailbox, no MCP validation and only read/glob/grep/list tools.
The analysis container receives one provider key, no CI token, host home or Docker socket.
It retains B9's non-root, read-only rootfs, cgroup, capability and no-new-privileges restrictions.
Repository configuration/plugins/instructions, shell, web, auxiliary agents, LSP/formatters,
updates and sharing remain disabled. Serialized macro escapes preserve literal user text.

Fix mode allows exact existing non-executable production files under `src/`, `app/` or `lib/`
with reviewed source extensions. Directory prefixes, additions/deletions, tests/specs/fixtures,
configuration, pipelines, dependency/build/quality controls and executable files are refused.
The external collector checks every change and mode **before writing a patch artifact**;
existing tests and quality files remain byte-identical. The allowlist is deliberately narrow:
legitimate configuration/dependency/test repairs return no patch in v1. Production-code semantic
correctness is still reviewed by a maintainer; path controls cannot prove a repair is useful.

The model may request only the configured argument-free validation tool. The launcher freezes
the editor for capture and runs each maintainer-pinned command in the existing non-root,
networkless, credential-free validator. Images are locally prepared with `--pull=never`.
Final capture/validation repeats after editor termination. Validation logs are bounded and
redacted before retention/tool transport. Missing commands remain `not_run`; unavailable
images/dependencies/cleanup remain `unavailable`; executed checks explicitly pass or fail.
Generic repair validation never invents B11 generated-test counts. Cleanup failure retains
scratch for operator cleanup. No automatic commit, push, PR, publication or deployment exists.

## Reports, delivery and original failure

Completed reports require cause (`repository`, `external`, `unknown`), likely-cause summary,
explicit uncertainty, next steps and `failure-log` evidence bound to the retained excerpt's
SHA-256/size. No patch requires `noPatchReason`. External/unknown causes cannot carry a patch.
Patches use the existing complete diff/base/digest/manifest contract. Validation is independent
of the original CI outcome: even passing local validation always emits gate `fail` and nonzero
CLI status. Partial runner usage retains B9's disjoint categories, unknown HTTP request/retry
counts and `billingComplete=false`; costs remain B15. Completed diagnosis requires captured
runner evidence. Owner-scoped result reads expose both upstream status and agent outcome.

Result POST failure emits local `agentError.kind=result_delivery_failed` in archived JSON;
a claim outage/uncertain response emits `claim_unavailable` and makes no model call. Duplicate
invocations emit skipped local JSON and do not overwrite the first stored run. The CI artifact
is the error record when the API is unreachable. stdout/stderr and artifacts must be archived.

The owner-scoped `ci-command` API returns a digest-pinned trusted-launcher command and Jenkins
wrapper replacing exactly one selected stage. A maintainer supplies the original command in
`DRIFTPLAIN_UPSTREAM_COMMAND`, provider/CI credential bindings and fresh external inputs/scratch
paths. The wrapper captures the exact commit before running that command, invokes diagnosis
only on its nonzero exit, sets FAILURE before invocation, archives results in `finally`, and
raises the original failure regardless of diagnosis/delivery outcome. Cancellation propagates;
this is not a recursive global failure hook. Preparation errors occur before the diagnosable
command. Full visual setup remains B13/B14. No live Jenkins installation was performed.

## Verification versus activation

Pure, PostgreSQL/API/migration, fake CLI and actual offline Docker checks cover deduplication,
redaction, owner scope, read-only mounts, protected tests, repair validation states and failure
preservation. `tests/agent/check_diagnosis_profiles.py` inspects all six configurations using
actual pinned OpenCode with network disabled and synthetic credentials; it never generates.
`B12_CONTAINER_TESTS=1` enables only local offline boundary tests, not runtime activation.
Separately approved live evidence must name exact task/profile/options/image/model response,
date, fixture/prompt and bounded usage. All six integrations remain pending until that exists.
