# Driftplain backend

[Project overview](https://github.com/Steve-droid/driftplain) · [Open the app](https://driftplain.dev) · [Frontend](https://github.com/Steve-droid/driftplain-frontend) · [Infrastructure](https://github.com/Steve-droid/driftplain-infra) · [GitOps](https://github.com/Steve-droid/driftplain-gitops)

This repo contains Driftplain's API and two CI agents. The API stores projects, model
benchmarks, review findings and usage. The agents run in the user's Jenkins pipeline with
their own model credentials and send results back to the API.

The backend uses Python 3.12, FastAPI, SQLAlchemy and PostgreSQL 16. Alembic manages database
changes, and `uv` manages Python dependencies.

## Main components

| Component | What it does | Code |
|---|---|---|
| Catalog | Stores models, providers, benchmark results and their sources. Provides read-only search and detail APIs at `/catalog/v1`. | [app/catalog](app/catalog/), [catalog routes](app/api/catalog.py) |
| Recommendations | Ranks models using benchmark scores and token prices. This is a calculation, with no model call. | [app/recommend](app/recommend/) |
| Projects and CI setup | Saves the selected model and review preferences, issues a project CI token and generates a Jenkins stage. | [app/projects](app/projects/), [app/ci](app/ci/) |
| Runs and feedback | Accepts findings and token counts at `POST /ci-runs`, records feedback and calculates costs. | [app/ci](app/ci/), [app/quality](app/quality/), [app/savings](app/savings/) |
| Authentication | Supports password login and Google sign-in. Users can access only their own projects and runs. | [app/auth](app/auth/) |
| Ingestion and chat | Imports benchmark data and answers questions about project usage. Both support fake model clients for development. | [app/ingest](app/ingest/), [app/chat](app/chat/), [app/llm](app/llm/) |

The current recommendation flow combines benchmark scores and price into a weighted ranking.
Its cost comparison prices one run's token usage at both the selected model's rates and a
baseline model's rates. The baseline is not run, so the difference is an estimate rather
than measured savings. Feedback controls which runs count toward the dashboard total.

The public catalog API is available in this source tree. The benchmark browsing UI is still
in development. The hosted deployment uses an earlier backend image and has its chat assistant
disabled; see [GitOps](https://github.com/Steve-droid/driftplain-gitops) for deployed image pins.

## CI agents

| Agent | Input and execution | Default failure rule |
|---|---|---|
| Code review | Makes one model call with a pull-request diff and review preferences. | A high or critical finding fails the stage. |
| Security analysis | Uses OpenCode to inspect a read-only checkout over multiple steps. | A critical finding fails the stage. |

Both agents enforce token limits and report findings and usage. They do not edit the checkout.
Model credentials stay in Jenkins, and the API receives results through a separate project CI
token. The review agent supports Anthropic, Gemini and Bedrock; the security agent also supports
DeepSeek and OpenAI through OpenCode.

The [agent guide](agent/README.md) covers container builds, configuration, output and exit codes.
The two Dockerfiles live in [agent/](agent/).

## Run locally

Install Python 3.12 or newer, `uv` and Docker. Copy the environment template, then replace
the `JWT_SECRET` placeholder in `.env` with a random signing key of at least 32 characters.

```bash
cp .env.example .env
```

After editing `.env`, start the database, install dependencies and prepare the local catalog:

```bash
docker compose up -d db
uv sync
uv run alembic upgrade head
uv run python -m app.catalog.seed
uv run uvicorn app.main:app --reload
```

Open [API docs](http://localhost:8000/docs) to try the endpoints. `/healthz` checks the process,
`/readyz` checks the database connection, and `/metrics` exposes Prometheus metrics.

Local defaults use fake model and blob clients, so setup needs no model API key. The catalog
seed loads checked-in data. Migrations run explicitly, not when the API starts.

### Configuration

[.env.example](.env.example) lists the settings. The main ones are:

| Setting | Purpose |
|---|---|
| `DATABASE_URL`, `JWT_SECRET` | Database connection and login token signing key. |
| `CORS_ALLOW_ORIGINS`, `PUBLIC_BASE_URL` | Allowed browser origins and the API address used by Jenkins. |
| `GOOGLE_CLIENT_ID` | Enables Google sign-in when configured. |
| `AGENT_IMAGE`, `AGENT_SECURITY_IMAGE` | Agent images used in generated Jenkins stages. |
| `LLM_CLIENT`, `BLOB_STORE` | Select fake clients locally or the supported AWS integrations. |
| `LLM_HOURLY_TOKEN_CAP`, `CI_AGENT_TOKEN_CEILING` | Limit hosted model usage and individual CI runs. |

## Tests

```bash
uv run pytest -m "not integration"  # unit tests, no database
uv run pytest -m integration        # database-backed tests
uv run pytest tests/agent           # agent tests with fake model responses
```

Database tests need the local Postgres container. They create a separate test database and
apply migrations. Tests use fake model responses and fixtures without paid API calls.

## Releases and deployment

GitHub Actions publishes Linux amd64 images to public GHCR:

| Tag | Images | Workflow |
|---|---|---|
| `vX.Y.Z` | `driftplain-backend` | [Backend release](.github/workflows/release-image.yml) |
| `agent-vX.Y.Z` | `driftplain-agent`, `driftplain-agent-security` | [Agent release](.github/workflows/release-agent-images.yml) |

All images use the `ghcr.io/steve-droid/` prefix. New releases use `driftplain-*` package names. Existing
`modelmatch-*` images remain available, and version numbers continue from those packages.
See the [image naming policy](https://github.com/Steve-droid/driftplain/blob/main/IMAGE-NAMING.md). Publishing an image does not deploy it. Deployment requires an
image digest update in [driftplain-gitops](https://github.com/Steve-droid/driftplain-gitops).

[migrations/](migrations/) contains the schema history, [tests/](tests/) contains the API and
agent tests, and [data/](data/) contains benchmark source files. The `Jenkinsfile` and
`Jenkinsfile.agent` retain the build and test pipelines used by the former AWS Jenkins controller.

## Independent benchmark imports

The explicit `python -m app.catalog.imports` command validates or promotes attributed catalog
inputs independently of CI runtime support. Validation is the default; `--promote` is the
operator's database-write action. See [B3 familiar sources](data/catalog/b3/README.md) and
[B4 independent/CI sources](data/catalog/b4/README.md) for the 19-family registry, exact
versions, supported fetch modes, honest coverage gaps and immutable-history contracts.
No importer runs on server startup and publication does not seed or deploy the service.

### Explicit selections (B6)

The additive [`/execution/v1` contract](app/selections/CONTRACT.md) supports exact source
choices, task/profile eligibility, benchmark policy snapshots, owner-scoped projects and
immutable execution revisions. New explicit projects have no comparison baseline. Legacy
projects/agents retain their existing contract. The new runtime set starts empty pending
exact task/profile verification; imported scores never enable execution. Migration head is
`d7e8f9a0b1c2`. This release does not enable a provider, migrate production or deploy an agent.

### Selected-run usage and cost (B15)

The owner-scoped `/projects/{id}/usage/v1` dashboard read separates complete estimates,
partial known charges, unavailable and legacy runs. Immutable exact-runtime rates are pinned
to execution revisions and priced with Decimal; feedback never changes cost. Historical
comparison amounts remain in compatibility APIs with their input/output-only limitations.
See [the billing contract](app/billing/CONTRACT.md) for operator rate authority, returned-tier
telemetry, additive migration and rollout order. No prices or pending runtimes are activated.
