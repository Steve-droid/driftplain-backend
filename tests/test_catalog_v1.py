"""B2 public catalog contracts against PostgreSQL; no auth or provider calls."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.models import (
    AgentRuntimeConfig,
    CatalogBenchmarkFamily,
    CatalogBenchmarkVersion,
    CatalogEvaluator,
    CatalogMetricDefinition,
    CatalogModel,
    CatalogModelAlias,
    CatalogObservation,
    CatalogObservationMetric,
    CatalogProtocol,
    CatalogProvider,
    CatalogProviderDeployment,
    CatalogSource,
    CatalogSourceSnapshot,
    Model,
)
from app.catalog import service as legacy_catalog_service
from app.schemas.catalog import CatalogRowIn


def _seed_normalized_catalog(db):
    model_a = CatalogModel(slug="alpha", name="Alpha 1", organization="Example Lab")
    model_b = CatalogModel(slug="beta", name="Beta 2", organization="Other Lab")
    provider = CatalogProvider(slug="cloud-example", name="Cloud Example")
    family = CatalogBenchmarkFamily(
        slug="bench-one",
        name="Bench One",
        description="A public benchmark.",
        tooltip="Measures one thing.",
        methodology_url="https://example.test/method",
        limitations="One known limit.",
    )
    source = CatalogSource(
        slug="reviewed-report",
        name="Reviewed Report",
        definition_url="https://example.test/definition",
        result_url="https://example.test/results",
    )
    evaluator_a = CatalogEvaluator(name="Eval Lab")
    evaluator_b = CatalogEvaluator(name="Second Lab")
    db.add_all([model_a, model_b, provider, family, source, evaluator_a, evaluator_b])
    db.flush()

    deployment = CatalogProviderDeployment(
        provider_id=provider.id,
        model_id=model_a.id,
        deployment_key="alpha-1-hosted",
        name="Alpha Hosted",
    )
    resolved = CatalogModelAlias(
        source_id=source.id,
        source_label="Alpha-v1",
        normalized_label="alpha-v1",
        resolution_status="resolved",
        catalog_model_id=model_a.id,
        reviewed_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    unresolved = CatalogModelAlias(
        source_id=source.id,
        source_label="Mystery Model",
        normalized_label="mystery model",
        resolution_status="unresolved",
        reviewed_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    ambiguous = CatalogModelAlias(
        source_id=source.id,
        source_label="Shared Model Label",
        normalized_label="shared model label",
        resolution_status="ambiguous",
        reviewed_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    version_a = CatalogBenchmarkVersion(
        benchmark_family_id=family.id,
        version="2026.1",
        release_date=date(2026, 1, 1),
        methodology="Version methodology",
    )
    version_b = CatalogBenchmarkVersion(
        benchmark_family_id=family.id,
        version="2026.2",
        release_date=date(2026, 2, 1),
    )
    db.add_all([deployment, resolved, unresolved, ambiguous, version_a, version_b])
    db.flush()

    protocol_a = CatalogProtocol(
        benchmark_version_id=version_a.id,
        name="no-tools",
        configuration_fingerprint="a" * 64,
        runner="Official runner",
        configuration={"tools": False, "attempts": 1},
    )
    protocol_b = CatalogProtocol(
        benchmark_version_id=version_b.id,
        name="tools",
        configuration_fingerprint="b" * 64,
        runner="Official runner",
        configuration={"tools": True, "attempts": 3},
    )
    snapshot = CatalogSourceSnapshot(
        source_id=source.id,
        content_hash="c" * 64,
        artifact_uri="https://example.test/results.json",
        fetched_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
        byte_count=1234,
    )
    metric = CatalogMetricDefinition(
        benchmark_family_id=family.id,
        key="accuracy",
        name="Accuracy",
        unit="percent",
        direction="higher",
        minimum=Decimal("0"),
        maximum=Decimal("100"),
    )
    db.add_all([protocol_a, protocol_b, snapshot, metric])
    db.flush()

    known = CatalogObservation(
        benchmark_family_id=family.id,
        benchmark_version_id=version_a.id,
        protocol_id=protocol_a.id,
        evaluator_id=evaluator_a.id,
        source_snapshot_id=snapshot.id,
        source_record_locator="rows/alpha",
        configuration_fingerprint="d" * 64,
        record_fingerprint="e" * 64,
        source_model_label="Alpha-v1",
        catalog_model_id=model_a.id,
        provider_deployment_id=deployment.id,
        origin="source",
        provenance_status="complete",
        observed_at=date(2026, 9, 20),
    )
    unknown = CatalogObservation(
        benchmark_family_id=family.id,
        benchmark_version_id=version_b.id,
        protocol_id=protocol_b.id,
        evaluator_id=evaluator_b.id,
        source_snapshot_id=snapshot.id,
        source_record_locator="rows/mystery",
        configuration_fingerprint="f" * 64,
        record_fingerprint="1" * 64,
        source_model_label="Mystery Model",
        origin="source",
        provenance_status="complete",
        observed_at=date(2026, 9, 21),
    )
    db.add_all([known, unknown])
    db.flush()
    db.add_all([
        CatalogObservationMetric(
            observation_id=known.id,
            metric_definition_id=metric.id,
            value=Decimal("91.25"),
            reported_value="91.25",
            confidence_low=Decimal("89.00"),
            confidence_high=Decimal("93.00"),
            confidence_level=Decimal("95"),
            sample_size=500,
        ),
        CatalogObservationMetric(
            observation_id=unknown.id,
            metric_definition_id=metric.id,
            value=None,
            missing_reason="not reported",
            subset="hard",
        ),
    ])
    db.commit()
    return {
        "model_a": model_a,
        "model_b": model_b,
        "provider": provider,
        "family": family,
        "known": known,
        "unknown": unknown,
    }


def _all_keys(value):
    if isinstance(value, dict):
        yield from value
        for nested in value.values():
            yield from _all_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _all_keys(nested)


def test_public_catalog_is_anonymous_and_keeps_unknown_evidence(client, db_session):
    graph = _seed_normalized_catalog(db_session)

    assert client.get("/benchmarks").status_code == 401
    models = client.get("/catalog/v1/models")
    benchmarks = client.get("/catalog/v1/benchmarks")
    observations = client.get("/catalog/v1/observations")

    assert models.status_code == benchmarks.status_code == observations.status_code == 200
    model_names = [item["name"] for item in models.json()["items"]]
    assert "Alpha 1" in model_names
    assert "Beta 2" in model_names
    assert benchmarks.json()["items"][0]["tooltip"] == "Measures one thing."

    rows = observations.json()["items"]
    assert len(rows) == 2
    mystery = next(row for row in rows if row["sourceModelLabel"] == "Mystery Model")
    assert mystery["modelId"] is None
    assert mystery["metrics"][0]["value"] is None
    assert mystery["metrics"][0]["missingReason"] == "not reported"
    assert mystery["version"] == "2026.2"
    assert mystery["protocol"] == "tools"
    assert mystery["evaluator"] == "Second Lab"
    assert mystery["sourceContentHash"] == "c" * 64

    by_provider = client.get(
        "/catalog/v1/observations", params={"providerId": graph["provider"].id}
    )
    assert [item["sourceModelLabel"] for item in by_provider.json()["items"]] == [
        "Alpha-v1"
    ]
    assert client.get(
        "/catalog/v1/observations", params={"model_id": graph["model_a"].id}
    ).status_code == 422

    forbidden = {"userId", "projectId", "credentialEnvVar", "usage", "s3Key", "rawPayload"}
    for payload in (models.json(), benchmarks.json(), observations.json()):
        assert forbidden.isdisjoint(_all_keys(payload))

    detail = client.get(f"/catalog/v1/models/{graph['model_a'].id}")
    assert detail.status_code == 200
    assert detail.json()["aliases"] == ["Alpha-v1"]
    assert detail.json()["deployments"][0]["providerName"] == "Cloud Example"
    assert client.get("/catalog/v1/models/999999").status_code == 404


def test_catalog_cursor_is_stable_bound_to_query_and_validated(client, db_session):
    _seed_normalized_catalog(db_session)
    db_session.add(CatalogModel(slug="gamma", name="Gamma 3", organization="Third Lab"))
    db_session.commit()

    first = client.get("/catalog/v1/models", params={"limit": 2, "q": "lab"})
    assert first.status_code == 200
    cursor = first.json()["pageInfo"]["nextCursor"]
    assert cursor
    second = client.get(
        "/catalog/v1/models", params={"limit": 2, "q": "lab", "cursor": cursor}
    )
    names = [x["name"] for x in first.json()["items"] + second.json()["items"]]
    assert names == ["Alpha 1", "Beta 2", "Gamma 3"]
    assert len(names) == len(set(names))

    assert client.get("/catalog/v1/models", params={"cursor": cursor, "q": "alpha"}).status_code == 422
    assert client.get("/catalog/v1/benchmarks", params={"cursor": cursor}).status_code == 422
    assert client.get("/catalog/v1/models", params={"cursor": "not-a-cursor"}).status_code == 422
    assert client.get("/catalog/v1/models", params={"limit": 101}).status_code == 422


def test_search_finds_alias_benchmark_and_provider_with_deterministic_types(client, db_session):
    _seed_normalized_catalog(db_session)

    alias = client.get("/catalog/v1/search", params={"type": "model", "q": "Alpha-v1"})
    benchmark = client.get("/catalog/v1/search", params={"type": "benchmark", "q": "Bench"})
    provider = client.get("/catalog/v1/search", params={"type": "provider", "q": "Cloud"})

    assert alias.status_code == benchmark.status_code == provider.status_code == 200
    assert [(x["type"], x["name"]) for x in alias.json()["items"]] == [("model", "Alpha 1")]
    assert benchmark.json()["items"][0]["type"] == "benchmark"
    assert provider.json()["items"][0]["type"] == "provider"
    provider_models = client.get(
        "/catalog/v1/search", params={"type": "model", "q": "Cloud Example"}
    )
    assert provider_models.json()["items"][0]["name"] == "Alpha 1"
    assert client.get("/catalog/v1/search", params={"type": "model", "q": "%"}).json()["items"] == []
    assert client.get("/catalog/v1/search", params={"type": "model", "q": "x" * 201}).status_code == 422
    assert client.get("/catalog/v1/search", params={"type": "invalid", "q": "x"}).status_code == 422


def test_runtime_disable_does_not_change_catalog_counts_or_observations(client, db_session):
    graph = _seed_normalized_catalog(db_session)
    legacy = Model(name="Runtime model", vendor="Provider")
    db_session.add(legacy)
    db_session.flush()
    db_session.add(AgentRuntimeConfig(
        model_id=legacy.id,
        provider="anthropic",
        provider_model_id="runtime-model",
        auth_mode="api_key",
        credential_env_var="MODEL_API_KEY",
        enabled=True,
    ))
    db_session.commit()

    before = {
        "models": client.get("/catalog/v1/models").json(),
        "observations": client.get("/catalog/v1/observations").json(),
    }
    db_session.execute(update(AgentRuntimeConfig).values(enabled=False))
    db_session.commit()
    after = {
        "models": client.get("/catalog/v1/models").json(),
        "observations": client.get("/catalog/v1/observations").json(),
    }
    assert after == before
    assert before["models"]["items"][0]["id"] == graph["model_a"].id


def test_multiple_protocols_evaluators_and_nullable_metrics_remain_distinct(db_session):
    graph = _seed_normalized_catalog(db_session)
    assert db_session.scalar(select(func.count()).select_from(CatalogObservation)) == 2
    observations = db_session.scalars(select(CatalogObservation).order_by(CatalogObservation.id)).all()
    assert observations[0].protocol_id != observations[1].protocol_id
    assert observations[0].evaluator_id != observations[1].evaluator_id
    values = db_session.scalars(
        select(CatalogObservationMetric.value).order_by(CatalogObservationMetric.observation_id)
    ).all()
    assert values == [Decimal("91.25000000"), None]


def test_legacy_upsert_appends_observation_only_when_evidence_changes(db_session):
    row = CatalogRowIn(
        model="Compatibility Model",
        vendor="Maker",
        benchmark="Compatibility Bench",
        metric="accuracy",
        score=Decimal("70"),
        cost_per_mtok=Decimal("1.5"),
        harness="Legacy Runner",
        harness_vendor="Lab",
        task_type="ci_review",
        context_window=32000,
        source="https://example.test/legacy",
        measured_at=date(2026, 9, 1),
    )

    legacy_catalog_service.upsert_catalog_row(db_session, row)
    legacy_catalog_service.upsert_catalog_row(db_session, row)
    legacy_catalog_service.upsert_catalog_row(
        db_session, row.model_copy(update={"score": Decimal("72")})
    )

    observations = db_session.scalars(
        select(CatalogObservation).order_by(CatalogObservation.id)
    ).all()
    assert len(observations) == 2
    assert all(item.origin == "legacy_backfill" for item in observations)
    assert all(item.provenance_status == "incomplete" for item in observations)
    assert all(item.benchmark_version_id is None for item in observations)
    values = db_session.scalars(
        select(CatalogObservationMetric.value).order_by(CatalogObservationMetric.id)
    ).all()
    assert values == [Decimal("70.00000000"), Decimal("72.00000000")]


def test_evidence_rows_are_immutable_and_alias_resolution_is_constrained(db_session):
    graph = _seed_normalized_catalog(db_session)

    with pytest.raises(DatabaseError, match="catalog evidence is immutable"):
        db_session.execute(
            update(CatalogObservation)
            .where(CatalogObservation.id == graph["known"].id)
            .values(source_model_label="rewritten")
        )
    db_session.rollback()

    source = db_session.scalar(select(CatalogSource).where(CatalogSource.slug == "reviewed-report"))
    unresolved = db_session.scalars(
        select(CatalogModelAlias).where(
            CatalogModelAlias.resolution_status.in_(["unresolved", "ambiguous"])
        )
    ).all()
    assert {alias.resolution_status for alias in unresolved} == {"unresolved", "ambiguous"}
    assert all(alias.catalog_model_id is None for alias in unresolved)
    invalid = CatalogModelAlias(
        source_id=source.id,
        source_label="False resolution",
        normalized_label="false resolution",
        resolution_status="resolved",
        catalog_model_id=None,
        reviewed_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    db_session.add(invalid)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
