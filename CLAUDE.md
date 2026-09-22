# CLAUDE.md — driftplain-backend

## Release versioning (September 22, 2026)

Follow [SemVer 2.0.0](https://semver.org/) and the
[release policy](https://github.com/Steve-droid/driftplain/blob/main/RELEASE-POLICY.md).
These rules replace older per-slice tagging rules and fixed next-version suggestions.

- **PATCH:** compatible bug fixes or dependency/security/packaging fixes needing a new artifact.
- **MINOR:** new backward-compatible functionality or deprecation with continued compatibility.
- **MAJOR:** a breaking supported API, CLI, configuration, user workflow or operational upgrade contract.
- **No release:** documentation, comments, tests or internal tooling/refactoring alone, unless a
  changed distributable is needed. Compatible internal/build changes that require an image get a patch.
- Evaluate all relevant changes since the last release of that component. Use the highest bump;
  reset patch for a minor, and minor/patch for a major. Commit prefixes and task numbers do not
  choose the version. Record `previous -> next`, category and compatibility reason in the PR or release.
- Fetch fresh tags and check published versions before choosing a number. Backend, frontend,
  agents, infrastructure and GitOps have independent sequences. Both agents share one
  `agent-vX.Y.Z` sequence; other component repos use `vX.Y.Z`. Keep existing 1.x sequences.
- A completed task does not automatically need a tag. For an intentional release, tag the
  reviewed main commit and create a GitHub Release even for patch/minor versions. Check for
  concurrent releases before tagging. Publication and deployment are separate actions.
- Never move, delete or overwrite a published tag/image to fix an incorrect bump. Backend
  `1.1.1` remains published; its added public APIs warranted a minor. The next backend release
  must be at least `1.2.0`, adjusted for any newer releases or breaking changes.
- Continue versions across the image rename. Preserve old packages, current deployment pins
  and all operational approval requirements. This policy itself requires no release tag.

Backend compatibility covers HTTP schemas, auth, CI ingestion and runtime configuration.
Agent compatibility covers CLI/env inputs, JSON output, exit codes and CI gate behavior.
New endpoints/tasks/providers are minor when compatible; removed or incompatible contracts are major.
Assess shared-code changes separately for the backend and agent release lines.

## Application image names (September 22, 2026)

New releases use `ghcr.io/steve-droid/driftplain-backend`, `driftplain-frontend`,
`driftplain-agent` and `driftplain-agent-security`. Follow the
[image naming policy](https://github.com/Steve-droid/driftplain/blob/main/IMAGE-NAMING.md).
Continue each existing version sequence; do not reset versions, reuse published tags or
delete old `modelmatch-*` packages. The verified starting points are backend 1.1.1,
frontend 1.1.0 and agents 1.1.3; check fresh tags before choosing the next version.

Keep existing production image pins until the new packages are published, public and
verified by an anonymous pull. Update both repository and digest for the first deployment
under a new name. Preserve Kubernetes, database, volume and CI credential/environment names.
This policy overrides older image-naming statements below; it does not authorize a deployment.

## HM8 done — releases publish to GHCR from GitHub Actions — September 22, 2026

Follow the [umbrella instructions](../CLAUDE.md). The home cluster is the only runtime. AWS
compute was retired on September 21 and HM7 moved `driftplain.dev` home on September 22. Home
runs backend 1.0.25 (built locally at HM7, answers that the chat is offline) and frontend 1.0.24
by digest from public GHCR with `LLM_CLIENT=fake` and `BLOB_STORE=fake`.

ECR was deleted at HM8. A `vX.Y.Z` tag runs
[`release-image.yml`](.github/workflows/release-image.yml); an `agent-vX.Y.Z` tag runs
[`release-agent-images.yml`](.github/workflows/release-agent-images.yml). Both push to
`ghcr.io/steve-droid/driftplain-*`, refuse to overwrite a published tag, and print the digest to
pin in the gitops home profile. The `Jenkinsfile*` pipelines and the `ci/` stack are kept for
reference only.

The design is the [HLD](../docs/planning/hld.md). Upcoming work is defined only in
[02-showcase-backlog.md](../docs/planning/02-showcase-backlog.md): HM6 wording, then P39.
Sentences below about ECR or Jenkins publishing are historical.

Preserve existing users/OAuth subjects, password hashes, JWT/DB values, projects, CI-token
hashes, findings, feedback, catalog and chat history. Do not log rows, passwords or credential
hashes. No blind migration or seed on restored data. `app/schemas/jenkins.py` and
`app/projects/jenkins_service.py` are metadata-only: secrets are rejected and legacy refs are
nullable/unused. Do not build a BYOK vault or replace existing CI tokens. Streaming, CORS and
OAuth callback behavior must survive the tunnel unchanged (HM5 tests them at the staging hosts;
`PUBLIC_BASE_URL`/`CORS_ALLOW_ORIGINS` come from the gitops home profile). No paid LLM calls.
Use fake LLM/disposable DB fixtures for any focused new test; don't rerun unchanged app suites
or invoke live E2E for documentation changes.

**Current working preference (Steve, September 15):** keep progressing and pause only
for critical architectural decisions. Plan, use focused tests for new behavior, verify and
self-review before routine commits/PRs; do not reintroduce the generic approval loops or
full-suite repetition below for unchanged work. This supersedes those older instructions
for this continuation. No paid LLM calls, public cutover, production teardown or destructive
source changes without explicit scope. No subagents/review agents, unsolicited diagrams
or additional tasks. Keep answers concise.

> Driftplain was previously Modicum / ModelMatch. The four public repositories use `driftplain-*`; existing infrastructure, database names, metrics and CI credential/environment identifiers retain `modelmatch` for compatibility. New image releases follow the policy above.

**Status: ACTIVE.** FastAPI backend for Driftplain **+ the CI-agent image**. See the umbrella
`../CLAUDE.md` and the design in `../docs/planning/hld.md`.

## Responsibilities

- **Deterministic recommender (NO LLM):** form → filter catalog → weighted score
  (`rank_score = w_q·quality + w_c·(1 − cost)`) → suggested model + baseline (+ shortlist). Pure
  function: same inputs → same output. Optional keyword pre-fill (rule-based).
- **Catalog ingestion (#3, LLM, in-cluster):** unstructured model/benchmark sources → **Bedrock Nova**
  extract → **validated** structured `benchmark_result` rows; **idempotent** (content-hash → skip
  unchanged); sources to **S3**. Fills the catalog; the formula still ranks.
- **Savings engine + quality gate:** per `ci_run`, `actual/baseline/savings`; acceptance-rate gate
  (failing runs excluded + surfaced). Baseline demo = **Sonnet, computed**.
- **Grounded Q&A chat (#4, LLM, in-cluster):** question → retrieve (savings + catalog) → **Bedrock
  Nova** → answer + **retrieval trace**; out-of-scope → honest refusal; SQL parameterized. Opening
  message = "explain my spend".
- **CI agent (`agent/`, the proof):** diff → AI code review via the **provider-agnostic `LLMClient`**
  (BYOK; adapters Bedrock·Anthropic·Gemini) → findings JSON + tokens → POST `/ci-runs`. Review +
  pass/fail **gate stay in CI**; never edits the repo. Built as a **second image** with its **own
  pipeline** (`Jenkinsfile.agent`, **P19**) — **publish-only, NOT an EKS workload** (it runs in a *user's*
  Jenkins via `docker run`): source→build→test→**Trivy**→publish the **same tested digest** to **private
  ECR (instance profile) + a public registry** (likely Docker Hub; separate `modelmatch/jenkins/*`
  credential), immutable tags. **No GitOps bump / ArgoCD / kubectl / cluster deploy.** Changing the
  `/ci-setup` default (`DEFAULT_AGENT_IMAGE=docker.io/…:<tag>`) is a **backend** release via P18/GitOps,
  not an agent deploy. **Trigger:** a **separate multibranch job** on `Jenkinsfile.agent` — **no
  long-lived `agent` branch**; the normal `feature/* → PR → main` model applies. The job wakes on
  webhook/SCM events, then a first **`Detect agent changes`** stage skips (exits a green **`skipped`**,
  not failure) unless an **agent-relevant** path changed (`agent/**`, `Jenkinsfile.agent`, agent build
  files, dep lockfiles `pyproject.toml`/`uv.lock`, shared `app/llm/**` + CI-run/finding schema/client,
  `tests/agent/**`) **or** `FORCE_AGENT_BUILD=true`. Feature/PR → build/test/Trivy only; `main` →
  also publish. (Historical — the mentor notes were removed from the umbrella at HM8.)
- **Auth:** register/login, JWT (argon2), owner-scoping. CI-run ingest authed by a **per-project
  token**, not the user JWT.

## Layout (target)

```
app/{api, models, schemas, recommend, ingest (#3), chat (#4), projects, ci, savings, observability,
     llm (LLMClient + adapters: bedrock / anthropic / gemini + fake)}
agent/        (CI-agent image: diff → LLMClient review → findings JSON; shares app.llm + the findings contract)
migrations/   (alembic; run as a Job/Helm hook, not on startup)
tests/        (unit: no containers · integration: testcontainers Postgres · all fake LLM + fixtures; live LLM only in the gated `e2e-live` E2E path on main/#e2e-live — historical Jenkins path)
```

## Rules

- **Postgres 16, in-cluster, NO pgvector** — no embeddings anywhere.
- **Two-surface model rule:** in-cluster LLM (ingestion + chat) = **Bedrock Nova via IRSA** (no static
  keys); the **agent** is **BYOK** multi-provider. Demo: Haiku live, Sonnet computed, Gemini free-tier
  (non-confidential code only).
- `/healthz` + `/readyz` split; `/metrics` (tokens, labeled by model+purpose — **not** $).
- **Per-request LLM log line** (model, latency, tokens in/out, retrieved-context size, truncated
  prompt+query, PII-redacted) for all three uses. **Never log diff content or secrets.**
- Config via `pydantic-settings` from env. Pydantic schemas, camelCase out. Errors 401/403/422/429/504.
- `uv` packaging. Branching: `feature/<story-id>-<desc>`; never commit to `main`.

Before asking Steve to approve a Story commit, Claude Code must:

1. Run the tests (focused area + full suite) and get them green on compose Postgres.
2. Present the **"What I built in this slice"** summary and STOP for Steve's explicit approval.

**Do NOT run `/pre-commit-scan`** — it has been removed from the workflow (2026-06-08): each run took
~10 min and it surfaced no meaningful logical bugs, while **Steve reviews with Codex** (faster, catches
more). Steve runs any review he wants **manually**; Claude does not invoke a review agent. Only
commit / merge / tag / push after Steve's explicit approval.

The flow:

```
Implement story
   |
Run tests  ->  not green? fix
   |
Show Steve the "What I built in this slice" summary
   |
Steve reviews (Codex, manual) + explicitly approves
   |
commit / merge / tag / push
```

## Build order touching this repo

S1 · S2 · S3 · S4 · S5 catalog · **S5b ingestion (#3)** · S6 pick · S7 pre-fill · S8 project · S9
Jenkins BYOK · **S10 CI agent (`agent/`, multi-provider)** · S11 ingest · S12 savings · S13 quality
gate · **S14b grounded chat (#4)** · S16 observability · S17 tests.
