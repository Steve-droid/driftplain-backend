# Named test generation — B11, September 24, 2026

B11 adds Python/pytest and Node.js `node:test` execution on B10's shared disposable
workspace, editor, narrow validation mailbox and credential-free executor. It adds no
runtime rows, migration, provider calls or deployment. **All six integrations remain
pending and blocked** in both the public CLI and internal editor worker:

| Language/capability | Exact models on their direct providers | Runtime identity |
|---|---|---|
| Python / `test_generation_python` | OpenAI `gpt-5.6-sol`, Anthropic `claude-sonnet-5`, Google `gemini-3.7-flash` | `tests-python-oc1.18.20-v1:<model>:low` |
| Node / `test_generation_node` | The same three exact models, independently verified | `tests-node-oc1.18.20-v1:<model>:low` |

The acceptance target remains three canonical models over at least two provider families
for **each** language. Mocks, offline profiles and another task's live evidence do not
activate these integrations. OpenCode stays 1.18.20 with the existing binary/source/SDK
pins, provider endpoints and low-effort options in [security support](SECURITY-SUPPORT.md).
Python alone uses the accepted TestGenEval Extra policy. It is published Python
additional-test evidence under its original runner, not evidence that these OpenCode tests
find bugs. This runtime adds new files rather than editing existing tests; that restriction
is narrower than the benchmark. Node stays unranked. Catalog and runtime support remain
independent; unresolved aliases never grant execution.

## Configuration and dependency preparation

The B7 configuration/result versions and outer v2 negotiation stay unchanged. New optional
`taskConfiguration.testEnvironment` lives in the existing immutable JSONB:

```json
{
  "inputs": {"diff": true, "files": ["src/calculator.py", "tests/test_calculator.py"]},
  "instructions": "Cover negative inputs and boundary values.",
  "writePaths": ["tests"],
  "testEnvironment": {
    "profile": "pytest-v1",
    "dependencyLock": "requirements.lock",
    "dependencySha256": "<SHA-256 of the committed dependency lock>"
  },
  "validationCommands": [{
    "id": "suite",
    "argv": ["python", "-m", "pytest", "tests"],
    "environmentImage": "registry/maintainer-tests@sha256:<reviewed-image-digest>",
    "required": true,
    "maxSeconds": 120
  }]
}
```

Angle-bracket values above are explanatory, not valid configuration. Historical B7 revisions
without this environment remain readable, but cannot execute the B11 consumer or claim a
passing named-test result. Editing creates a new revision; no history is rewritten.

The first reviewed profiles deliberately accept **one required complete-suite command**,
with literal suite paths following either `python -m pytest` or `node --test`. No shell,
extra flags, test-name filters, package scripts, model-selected commands or installation.
The launcher wraps that command with its own read-only reporter. Python is pytest **9.0.3**;
Node is **22.x**, with its exact patch version/dependencies pinned by the image digest.
`pytest-v1` disables plugin autoload, repository `addopts` and cache writes, enables strict
markers, and imports pytest before adding source to the import path. Maintainers must check
that this explicit profile covers their intended full suite; third-party plugin-dependent
suites need a future reviewed profile. Node recursively selects `.test.js`, `.test.cjs` and
`.test.mjs` files under configured directories, or literal configured test files. Jest,
Vitest, TypeScript loaders and other runners require their own reviewed reporter adapters;
they are not silently counted as named support. Other remains available for custom runners.

Prepare images **separately**, on a credential-free build worker, then review/publish/pull
and configure the resulting immutable digest. Validation always uses `--pull=never` and
`--network=none`. Python images install a hash-locked dependency closure (including pytest)
and retain its exact bytes at `/opt/driftplain-tests/requirements.lock`. The repository lock
path may differ, but its hash must match both configuration and the image copy. Node images
retain `/opt/driftplain-tests/package-lock.json`, use `npm ci` in the controlled preparation
phase and place preinstalled dependencies at `/node_modules` for source under `/workspace`.
A built-in-only Node suite needs no third-party packages. Do not copy a host dependency tree
or pass package registry/provider/CI credentials into validation. No services, Docker socket,
network installs or writable source are supported by these profiles.

[Offline fixture Dockerfiles](../tests/agent/fixtures/test_generation/) demonstrate pinned
Python/Node environments with a complete dependency lock. They are test fixtures, not a
production project environment or newly published validation-image product. Publishing the
agents does not install or enable any validation environment on a CI node.

## Execution and evidence

The named system prompt is trusted code; optional instructions, supplied diff, selected files
and named artifacts are literal bounded text. The user cannot replace the system prompt.
Only new non-executable UTF-8 test files under configured `test`, `tests` or `__tests__`
directories are permitted. Python uses `test_*.py` / `*_test.py`; Node uses the suffixes above.
Every existing blob/mode is immutable. Production edits, modified/deleted tests, added runner
configuration, hooks, locks, links and special files fail before validation. Ordinary Python
collection/skip hooks are also rejected statically; reporter checks reject skipped, xfailed,
todo, unexecuted, duplicate, missing or mismatched identities for either language.

The launcher exports the exact committed base and makes a separate baseline copy **outside
the editor mount**. Each validation request runs the configured suite on that base and on the
current candidate in separate containers, sharing one command wall/output budget. Both require
positive, completely executed tests; this first profile conservatively rejects skipped tests
in the existing suite too. The original checkout, including dirty/untracked files, is untouched.
Final validation always reruns after the editor exits, so an intermediate green result cannot
validate later edits. Base/patch/revision/command attribution uses B10's existing contracts.

Trusted reporter source is copied from the launcher to a read-only mount outside source;
it is never loaded from the model's repository. Pytest records collection plus call/setup/
teardown outcomes. Node's reporter consumes runner events outside test child processes;
file-level success without an actual named test is excluded. Test stdout is diagnostic data,
not a report. The external parser requires a single bounded record, unique path-qualified
identities, preservation of the entire baseline test set and executed tests in **every**
generated file. A zero exit alone is insufficient.

A passing check includes `testEvidence`: reviewed profile, lock hash, exact generated paths,
existing discovered/executed counts and matching SHA-256 hashes of baseline/final existing
identities. `generatedTestsDiscovered` and `generatedTestsExecuted` must be positive and equal.
The API validates these against the immutable executed configuration, patch paths, revision,
command, log artifact and final patch hash; no model-authored result can change the command.
Full bounded baseline/candidate reports and diagnostics are retained in the validation-log
artifact; the API stores only metadata. Missing evidence is unknown, not zero or passed.
Test assertion failure is `failed`; missing dependencies/reporting, skip/zero discovery,
timeout or executor problems are `unavailable`. Both fail CI. Syntax/policy/model errors can
fail execution before validation. Partial output on executor failure remains a bounded log;
unconfirmed container cleanup retains job scratch for operator recovery.

This is reported execution evidence, **not cryptographic attestation of arbitrary test code**.
Like ordinary CI, the maintainer-owned runtime and test harness are trusted; Python tests
share a process with pytest hooks and can execute arbitrary Python inside the credential-free
container. Structural/report checks catch contract mismatches and ordinary bypasses, but do
not prove useful assertions or correctness against an adversarial program. No generated
patch is automatically committed, pushed, made into a PR, published or deployed.

OpenCode `runnerUsage` v1 now applies to named tests without changing token semantics:
normalized disjoint categories, unknown transport/request counts, billingComplete=false and
partial captured usage on failure. No native/runner usage mixing and no invented costs.

## CI setup, checks and future activation

Owner-scoped `/execution/v1/projects/{id}/ci-command` accepts named tests and returns the
trusted launcher command plus `jenkinsStage`. The stage retains the agent's nonzero exit and
archives `result/**` and `result.json` from job-private external `DRIFTPLAIN_SCRATCH` even on
failure. Supply the selected provider credential, existing project CI token, explicit input
directory and same-absolute-path scratch mount as in [Other support](OTHER-SUPPORT.md).
`AGENT_OTHER_IMAGE` remains the compatibility variable for the shared editor image. Only
the launcher holds the engine socket. B13/B14 still own full setup/picker/preview UX.

Offline checks: `tests/agent/test_test_generation.py`, `test_test_generation_containers.py`,
`check_test_profiles.py`, and `tests/test_test_generation_api.py`. Container tests opt in with
`B11_CONTAINER_TESTS=1`; build the two fixture images using their Dockerfiles first. The
fixture executor substitutes local image IDs solely inside test code; production has no
pending-activation, fake-runner or arbitrary-executable override. Pinned `debug config`,
`models`, tool permissions and MCP checks run network-disabled with synthetic keys for all
six profiles, without a generation call. B10's full containment tests remain regression checks.

A separately approved capped smoke must cover each exact language/model/image/prompt/options
combination, record actual returned identity and usage, and bound provider spending including
OpenCode's hidden retry layer. This document authorizes no paid run, activation, production
migration or deployment. Current fake tests are implementation evidence only.
