# Security integrations — B9, September 24, 2026

**Six pending integrations, zero newly live-verified models.** All six remain unselectable
and blocked by the CLI. No trusted runtime rows are seeded. Mocks and offline model
registration are not paid verification. Existing legacy security configurations retain
submission shape, retries, prompt and gates.

| Exact bare ID | OpenCode route | Pinned options | Status |
|---|---|---|---|
| `gpt-5.6-sol` | `openai/gpt-5.6-sol` | reasoningEffort low, store false | pending |
| `gpt-5.6-terra` | `openai/gpt-5.6-terra` | reasoningEffort low, store false | pending |
| `gpt-5.6-luna` | `openai/gpt-5.6-luna` | reasoningEffort low, store false | pending |
| `claude-fable-5-1` | `anthropic/claude-fable-5-1` | adaptive thinking, effort low | pending |
| `claude-sonnet-5` | `anthropic/claude-sonnet-5` | adaptive thinking, effort low | pending |
| `gemini-3.7-flash` | `google/gemini-3.7-flash` | thinkingLevel low, includeThoughts true | pending |

Every runtime version is `security-oc1.18.20-v1:<bare ID>:low`. These are task/profile
identities, not image versions. Verification additionally records the exact image digest,
agent commit, model/response identity and dated fixture/prompt/settings evidence. B8 review
verification does not grant security support. After verification these candidates start as
`supported_unranked`; no matching complete RealVuln recommendation group is available.

## Pinned implementation and offline evidence

OpenCode remains **1.18.20**, source `7248bc1964b13fa67e601733f89ee9dc6dfa0563`.
Linux x64 archive SHA-256: `8603214aa1e2e18f9312360c9360dfae6174f5cff6dc4f72b6045c80608af4d4`.
Extracted binary SHA-256: `5dce99ea079d925736e332b20f5bf869fe9a1fa67dc0a09027156b0ed8e41b16`.
The explicit runner checks that binary digest; there is no arbitrary executable override.
Bundled SDK versions are OpenAI 3.0.84, Anthropic 3.0.82 and Google 3.0.73.

Inspected exact upstream source (September 24, 2026):

- [Provider configuration and model registration](https://github.com/sst/opencode/blob/7248bc1964b13fa67e601733f89ee9dc6dfa0563/packages/opencode/src/provider/provider.ts)
  supports config-defined bare model IDs with bundled native SDKs. OpenAI uses Responses.
- [Request option merge](https://github.com/sst/opencode/blob/7248bc1964b13fa67e601733f89ee9dc6dfa0563/packages/opencode/src/session/llm/request.ts)
  applies model options after runner defaults; configured output tokens are capped.
- [Normalized usage](https://github.com/sst/opencode/blob/7248bc1964b13fa67e601733f89ee9dc6dfa0563/packages/opencode/src/session/session.ts)
  subtracts cache from input and reasoning from output, and fills missing counters with zero.
- [Internal retries](https://github.com/sst/opencode/blob/7248bc1964b13fa67e601733f89ee9dc6dfa0563/packages/opencode/src/session/retry.ts)
  use a maximum of five retries. The CLI JSON stream does not expose retry status or
  provider-returned model identity. SDK retries are separately set to zero; this does not
  remove the session retry layer.

`tests/agent/check_security_profiles.py` runs `debug config` and `models` in the actual
pinned container with `--network none`, synthetic credentials and hostile repository config.
All six exact routes must be the sole registered model for their enabled provider. It makes
no generation request. This proves schema/configuration compatibility only, not that the
bundled SDK accepts every live response or emits every desired wire option. Before activation,
verify actual wire parameters and exact provider identity against the approved profile; no
silent runner upgrade, host substitution or model alias is permitted.

## Explicit consumer and isolation

`MODELMATCH_EXECUTION_CONFIG=true` dispatches by the validated task. Review keeps its B8
consumer; `security_analysis`/`opencode` uses the security image. The v2 endpoint is explicitly
negotiated, with no legacy fallback. API URL/project ID/CI token are mandatory. Provider,
credential variable, task, capability and runtime version must match exactly. Pending profiles
fail before input/provider work. Legacy/env routing cannot bypass pending status for these IDs.

Security accepts a whole bounded read-only checkout: set `taskConfiguration.inputs.diff=false`,
with no selected files/artifacts and no `--diff`. The entire checkout must fit maxBytes and each
file maxFileBytes, with at most 1,000 files. Symlinks and special files fail. Use a small exported
source tree; dependencies and `.git` are not silently exempted from these bounds. Project
instructions remain literal; system prompt and tool policy are trusted code. Custom prompt-file
and executable env overrides apply only to legacy mode.

The explicit preflight requires Linux cgroup v2, non-root, a read-only workspace, zero effective
capabilities, no-new-privileges and CPU/memory/PID ceilings no larger than the immutable
configuration. For default limits the CI launch needs `--cpus 2 --memory 2g --pids-limit 64`,
`--cap-drop ALL --security-opt no-new-privileges`, a `:ro` checkout, bounded temporary storage,
and no host home, additional host mounts or container-engine socket. The launcher is trusted;
process inspection does not certify arbitrary external mounts. This is not a validation executor.

The child gets a fresh disposable home/config/session directory and an environment allowlist:
only the selected provider credential, controlled paths and pinned OpenCode settings. No CI token,
other provider keys, proxy or endpoint overrides are inherited. Gemini's exact `GEMINI_API_KEY`
is mapped to the runner's `GOOGLE_GENERATIVE_AI_API_KEY` without accepting a conflicting key.
Provider endpoints and bundled SDKs are fixed. Model metadata fetching, repository config and
instructions, external/default plugins/skills, LSP, formatters, sharing, compaction and automatic
updates are disabled. A fixed title and disabled title agent avoid auxiliary title generations.
Only read/glob/grep/list tools are allowed; shell, edits, web, tasks, external directories and
validation are denied. OpenCode permissions are defense in depth, not an OS sandbox. Its
provider transport still has network access and its own provider credential.

## Ceilings, results and usage

The explicit profile runs **one generation attempt** (one OpenCode session), with no outer
refusal/output retries. That session can perform multiple model steps and internal transport
retries. Never describe an attempt as one HTTP request. Local time/token/step/output limits
only tighten persisted limits. maxIterations also bounds observed tool calls. Output per step
is min(AGENT_MAX_TOKENS, 16,384, cumulative limit), reasoning included. Input/context/cumulative
usage, steps and tools are checked as events arrive. These abort further work; they cannot
undo provider work already performed or enforce an exact dollar ceiling. A separately approved
paid run requires account/spend controls covering internal retries and unreported work.

The nonblocking reader drains stdout/stderr concurrently, bounds their combined bytes, rejects
malformed events, and enforces a monotonic deadline even on silence or pipe flooding. It kills
the entire child process group, including descendants after the leader exits. Event IDs prevent
double-counting repeated completed events; conflicting repeats fail. Only the last step's text
is interpreted as output. Error events, abnormal exits, incomplete usage, unfinished/truncated
steps, denied tools, refusal and malformed findings cannot pass. Stderr and reasoning content
are discarded, never logged. Captured counters survive failed/timed-out runs where available.

Output is a strict Semgrep-shaped JSON object with results, mapped to bounded findings/CWEs.
Unknown severities/confidences, invalid or absent checkout paths, malformed rows and absent CWEs
fail the whole result; they cannot be dropped to produce a clean scan. Critical findings always
fail, even if local severity configuration tries to remove that gate. Validation remains not_run.
The output and POST contain the **bare** model ID and exact executed revision, not OpenCode's
prefixed route. Legacy exit codes remain 0/1/2/3/4/124.

Optional `taskResult.runnerUsage` **version 1**, separate from B8 `providerUsage`, is persisted in
existing immutable JSONB without a migration. It records disjoint normalized input, output,
cache-read, cache-write and reasoning counts, runner total, attempts, completed steps and tools.
Missing event values remain null; captured partial sums may survive, but an incomplete stream's
total is null. `generationRequests` and `transportRetries` are always null because the CLI does
not expose them. `billingComplete` is always false, even for a complete normalized event stream:
the upstream zero-fill has already lost raw availability information. These are not raw
provider billing counts. B15 must not treat them as a complete invoice or zero-cost proof.

The cumulative ceiling includes all five disjoint categories and compares the reported total.
Top-level compatibility tokensIn/tokensOut hold captured normalized input/output (zero if no
counter captured), with nullable cacheReadTokens. API ingestion requires these to match, binds
provider/profile to the executed revision and validates completed-event evidence and limits.
Native and runner usage cannot coexist. Old revisions remain reportable after edits or runtime
disabling. No new costs, migration, runtime activation or production operation is included.

## Separately approved tiny smoke plan — not executed

Use only `fixtures/b9-security-smoke/app.py`, a synthetic SQL-injection fixture; never run its
code. Use the exact bundled auditor system prompt and `build_task` scaffold, no added instructions.
Record fixture, system/task hashes, effective generated configuration and agent/image/binary
identities in the approval packet. Frozen fixture/prompt SHA-256 values:

- Fixture, 205 bytes: `76ac1c79d7b68139c533a12ee23142f967b348c5273f7f5fc4c992111014a813`.
- System, 4,568 bytes: `3481f1432c231f0a080f860da775f929eb3b4f346b38d98bd91487952049ab25`.
- Task, 5,093 bytes: `a33bfe444ce2bcf83adaafe053972a106529bf4d434903507b050e581a939b48`.

For each of the six exact profiles propose:

- One session/attempt, at most four observed model steps and four observed tool calls, 60 seconds,
  1,024 output tokens per step, 32,768 cumulative normalized tokens, 16,384 context tokens,
  4,096 checkout bytes and 262,144 combined output/event/stderr bytes. No outer retry.
- Six sessions maximum; nominal 24 model steps. Upstream's five internal retries per request
  mean this is **not** a six-request or 24-request budget. No actual request/transport count
  may be inferred from the number of completed steps. Review current prices and an explicit
  aggregate spend ceiling/account controls before approving a harness.
- Capture the actual request parameters and returned exact model identity through a separately
  reviewed observation harness; the normal CLI omits identity/retry evidence. Do not log keys,
  raw reasoning or real code. Require a dated security finding for app.py with CWE-89, failing
  critical gate, normal stop, complete event categories and any raw provider usage available.
- Missing evidence, refusal, wrong identity, incompatible options, incomplete events or any
  overrun leaves the row pending. No model replacement or incidental runner upgrade. No
  production CLI bypass flag is supplied. Verification does not itself activate runtime rows.

Six successful exact approved live verifications are still required before calling coverage
complete. This document authorizes neither paid calls nor activation/deployment.
