# PR-review integrations — B8, September 24, 2026

**Six implementations, zero newly live-verified models. All six are pending and not
runnable or selectable.** HTTP fixtures establish adapter behavior only. No activation
rows, provider calls or production changes are included. Existing legacy Anthropic,
Gemini and Bedrock review paths retain their existing configuration and behavior.

| Exact provider model ID | Native protocol | Fixed reasoning profile | Status |
|---|---|---|---|
| `gpt-5.6-sol` | OpenAI Responses | `reasoning.effort=low` | pending |
| `gpt-5.6-terra` | OpenAI Responses | `reasoning.effort=low` | pending |
| `gpt-5.6-luna` | OpenAI Responses | `reasoning.effort=low` | pending |
| `claude-fable-5-1` | Anthropic Messages | adaptive / effort low | pending |
| `claude-sonnet-5` | Anthropic Messages | adaptive / effort low | pending |
| `gemini-3.7-flash` | Gemini GenerateContent | thinking level LOW | pending |

`app/review_contracts.py` is the pure profile/usage registry. Each exact runtime version
is `review-http-v1:<exact model ID>:low`. These versions pin protocol, prompt/output
contract and options; they are not model aliases or image versions. A change to any
of those requires a new profile version and matching verification. An agent image
commit/digest must additionally be recorded with the live evidence.

The explicit Gemini profile uses catalog provider `google` and credential variable
`GEMINI_API_KEY`. The legacy agent's separate `gemini` enum is unchanged. Provider usage
uses the exact provider name of the pinned configuration, including `google` here.

## Transport and opt-in execution

`MODELMATCH_EXECUTION_CONFIG=true` explicitly selects the B7 negotiated v2 config
endpoint. Complete API URL/project ID/CI token configuration is required; no fallback.
The caller still uses the review image. Task, mode, capabilities, bare model ID,
provider, credential variable and runtime version must match the reviewed profile.
Pending profiles fail before reading the diff or opening a provider connection.
Legacy/env selection cannot route these six through the older native SDK adapters.
There is no enablement environment variable or paid-smoke CLI switch.

The B8 consumer supports `ci_review`/`single_call` with a diff only. It rejects file
and artifact inputs; other executors belong to later slices. Project instructions
remain literal and review preferences use the existing bounded sanitizer. The server
authorizes no writes or tools. It pins the exact revision in every result and POST.

The new adapters use standard-library HTTPS directly. This makes the request count
explicit and avoids SDK retry defaults or endpoint environment overrides. Routes are
fixed to api.openai.com, api.anthropic.com and generativelanguage.googleapis.com; no
proxy, redirect, arbitrary endpoint, model substitution or compatible-provider catch-all.
DeepSeek/hosted copies are not part of the frozen review target and are not added.
Legacy SDK paths and the backend's Bedrock-only production surface remain separate.

Every run permits one generation request and zero transport retries. No repair loop,
tools, forced tool use, temperature or arbitrary provider options are sent. OpenAI
uses `store=false` and `truncation=disabled`. Anthropic uses version `2023-06-01`,
adaptive thinking and output_config.effort. Gemini uses one candidate and never minimal
thinking. Strict findings validation is local: only the findings object, bounded fields
and relative paths pass. Unlike the legacy reader, the new profile rejects code fences.

Input reads are byte bounded and reject oversize/invalid UTF-8, without silent truncation.
The UTF-8 byte count of the rendered prompts plus 4,096 protocol-reserve tokens is a
conservative preflight bound, **not a measured provider token count**. Input/context and
cumulative token limits must admit that bound plus requested output. Provider output
is limited to min(AGENT_MAX_TOKENS, 16,384); local ceilings can only tighten persisted
resource limits. Actual reported context/output/cumulative usage is checked after the
response, counting cache/thinking once. An overrun fails and cannot undo billed work.
Response bytes include thinking blocks and are capped by maxOutputBytes. A process
alarm enforces a wall deadline on Linux/macOS even during stalled input/HTTP reads;
there is no background request thread after timeout. Tests are main-thread CLI tests.

Refusal, truncation, context rejection, malformed output, timeout and other provider
errors never pass the gate. Response bodies, thinking text, prompts, credentials and
provider exception messages are not logged. Exact response model identity is required
for completion; a dated provider-returned revision needs explicit review, never a loose
prefix match. A timeout may have spent tokens even when no usage was returned.

## Reported usage contract

Optional `taskResult.providerUsage` version 1 is additive to B7 result version 1.
It is stored in the existing immutable task_result JSONB; there is no new migration,
historical backfill or billing calculation. B7 owner scoping and downgrade guards apply.
Backend ingestion binds provider/profile to the executed revision and checks the
legacy captured-count fields agree. Unknown fields, negative counts, incompatible
categories and contradictory totals are rejected.

| Provider | inputTokens | outputTokens | Cache and reasoning semantics |
|---|---|---|---|
| OpenAI | inclusive input | inclusive output | cache read/write are input subsets; reasoning is an output subset |
| Anthropic | uncached input | inclusive output | add cache read/write to input; thinking is an output subset; preserve reported 5m/1h writes |
| Gemini | inclusive prompt | response candidates | cached input is a subset; add thoughts to candidate output |

Null means unavailable and zero means reported zero. No cache/thinking estimates are
invented. `reportedTotalTokens` preserves the provider's total where present; consistency
is checked against known categories. `generationRequests` records 0 (local preflight) or
1 (transport attempted, including uncertain timeout); `transportRetries` is always 0.
Required categories missing for the cumulative ceiling prevent a passing review.

Existing top-level tokensIn/tokensOut remain the captured provider counters, with 0
when unavailable because that compatibility envelope requires integers. For B8, consumers
must inspect nullable providerUsage before interpreting those zeros; they are not proof
of zero consumption. cacheReadTokens remains nullable. The full usage envelope survives
refusal/malformed output and is posted with a failed gate when reporting is enabled.
Pre-dispatch configuration/input-read failures still exit without a CI run. No cost or
quality claim follows from these counters. B15 owns billing and dashboard interpretation.

## Separately approved smoke plan — NOT executed

Use [the synthetic diff](fixtures/b8-review-smoke.diff), the exact `agent.review.SYSTEM_PROMPT`
and `build_user_prompt(diff)`, no preferences/instructions. Record both rendered prompt
hashes, fixture hash and the tested agent commit/image digest before approval/execution.
Frozen SHA-256 values for this packet:

- Fixture (192 bytes): `5a74dad799d63f9ca863df74f9e57e3c94db59579240533b3aa13cd339a3c9e3`.
- System prompt (400 bytes): `7383d30dddd40ea965e0736d5a86c12d3e0aa064c73fd0f3ed4c07920d139f8c`.
- User prompt (219 bytes): `09afb95f76e3b0c27f9b705bb26c0afc94943a3e838739bf5c68cb18edec7563`.

For each of the six exact rows above, propose exactly one request on its fixed route:

- 1 generation, 0 retries, 60-second wall limit, 1,024 output tokens (thinking included
  in the provider cap), 8,192 cumulative tokens, 8,192 context tokens, 4,096 input bytes
  and 65,536 response bytes. Stop on any overrun; no automatic retry after failure.
- Maximum six requests, 6,144 requested output tokens across the set. The cumulative
  per-run ceiling totals 49,152 tokens. Before execution, review current provider prices
  and set an explicitly approved dollar budget/account controls; this document authorizes
  no spending. Only throwaway fixture source reaches Gemini.
- Capture dated exact request options, returned model ID, sanitized findings, finish
  status, usage (including unknowns), elapsed time, adapter/profile/image identity and
  fixture/prompt hashes. Do not save keys, headers, raw thinking or real code.
- Require a completed strict findings result identifying unsafe evaluation in the fixture,
  correctly failing the high/critical gate, plus complete cumulative usage. Mocks are not
  live evidence. Refusal/truncation/wrong identity/unknown usage leaves that row pending.
- Any approved one-off harness must be separately reviewed to make only these requests;
  production CLI gates are not weakened for the smoke. No activation is implied by a
  successful smoke. Reviewed exact evidence must precede a profile-status code change
  and trusted runtime row; operating on production still needs its own scope.

After verification these six initially qualify only as supported_unranked: none has
matching complete evidence in the accepted CodeReviewBench group. Six successful exact
task/profile verifications are required before claiming the coverage target complete.

## Official documentation checked September 24, 2026

- [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol),
  [Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra),
  [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) and
  [Responses](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).
- [Fable 5.1](https://platform.claude.com/docs/en/models/fable-5-1/overview),
  [Claude models](https://platform.claude.com/docs/en/models/overview),
  [thinking options](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost)
  and [Messages usage](https://platform.claude.com/docs/en/api/typescript/messages).
- [Gemini 3.7 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash) and
  [GenerateContent](https://ai.google.dev/api/generate-content).

These pages establish documented IDs/parameters, not account availability or live success.
