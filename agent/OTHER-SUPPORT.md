# Other custom runtimes — B10, September 24, 2026

Three exact models, each with independent single-call and OpenCode profiles, remain
**pending**: `gpt-5.6-sol` (OpenAI), `claude-sonnet-5` (Anthropic), `gemini-3.7-flash`
(Google). Six pending profiles are not six runnable integrations. No live calls, runtime
rows, activation or deployment are included. The CLI and internal worker both reject
pending profiles. There is no environment flag to override this gate.

Profile identities are `other-http-v1:<model>:low` and
`other-oc1.18.20-v1:<model>:low`. Exact native provider options/routes reuse the B8 transport;
OpenCode keeps the B9 binary/source/SDK pins. Review or security verification does not
verify custom tasks. Separate dated task/mode/image/prompt/settings and returned-model
live evidence is required before a reviewed activation change.

## Single call

Negotiated `MODELMATCH_EXECUTION_CONFIG=true` dispatches Other using the normal agent image.
The system prompt and instructions are literal UTF-8 text. **No interpolation, including
`${BUILD_TAG}`, is performed.** OpenCode config macro markers (`{env:...}` / `{file:...}`)
are escaped in serialized JSON before the pinned runner parses it; prompt text remains literal. Selected files resolve under `AGENT_WORKSPACE`, named
artifacts under `AGENT_INPUT_ARTIFACTS`; the optional diff comes from `--diff` or stdin.
Directories, path traversal, links (including linked ancestors/hardlinks), special files,
invalid UTF-8, missing inputs and byte/context limits fail explicitly. No implicit workspace
upload. One generation request, zero transport retries, no tools, output execution or repair
loop. The strict report accepts summary, optional uncertainty and nextSteps; code may appear
as unexecuted report text. Findings/CWEs and model-authored execution commands are rejected.

Native `providerUsage` version 1 now also applies to Other/single_call, with unchanged B8
provider-specific cache/reasoning semantics. Incomplete usage/refusal/malformed output and
wall/time/token failures cannot pass. The backend binds usage to the exact executed revision.

## Trusted launcher and writable editor

The OpenCode mode is a **three-party launch**: trusted orchestrator, editor container and
validation container. The orchestrator runs `python -m agent` from the normal agent image
(which contains Git and a Docker client). Only the orchestrator has CI credentials and
container-engine authority. Its code/image/configuration must be trusted; never run the
orchestrator from repository-provided Python modules, shell scripts or an unreviewed image.

The Linux Docker node must supply a read-only original checkout, exact `AGENT_BASE_COMMIT`,
a fresh `AGENT_OUTPUT_DIR` outside that checkout, and digest-pinned `AGENT_OTHER_IMAGE`
(the OpenCode-bearing security image, using the separate `agent.other_profile` entry point).
`TMPDIR` must be a job-private scratch mount shared at the **same absolute host/container
path** when the orchestrator itself is containerized. Local Docker Desktop Linux containers
are suitable for offline verification; macOS/Windows processes are not validation executors.
Images/dependencies must already exist locally (`--pull=never`). No automatic installation.

The launcher exports exact committed blobs without checkout hooks, filters, archive
substitution or uncommitted source changes. Links, submodules, special paths and oversized
bases are rejected. Git metadata stays outside the editor. Only a disposable bounded copy
is writable; no automatic commit/push/PR/publication/deployment. External patch capture
checks allowed paths, counts, bytes and file modes; trusted index attributes disable
repository-driven content normalization. Added/deleted files and binary Git patches are
preserved with base commit and SHA-256. The original checkout is never edited.

The editor gets only that copy, a read-only job description, a private validation mailbox,
bounded tmpfs and its **selected provider key**. It gets no CI token, engine socket, host
home/cloud configuration or other mounts. Non-root, read-only rootfs, dropped capabilities,
no-new-privileges and cgroup CPU/memory/PID ceilings apply. Its provider network is intentional.
Pinned OpenCode disables project config/instructions, plugins, LSP/formatters, auxiliary
agents, sharing and updates. Custom prompt/parser are independent of review/security.
Read/edit/write/apply_patch tools operate inside the copy; Bash, web, tasks and external
paths are denied. Permissions are policy checks, not a claim that the provider process
itself is credential-free. Existing security remains read-only.

## Narrow validation and final evidence

One stdio MCP tool, `validation_validate`, accepts **no arguments** and requests the immutable
maintainer-configured checks. The fixed bounded job mailbox contains request nonce/version
and a bounded response; it is not an engine socket or a command API. Unsupported arguments,
links, malformed requests and request-count overruns fail. The launcher pauses the entire
editor container while inspecting/validating its current patch, so edits cannot race the
check. Model output/repository configuration cannot select executable, image, environment,
mount, network or resource policy. Bounded log excerpts are untrusted diagnostic data.

Each validation container has a read-only view of the exact candidate, a writable 64 MiB
`/tmp`, **network none**, independent PID namespace, non-root UID, dropped capabilities,
no-new-privileges and configured resource ceilings. It runs the configured argv through
`env -i` with only fixed PATH/HOME/TMPDIR and Python bytecode policy. It has no provider key,
CI token, host home, cloud configuration or engine socket. Image-provided dependencies must
be preinstalled in a maintainer-reviewed digest; privileged/service/networked environments
are unavailable. Commands needing writable caches must use `/tmp`. Tests cannot edit the
candidate. Named Python/Node discovery semantics belong to B11; B10 records command exits,
not invented counts of discovered/executed tests.

After editor termination, the launcher captures the **final** patch and runs all configured
checks again. Intermediate passing checks never validate later edits. Every check references
the revision, base and final patch digest. Required failed/unavailable checks fail the gate;
no commands means `not_run`, never tested/passed validation. A command with exit 0 establishes
command success only. Missing executables/images, timeout/output overflow and executor errors
are unavailable. Artifacts are written outside the disposable copy before its cleanup.
Each process reader bounds combined stdout/stderr and kills the process group; the launcher
also forcibly removes each job container. Unconfirmed editor cleanup fails and retains its
scratch directory for operator recovery instead of deleting a still-mounted workspace.

`runnerUsage` version 1 extends to Other/OpenCode without changing B9's disjoint normalized
semantics. Attempts, completed steps and tool calls are distinct. HTTP request/retry counts
stay null; billingComplete stays false. One session can retry internally; it is never reported
as one API request. Partial captured usage survives failures where available; an incomplete
stream has unknown total. Stream ceilings stop future work; they cannot undo billed work.

## Setup and verification

Owner-scoped `GET /execution/v1/projects/{id}/ci-command` is the B10 setup seam, using the
current immutable configuration and enabled runtime check. It renders the correct launcher
and editor entry point from operator-pinned image settings; tag-only settings return 409.
Bind API/project/build/token and selected provider credentials in Jenkins by environment
name. Prepare the explicit diff/named artifacts in `DRIFTPLAIN_INPUTS`, and a fresh external
scratch directory for OpenCode. Archive its result directory. B13/B14 own the full Jenkins
stage, preview and model-picker UX. A mode change requires a new valid selection/revision.

Offline evidence lives in `tests/agent/test_other_*`, `check_other_profiles.py` and
`tests/test_other_usage_api.py`. Container tests opt in with `B10_CONTAINER_TESTS=1`; they use
synthetic credentials, network-disabled fake editor work and real independent validation.
Pinned-binary `debug config`, `models`, MCP handshake and `debug agent --tool` checks exercise
write/add/delete and denials without generation. They do not establish live model behavior.
See the pinned upstream [tool debug runner](https://github.com/sst/opencode/blob/7248bc1964b13fa67e601733f89ee9dc6dfa0563/packages/opencode/src/cli/cmd/debug/agent.handler.ts)
and [external-directory policy](https://github.com/sst/opencode/blob/7248bc1964b13fa67e601733f89ee9dc6dfa0563/packages/opencode/src/tool/external-directory.ts).

Any future approved smoke must separately bound both modes for all three exact models,
record prompt/input/output/image/binary hashes and actual returned identity/options, and cap
account spending including hidden OpenCode retries. Use only throwaway fixtures, one native
request or one bounded OpenCode session per profile, no automatic repairs/retries outside the
pinned runner. No smoke, activation or operational change is authorized by this document.

B14 publishes the custom authoring UI and extends the backend setup seam with an additive
`jenkinsStage`. It captures single-call `result.json`, or OpenCode `result/**` plus JSON,
and archives from external scratch even when the command fails. Single-call Docker flags
now also enforce the persisted CPU/memory/process ceilings. Templates remain editable
literal prompts; they cannot grant capabilities. No agent change or activation is included.
