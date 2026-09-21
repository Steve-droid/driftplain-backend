# Driftplain backend

[driftplain.dev](https://driftplain.dev) · [Frontend](https://github.com/Steve-droid/driftplain-frontend) · **Backend** · [Infra](https://github.com/Steve-droid/driftplain-infra) · [GitOps](https://github.com/Steve-droid/driftplain-gitops)

Driftplain picks a cheaper LLM for code review from benchmark data and runs it in the user's CI
on the user's own API key. There are two agents. The review agent makes one API call with the PR
diff and the user's review preferences. The security agent runs an agentic loop with OpenCode
over the checkout and reports vulnerabilities. The dashboard shows the money saved while review
quality holds.

This repo is the FastAPI backend and the source of the two agent images.

## What it does

**Recommender.** The user describes a task and a budget. The backend filters the benchmark
catalog and ranks candidates with `rank_score = w_q * quality + w_c * (1 - cost)`. It returns a
pick, a baseline and a shortlist. No LLM is involved in ranking. Only models the agent can run
are ranked.

**CI agent (`agent/`).** Two images: one reviews a PR diff, the other runs an OpenCode security
scan. Both post findings and token counts to `POST /ci-runs`. The user supplies the key
(Anthropic, Gemini or Bedrock; OpenAI through OpenCode for security only). The pass/fail
decision stays in the user's CI. The agent never edits the repo.

**Savings.** Per run, `actual = tokens * selected_price` and `baseline = tokens * baseline_price`.
The baseline is priced, never executed. Savings count only while the acceptance rate is above
`QUALITY_THRESHOLD`. Run ingestion is deterministic and costs no tokens.

**Catalog ingestion and chat.** These are the two in-cluster LLM uses (Bedrock Nova on AWS). At
home both use the fake client: the chat replies that the assistant is offline, and the catalog is
the seeded snapshot.

**Auth.** Password login (argon2 + JWT) and Google sign-in (ID token with a server nonce, no
Drive or Gmail scopes). Each project has its own CI token for run ingest. Registration is capped
by `MAX_REGISTERED_USERS`. New accounts get two example projects
([docs/example-projects.md](docs/example-projects.md), [docs/public-access.md](docs/public-access.md)).

Two rules hold everywhere. The in-cluster LLM is Bedrock Nova over IRSA only. The agent runs on
the user's key with any provider.

## Run it

```bash
cp .env.example .env                    # set a real JWT_SECRET; the placeholder is rejected
docker compose up -d db                 # PostgreSQL 16 on :5432
uv sync
uv run alembic upgrade head             # migrations never run on startup
uv run uvicorn app.main:app --reload    # :8000 with /docs, /healthz, /readyz, /metrics
```

All configuration comes from the environment (`pydantic-settings`, template in
[`.env.example`](.env.example)). The main settings:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | compose Postgres | connection string |
| `JWT_SECRET` | placeholder (rejected) | JWT signing key |
| `LLM_CLIENT`, `BLOB_STORE` | `fake` | `bedrock` / `s3` on AWS, `fake` at home and in tests |
| `BEDROCK_MODEL_ID`, `AWS_REGION`, `S3_BUCKET` | Nova Lite, `ap-south-1`, ingestion bucket | the AWS side |
| `LLM_HOURLY_TOKEN_CAP` | `200000` | hard cap on in-cluster Nova usage; requests above it get 429 |
| `BASELINE_MODEL_IDS`, `QUALITY_THRESHOLD` | Sonnet/Opus per task, `0.8` | savings baseline and quality gate |
| `AGENT_IMAGE`, `AGENT_SECURITY_IMAGE`, `CI_AGENT_TOKEN_CEILING` | `modelmatch-agent:latest`, `…-security:latest`, `20000` | images in the generated CI snippet and the per-run token ceiling |
| `PUBLIC_BASE_URL`, `CORS_ALLOW_ORIGINS`, `GOOGLE_CLIENT_ID` | localhost, localhost, empty | where CI posts back, allowed origins, Google client (empty disables it) |
| `MAX_REGISTERED_USERS`, `SEED_NEW_USER_EXAMPLES` | `700`, `true` | public demo limits |

Credentials are never literals. In the cluster they arrive as Kubernetes Secrets. The
`llm_call` log line records model, latency, tokens and context size, never the diff.

## The catalog

Rows are only comparable when they share a benchmark and a metric, so each task type maps to
exactly one pair. The mapping is enforced on upsert and at rank time. Every score cites a dated
source, and a test rejects uncited figures.

| Task type | Benchmark | Metric | Baseline |
|---|---|---|---|
| `ci_review` | CodeReviewBench (June 2026) | `review_score_percent` | Claude Sonnet 4.5 |
| `security_analysis` | RealVuln v2.1 | `f3_score` | Claude Opus 5 |
| `agentic_coding` | SWE-bench Verified | `pass@1_percent` | data only |

The security rows come from RealVuln's published JSON and YAML in [`data/catalog/`](data/catalog),
loaded by `uv run python -m app.catalog.ingest_realvuln`. The loader is idempotent by content
hash and skips unpriced scanners.

## Tests

All LLM calls go through one `LLMClient` with a fake implementation and recorded fixtures, so the
suite runs offline. Postgres must be running.

```bash
uv run pytest                        # everything
uv run pytest -m "not integration"   # no database
uv run pytest -m integration         # database-backed tests
uv run pytest tests/agent            # the CI agent, offline
scripts/agent-live-smoke.sh          # manual: the agent against a real model on the diffs in docs/tests/
```

## Releasing

Merge, then push an annotated tag. GitHub Actions builds `linux/amd64` and pushes to public GHCR.
A published tag is never overwritten. The job summary prints the digest to pin in the gitops
home profile.

| Tag | Workflow | Image |
|---|---|---|
| `vX.Y.Z` | [`release-image.yml`](.github/workflows/release-image.yml) | `ghcr.io/steve-droid/modelmatch-backend:X.Y.Z` (images keep the project's old `modelmatch` name) |
| `agent-vX.Y.Z` | [`release-agent-images.yml`](.github/workflows/release-agent-images.yml) | `ghcr.io/steve-droid/modelmatch-agent:X.Y.Z` and `modelmatch-agent-security:X.Y.Z` |

Deployment is a separate digest bump in
[driftplain-gitops](https://github.com/Steve-droid/driftplain-gitops). The `Jenkinsfile` and
`Jenkinsfile.agent` pipelines ran on the AWS Jenkins controller until September 21, 2026 and are
kept for reference.

## Layout

```
app/          api, auth, models, schemas, recommend, catalog, ingest, chat, projects, ci, savings,
              quality, demo, observability, llm (LLMClient and adapters), llm_budget, blob_store, config
agent/        the review image (Dockerfile) and the OpenCode security image (Dockerfile.security)
migrations/   Alembic, run as a Job in the cluster
tests/        pytest with the fake LLM; tests/agent covers the agent
ci/           compose stack and smoke scripts used by the old pipelines
data/         checked-in catalog sources
docs/         example-projects.md, public-access.md, three draw.io diagrams, tests/ (smoke diffs)
```

Steve Levit, stevelevit230@gmail.com
