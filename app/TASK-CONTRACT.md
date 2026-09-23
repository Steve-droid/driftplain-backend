# Task/configuration/result contracts — B7, September 24, 2026

`app/task_contracts.py` is the shared, dependency-light source of truth for the backend
and both agent images. This release defines contracts and persists metadata. It does **not**
execute new tasks, activate providers, run validation commands or claim containment is proven.
The execution runtime table still starts empty; catalog evidence and mock fixtures cannot
enable support. The old review/security CLI, output, exit codes and config endpoint remain.

## Registry and versions

Authenticated `GET /execution/v1/tasks` returns registry `version: 1` and these profiles.
It is vocabulary, **not a list of enabled runtimes**; use `/execution/v1/candidates` for eligibility.

| Task | Mode | Capability | Result kinds |
|---|---|---|---|
| `ci_review` | `single_call` | `ci_review` | findings |
| `security_analysis` | `opencode` | `security_analysis` | findings |
| `test_generation` (Python/Node) | `opencode` | `test_generation_python` / `test_generation_node` | patch |
| `ci_failure_diagnosis` | `opencode` | `diagnosis_readonly` | report |
| Diagnosis with `proposeFix: true` | `opencode` | `diagnosis_fix` | report or patch |
| `other` | `single_call` | `custom_single_call` | report |
| `other` | `opencode` | `custom_opencode` | report or patch |

`propose_fix` is not a sixth task. Only named test generation accepts `language`, exactly
`python` or `node`. Old `review` / `security` dispatch names stay in the legacy facade;
new envelopes use canonical task IDs. Legacy recommendation/project creation stays two-task.
Benchmark selection policy values and B6 evidence grouping do not change.

## Immutable configuration

Explicit project create/PATCH accepts additive `taskConfiguration` with strict nested objects:

```json
{
  "label": "Release notes",
  "systemPrompt": "Explain changes clearly.",
  "instructions": "Summarize the supplied diff for maintainers.",
  "inputs": {"diff": true, "files": ["README.md"], "artifacts": ["build-log"]}
}
```

Other requires a label (100 characters), system prompt (8,000) and instructions (16,000).
Named tasks do not accept a custom system prompt/label. Text is literal data in B7: **no
template or shell expansion**. Future documented CI-field interpolation requires its own
bounded implementation. Prompts cannot supply capabilities, URLs, executables or resource
policies. Configuration may include maintainer-controlled validation commands as described below.

Inputs specify a diff, up to 100 literal repository-relative file paths and 20 named artifacts.
Absolute paths, drive/UNC names, traversal, control characters and globs are rejected; the
initial path alphabet is ASCII letters/digits, spaces and `_. /@+()-`. A file path does not
authorize recursively uploading its directory. Default total/file/context limits are
262,144 bytes / 65,536 bytes / 32,768 tokens; ceilings are 1 MiB / 256 KiB / 131,072 tokens.
The future executor must enforce actual bytes, symlinks and containment, reject oversize
inputs explicitly and keep full inputs in CI. Schema validation alone cannot prove these.

`resources` stores the effective wall time, cumulative tokens, iterations/attempts, process,
CPU, memory and output ceilings. Defaults: 600 seconds, 100,000 tokens, 20 iterations, one
attempt, 64 processes, 2 CPUs, 2 GiB memory, 1 MiB output. Hard maxima: 1,800 seconds,
1,000,000 tokens, 40 iterations, three attempts, 128 processes, 4 CPUs, 4 GiB, 4 MiB.
Single-call configuration always records one iteration and one generation attempt. No retry
loop is introduced; later adapters must account for any transport retries explicitly.

The server derives and persists `capabilityPolicy` from the profile, separately from prompts.
Read-only profiles cannot configure writes or validation execution. Writable profiles require
up to 30 explicit `writePaths` (literal files or directory prefixes); they use a disposable
workspace. `shell: false`, `network: false`, `hostAccess: false`, `publish: false` describe
repository/tool/validation authority: no arbitrary model shell, validation network, host
access or publishing. Provider transport is a separate trusted runtime route. Writable
containment and named-task restrictions must be enforced outside the model in B10–B12.

`validationCommands` contains up to five unique IDs, argument vectors (no shell interpolation),
digest-pinned `environmentImage`, `required` (default true) and `maxSeconds` (default 120,
maximum 600). These are authenticated maintainer configuration, never generated instructions.
The future credential-free executor runs them without provider/CI credentials or host mounts;
dependencies must be in the pinned image. Named test generation needs required validation.
Other/fix may omit it; resulting changes remain explicitly unverified.

Every edit creates an immutable revision. Same-profile model re-picks and preference-only
edits retain task configuration; a profile change validates a fresh configuration. PATCH of
`taskConfiguration` replaces the whole object (omitted inner fields use documented defaults),
not a recursive merge. Omitting the field preserves it; explicit null resets defaults and
therefore fails for Other/writable profiles that require configuration. Historical B6 revisions
are never backfilled with invented policies. Owner/project scoping and evidence holds remain.

## Agent negotiation

The outer execution config stays `contractVersion: 2`. New revisions add
`taskContractVersion: 1`, `resultContractVersion: 1`, `resultKinds`, `taskConfiguration` and
`capabilityPolicy`. `agent.remote.fetch_execution_config` explicitly requests
`GET /execution/v1/projects/{id}/agent-config?taskContractVersion=1` using the project CI token.
It rejects unsupported versions, inconsistent profile/authority, unknown config/model fields
and identity mismatch. It never falls back to a legacy endpoint or another provider.

New tasks require this negotiation (409 without it). Review/security retain B6 reads without
the parameter; requesting version 1 for an old B6 revision returns 409. Edit the configuration
to create a new revision when adopting B7. The current CLI still calls the legacy config
reader, so it cannot accidentally execute custom tasks. B8/B9/B10 wire verified consumers.

## Run metadata and validation

`POST /projects/{id}/ci-runs` retains the existing usage/model/gate/build fields and accepts
an optional `taskResult` (`version: 1`). It requires `executionRevisionId`, exact task/mode/
language/fix matching, and the B6 exact provider-model identity. The result kinds are validated
against the registry. `taskResult` is required for new tasks; old review/security payloads
remain accepted with null metadata, including against historical revisions.

Report/patch results contain no findings/CWEs. A completed result needs a bounded report
summary (8,000 characters), optional uncertainty/next steps and artifact evidence IDs.
The manifest has at most 30 unique IDs with literal relative artifact paths, kinds, SHA-256
digests and byte counts; its total size cannot exceed the configured output ceiling.
Paths identify CI-retained artifacts; the backend does not fetch URLs or store raw logs,
patch contents, source files or whole workspaces.

A completed patch result also needs the full Git `baseCommit` (40/64 hex), patch SHA-256,
matching manifest artifact and up to 100 changed-file paths with added/modified/deleted
operations. Paths must fall within configured write paths. A single-call report can reference
`proposed_code` artifacts, but cannot claim it applied or validated a patch. A fix with no
patch uses a report explaining why. Artifact metadata is reported evidence, not proof that
the agent ran or that the patch is safe; executor checks remain necessary.

`executionStatus` is explicit: completed, failed, timed_out, refused or skipped.
Incomplete execution requires an explicit bounded `executionReason`.
`validationStatus` is separate: passed, failed, not_run or unavailable. Omitted validation
means **not_run**. Each check references a configured `commandId`, executed revision ID,
base commit and final patch SHA-256, plus status, exit code, duration and log artifact.
The command/environment itself is read from that immutable revision. Executed checks need
an exit status and log; passed requires exit zero. Unexecuted checks need a reason and cannot
claim an exit code. Stale patch/config IDs, unknown commands, contradictory summary statuses
and “passed” without checks are rejected. A passed summary accounts for every configured command.

The CI gate stays separate. Execution failure or failed/unavailable validation cannot pass.
Each required command must pass before a passing gate is accepted. Named test generation also
requires a patch and positive `generatedTestsDiscovered` and `generatedTestsExecuted` counts.
Diagnosis/fix cannot turn the original failed build green, even when local validation passes.
Other without configured validation may finish with a passing execution gate but validation
is still not_run: this makes no semantic-quality or test-success claim. Legacy CI gates retain
their existing behavior. Billing remains B15; explicit cost/baseline/savings stay null.

Metadata persists in nullable `ci_run.task_result`; existing findings/feedback APIs are unchanged.
Owner JWT `GET /execution/v1/projects/{projectId}/runs/{runId}/result` reads the result with
both owner and project checks. It returns null for legacy metadata. Old executed revisions
remain reportable after a re-pick or runtime disable.

## Migration and delivery

Migration `e8f9a0b1c2d3` follows B6 `d7e8f9a0b1c2`. It adds nullable JSONB plus a revision
constraint and update-protection trigger. No history is rewritten. SQL NULL (not JSON null)
represents absent legacy metadata. Existing costs, findings, feedback and token hashes survive.
Empty B7 additions can downgrade; B7 configuration/result history makes downgrade abort before
DDL. Normal owner-requested project deletion retains existing cascade behavior.

Backend `1.5.0 -> 1.6.0`: minor for additive configuration/result APIs. Agents `1.1.3 -> 1.2.0`:
minor for the shared contract reader/serializer capability; current dispatch and CI gates remain.
Publication does not authorize production migration, deployment or live provider verification.
