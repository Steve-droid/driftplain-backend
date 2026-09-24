"""B6: exact, owner-scoped evidence choices and execution history."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import ExecutionRevision, ExecutionRuntime, ModelSelection
from app.selections.policy import order_scores, policy_for


def test_policy_profiles_are_explicit():
    assert policy_for("ci_review", "single_call", None, False)["metric"] == "f1"
    assert (
        policy_for("test_generation", "opencode", "python", False)["metric"] == "e_at_1"
    )
    assert policy_for("test_generation", "opencode", "node", False)["metric"] is None
    assert (
        policy_for("ci_failure_diagnosis", "opencode", None, True)["capability"]
        == "diagnosis_fix"
    )
    assert policy_for("other", "single_call", None, False)["metric"] is None
    for args in [
        ("ci_review", "opencode", None, False),
        ("other", "single_call", None, True),
        ("test_generation", "opencode", "ruby", False),
    ]:
        with pytest.raises(ValueError):
            policy_for(*args)


def test_precision_direction_ties_and_no_score():
    rows = [
        (9, Decimal("0.12345678")),
        (2, Decimal("0.12345679")),
        (1, Decimal("0.12345678")),
        (7, None),
    ]
    assert order_scores(rows, "higher") == [(2, 1), (1, 2), (9, 2)]
    assert order_scores(rows, "lower") == [(1, 1), (9, 1), (2, 3)]


def test_no_runtime_is_silently_verified(db_session):
    assert list(db_session.scalars(select(ExecutionRuntime))) == []


def test_v2_empty_eligible_set_is_not_legacy_fallback(client, db_session):
    from tests.test_ci import _register

    headers, _ = _register(client, db_session, "b6-empty@example.com")
    r = client.get(
        "/execution/v1/candidates",
        params={"task": "ci_review", "mode": "single_call"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["groups"] == []


@pytest.fixture
def evidence(db_session):
    """Synthetic source/runtime evidence only; never activation evidence."""
    from datetime import UTC, datetime

    from app.models import (
        CatalogBenchmarkFamily,
        CatalogBenchmarkVersion,
        CatalogEvaluator,
        CatalogMetricDefinition,
        CatalogModel,
        CatalogObservation,
        CatalogObservationMetric,
        CatalogProtocol,
        CatalogProvider,
        CatalogProviderDeployment,
        CatalogSource,
        CatalogSourceSnapshot,
    )

    db = db_session

    def add(cls, **kw):
        row = cls(**kw)
        db.add(row)
        db.flush()
        return row

    p = policy_for("ci_review", "single_call")
    family = add(CatalogBenchmarkFamily, slug=p["benchmark"], name="Synthetic review")
    version = add(
        CatalogBenchmarkVersion,
        benchmark_family_id=family.id,
        version=p["benchmarkVersion"],
    )
    source = add(CatalogSource, slug="b6-fixture", name="Synthetic source")
    snapshot = add(CatalogSourceSnapshot, source_id=source.id, content_hash="f" * 64)
    evaluator = add(CatalogEvaluator, name="Synthetic evaluator")
    provider = db.scalar(
        select(CatalogProvider).where(CatalogProvider.slug == "anthropic")
    )
    if provider is None:
        provider = add(CatalogProvider, slug="anthropic", name="Anthropic")
    proto_config = {
        "runner": "kodus",
        "runner_version": "fixture",
        "execution_mode": "replay",
        "judge": "claude-haiku-4-5",
        "pull_requests": 30,
        "bugs": 95,
        "coverage": "complete",
        "recommendation_eligible": True,
        "comparison_group": "fixture-group",
    }
    proto = add(
        CatalogProtocol,
        benchmark_version_id=version.id,
        name="Fixture protocol",
        runner="Synthetic Kodus",
        configuration_fingerprint="1" * 64,
        configuration=proto_config,
    )
    metric = add(
        CatalogMetricDefinition,
        benchmark_family_id=family.id,
        benchmark_version_id=version.id,
        key="f1",
        name="F1",
        direction="higher",
        unit="percent",
    )
    rows = []
    for idx, (name, score) in enumerate(
        [
            ("Alpha", "80.12345678"),
            ("Beta", "80.12345678"),
            ("Gamma", None),
            ("Unsupported", "99"),
        ]
    ):
        model = add(CatalogModel, slug="b6-" + name.lower(), name=name)
        dep = add(
            CatalogProviderDeployment,
            model_id=model.id,
            provider_id=provider.id,
            deployment_key="fixture-" + name.lower(),
            name=name,
        )
        rt = add(
            ExecutionRuntime,
            catalog_model_id=model.id,
            deployment_id=dep.id,
            task="ci_review",
            mode="single_call",
            capability="ci_review",
            provider="anthropic",
            provider_model_id=dep.deployment_key,
            auth_mode="api_key",
            credential_env_var="ANTHROPIC_API_KEY",
            runtime_version="fixture-only",
            enabled=idx != 3,
            verification_status="verified",
            verified_at=datetime(2026, 9, 24, tzinfo=UTC),
            verification_ref="synthetic test, not live evidence",
        )
        obs = add(
            CatalogObservation,
            benchmark_family_id=family.id,
            benchmark_version_id=version.id,
            protocol_id=proto.id,
            evaluator_id=evaluator.id,
            source_snapshot_id=snapshot.id,
            source_record_locator=name,
            configuration_fingerprint="1" * 64,
            record_fingerprint=str(idx) * 64,
            source_model_label=name,
            catalog_model_id=model.id,
            origin="source",
            provenance_status="complete",
        )
        add(
            CatalogObservationMetric,
            observation_id=obs.id,
            metric_definition_id=metric.id,
            value=Decimal(score) if score else None,
            reported_value=score,
            missing_reason="not reported" if score is None else None,
        )
        rows.append((rt, obs))
    db.commit()
    return rows


def pick(rt, obs, **kw):
    return dict(
        task="ci_review",
        mode="single_call",
        runtimeId=rt.id,
        observationId=obs.id,
        method="supported_unranked",
        **kw,
    )


def create(client, headers, choice):
    r = client.post(
        "/execution/v1/projects",
        headers=headers,
        json={"name": "Explicit", "selection": choice},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_candidates_ties_missing_scores_and_direct_search(client, db_session, evidence):
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-rank@example.com")
    params = {"task": "ci_review", "mode": "single_call"}
    result = client.get("/execution/v1/candidates", params=params, headers=h).json()
    assert len(result["items"]) == 3
    assert all(i["method"] == "supported_unranked" for i in result["items"])
    assert result["groups"][0]["totalResults"] == 3  # includes unsupported source score
    group = result["groups"][0]["id"]
    ranked = client.get(
        "/execution/v1/candidates", params={**params, "group": group}, headers=h
    ).json()["items"]
    assert [i["rank"] for i in ranked] == [1, 1, None]
    assert [i["position"] for i in ranked] == [1, 2, None]
    assert ranked[0]["score"] == "80.12345678"
    assert ranked[2]["score"] is None
    found = client.get(
        "/execution/v1/candidates", params={**params, "q": "Gamma"}, headers=h
    ).json()
    assert len(found["items"]) == 1
    project = create(client, h, pick(*evidence[2]))
    assert project["baselineModelId"] is None and project["policyResult"] is None
    choice = pick(*evidence[0])
    choice.update(method="benchmark_ranked", group=group)
    project = create(client, h, choice)
    assert project["policyResult"]["rank"] == 1


def test_selection_config_repick_and_inflight_run_keep_original_revision(
    client, db_session, evidence
):
    from app.models import CatalogSnapshotLifecycle, CiRun, Project
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-history@example.com")
    project = create(client, h, pick(*evidence[0]))
    pid = project["id"]
    revision = project["executionRevisionId"]
    assert db_session.get(
        CatalogSnapshotLifecycle, evidence[0][1].source_snapshot_id
    ).hold_reason
    token = client.post(f"/execution/v1/projects/{pid}/ci-token", headers=h).json()[
        "token"
    ]
    assert token
    assert (
        client.post(f"/execution/v1/projects/{pid}/ci-token", headers=h).json()["token"]
        is None
    )
    ci = {"X-CI-Token": token}
    cfg = client.get(f"/execution/v1/projects/{pid}/agent-config", headers=ci)
    assert cfg.status_code == 200 and cfg.json()["executionRevisionId"] == revision
    assert cfg.json()["model"]["providerModelId"] == "fixture-alpha"
    assert client.get(f"/projects/{pid}/agent-config", headers=ci).status_code == 422
    updated = client.patch(
        f"/execution/v1/projects/{pid}",
        headers=h,
        json={"selection": pick(*evidence[1])},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["selectionId"] != project["selectionId"]
    run = {
        "jenkinsBuildId": "b6-1",
        "tokensIn": 1,
        "tokensOut": 1,
        "model": "fixture-alpha",
        "gate": "pass",
        "executionRevisionId": revision,
    }
    response = client.post(f"/projects/{pid}/ci-runs", headers=ci, json=run)
    assert response.status_code == 201, response.text
    assert response.json()["executionRevisionId"] == revision
    assert response.json()["savings"] is None
    row = db_session.get(CiRun, response.json()["id"])
    assert row.task == "ci_review" and row.execution_revision_id == revision
    assert (
        client.post(
            f"/projects/{pid}/ci-runs",
            headers=ci,
            json={**run, "jenkinsBuildId": "b6-bad", "model": "fixture-beta"},
        ).status_code
        == 422
    )
    del run["executionRevisionId"]
    run["jenkinsBuildId"] = "b6-missing"
    assert (
        client.post(f"/projects/{pid}/ci-runs", headers=ci, json=run).status_code == 422
    )
    assert client.get("/projects", headers=h).status_code == 200
    old = db_session.get(ModelSelection, project["selectionId"])
    assert old.observation_id == evidence[0][1].id
    assert (
        client.patch(
            f"/projects/{pid}", headers=h, json={"selectedOptionId": 1}
        ).status_code
        == 422
    )
    # Owner-requested project deletion must retain existing cascade semantics.
    assert client.delete(f"/projects/{pid}", headers=h).status_code == 204
    db_session.expire_all()
    assert db_session.get(Project, pid) is None


def test_tenancy_and_disabled_runtime(client, db_session, evidence):
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-owner@example.com")
    stranger, _ = _register(client, db_session, "b6-stranger@example.com")
    project = create(client, h, pick(*evidence[0]))
    pid = project["id"]
    assert (
        client.patch(
            f"/execution/v1/projects/{pid}",
            headers=stranger,
            json={"selection": pick(*evidence[1])},
        ).status_code
        == 403
    )
    assert (
        client.get(f"/execution/v1/projects/{pid}", headers=stranger).status_code == 403
    )
    assert (
        client.post(
            f"/execution/v1/projects/{pid}/ci-token", headers=stranger
        ).status_code
        == 403
    )
    token = client.post(f"/execution/v1/projects/{pid}/ci-token", headers=h).json()[
        "token"
    ]
    evidence[0][0].enabled = False
    db_session.commit()
    assert (
        client.get(
            f"/execution/v1/projects/{pid}/agent-config", headers={"X-CI-Token": token}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/execution/v1/projects/{pid}", headers=h, json={"name": "Rename"}
        ).status_code
        == 422
    )
    # History remains readable while execution is disabled.
    assert client.get(f"/execution/v1/projects/{pid}", headers=h).status_code == 200
    assert client.get(f"/execution/v1/projects/{pid}/agent-config").status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"observationId": 999999},
        {"runtimeId": 999999},
        {"method": "benchmark_ranked"},
        {"mode": "opencode"},
        {"task": "other"},
        {"proposeFix": True},
        {"group": "bad"},
        {"providerModelId": "arbitrary"},
        {"language": "node"},
    ],
)
def test_forged_selection_rejected_without_partial_project(
    client, db_session, evidence, change
):
    from app.models import CatalogSnapshotLifecycle, Project
    from tests.test_ci import _register

    h, uid = _register(client, db_session, "b6-forged@example.com")
    choice = {**pick(*evidence[0]), **change}
    r = client.post(
        "/execution/v1/projects", headers=h, json={"name": "bad", "selection": choice}
    )
    assert r.status_code == 422, r.text
    assert list(db_session.scalars(select(Project).where(Project.user_id == uid))) == []
    assert (
        db_session.get(CatalogSnapshotLifecycle, evidence[0][1].source_snapshot_id)
        is None
    )


def test_database_identity_and_history_constraints(db_session, evidence):
    from sqlalchemy.exc import DatabaseError

    rt = evidence[0][0]
    rt.provider_model_id = "wrong-route"
    with pytest.raises(DatabaseError, match="immutable"):
        db_session.commit()
    db_session.rollback()


def test_unknown_settings_never_become_comparable(db_session, evidence):
    from app.models import (
        CatalogBenchmarkFamily,
        CatalogBenchmarkVersion,
        CatalogMetricDefinition,
        CatalogObservationMetric,
        CatalogProtocol,
    )
    from app.selections.service import comparison_group

    obs = evidence[0][1]
    proto = db_session.get(CatalogProtocol, obs.protocol_id)
    family = db_session.get(CatalogBenchmarkFamily, obs.benchmark_family_id)
    version = db_session.get(CatalogBenchmarkVersion, obs.benchmark_version_id)
    m = db_session.scalar(
        select(CatalogObservationMetric).where(
            CatalogObservationMetric.observation_id == obs.id
        )
    )
    d = db_session.get(CatalogMetricDefinition, m.metric_definition_id)
    p = policy_for("ci_review", "single_call")
    assert comparison_group(obs, proto, family, version, d, m, p)
    for bad in (
        {"effort": {"unknown_reason": "missing"}},
        {"pull_requests": 29},
        {"bugs": 94},
        {"judge": "other"},
    ):
        original = proto.configuration
        proto.configuration = {**original, **bad}
        assert comparison_group(obs, proto, family, version, d, m, p) is None
        proto.configuration = original


def test_source_rank_is_not_invented_from_filtered_results(
    client, db_session, evidence
):
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-source-rank@example.com")
    r = client.get(
        "/execution/v1/candidates",
        params={"task": "ci_review", "mode": "single_call"},
        headers=h,
    ).json()
    assert all(
        i["sourceRank"] is None for i in r["items"]
    )  # fixture reports no source ranks
    assert r["items"][0]["sourceGroupRank"] == 2  # explicitly separate calculated field


def test_runtime_identity_does_not_authorize_other_profiles(
    client, db_session, evidence
):
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-modes@example.com")
    for params in [
        {"task": "other", "mode": "single_call"},
        {"task": "other", "mode": "opencode"},
        {"task": "test_generation", "mode": "opencode", "language": "python"},
        {"task": "test_generation", "mode": "opencode", "language": "node"},
        {"task": "ci_failure_diagnosis", "mode": "opencode"},
        {"task": "ci_failure_diagnosis", "mode": "opencode", "proposeFix": True},
    ]:
        r = client.get("/execution/v1/candidates", params=params, headers=h)
        assert r.status_code == 200 and r.json()["items"] == []


def test_source_supersession_keeps_selection_and_hold(client, db_session, evidence):
    from datetime import UTC, datetime

    from app.catalog.imports.retention import retention_candidates
    from app.models import (
        CatalogImportState,
        CatalogSnapshotLifecycle,
        CatalogSourceSnapshot,
    )
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-refresh@example.com")
    result = create(client, h, pick(*evidence[0]))
    snapshot = db_session.get(CatalogSourceSnapshot, result["snapshotId"])
    newer = CatalogSourceSnapshot(source_id=snapshot.source_id, content_hash="e" * 64)
    db_session.add(newer)
    db_session.flush()
    db_session.add(
        CatalogImportState(source_id=snapshot.source_id, active_snapshot_id=newer.id)
    )
    lifecycle = db_session.get(CatalogSnapshotLifecycle, snapshot.id)
    lifecycle.superseded_at = datetime(2026, 9, 24, tzinfo=UTC)
    db_session.commit()
    assert snapshot.id not in retention_candidates(
        db_session, now=datetime(2028, 9, 24, tzinfo=UTC)
    )
    current = client.get(f"/execution/v1/projects/{result['id']}", headers=h).json()
    assert (
        current["snapshotId"] == snapshot.id
        and current["selectionId"] == result["selectionId"]
    )


def test_cross_project_revision_rejected_even_same_owner(client, db_session, evidence):
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-cross-revision@example.com")
    a = create(client, h, pick(*evidence[0]))
    b = create(client, h, pick(*evidence[1]))
    token = client.post(f"/execution/v1/projects/{b['id']}/ci-token", headers=h).json()[
        "token"
    ]
    r = client.post(
        f"/projects/{b['id']}/ci-runs",
        headers={"X-CI-Token": token},
        json={
            "jenkinsBuildId": "forged",
            "model": "fixture-alpha",
            "tokensIn": 1,
            "tokensOut": 1,
            "gate": "pass",
            "executionRevisionId": a["executionRevisionId"],
        },
    )
    assert r.status_code == 422


def test_preference_edit_creates_revision_without_relabelling_selection(
    client, db_session, evidence
):
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-prefs@example.com")
    a = create(client, h, pick(*evidence[0]))
    pid = a["id"]
    b = client.patch(
        f"/execution/v1/projects/{pid}",
        headers=h,
        json={"reviewPreferences": "Check bounds"},
    ).json()
    assert (
        b["selectionId"] == a["selectionId"]
        and b["executionRevisionId"] != a["executionRevisionId"]
    )
    assert (
        db_session.get(ExecutionRevision, a["executionRevisionId"]).configuration[
            "reviewPreferences"
        ]
        is None
    )
    assert b["configuration"]["reviewPreferences"] == "Check bounds"


def test_selection_must_match_exact_canonical_model(client, db_session, evidence):
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-model-mismatch@example.com")
    choice = pick(evidence[0][0], evidence[1][1])
    assert (
        client.post(
            "/execution/v1/projects",
            headers=h,
            json={"name": "bad", "selection": choice},
        ).status_code
        == 422
    )


def test_python_policy_matches_extra_only():
    from types import SimpleNamespace as N

    from app.selections.service import comparison_group

    p = policy_for("test_generation", "opencode", "python")
    obs = N(
        provenance_status="complete",
        source_snapshot_id=1,
        evaluator_id=1,
        benchmark_version_id=1,
    )
    proto = N(
        configuration={
            "configuration": "Extra",
            "language": "Python",
            "attempts": 1,
            "runner": "fixed",
        },
        configuration_fingerprint="f" * 64,
    )
    family = N(slug="testgeneval")
    version = N(version=p["benchmarkVersion"])
    d = N(key="e_at_1", direction="higher", id=1)
    m = N(
        value=Decimal("30.4"),
        missing_reason=None,
        category=None,
        subset=None,
        aggregation=None,
        sample_size=None,
        denominator=None,
        attempts=1,
    )
    assert comparison_group(obs, proto, family, version, d, m, p)
    assert (
        comparison_group(
            obs,
            proto,
            family,
            version,
            d,
            m,
            policy_for("test_generation", "opencode", "node"),
        )
        is None
    )
    proto.configuration["configuration"] = "Full"
    assert comparison_group(obs, proto, family, version, d, m, p) is None


def test_selection_and_revision_snapshots_are_immutable(client, db_session, evidence):
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError

    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-immutable@example.com")
    a = create(client, h, pick(*evidence[0]))
    for query, identifier in [
        (
            "UPDATE model_selection SET method='benchmark_ranked' WHERE id=:id",
            a["selectionId"],
        ),
        (
            "UPDATE execution_revision SET configuration='{}'::jsonb WHERE id=:id",
            a["executionRevisionId"],
        ),
    ]:
        with pytest.raises(DatabaseError, match="immutable"):
            db_session.execute(text(query), {"id": identifier})
        db_session.rollback()


def test_populated_b6_downgrade_refuses_data_loss(db_session, evidence):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    db_session.commit()
    with pytest.raises(RuntimeError, match="preserve"):
        command.downgrade(cfg, "c6d7e8f9a0b1")
    assert db_session.get(ExecutionRuntime, evidence[0][0].id)


def test_catalog_listing_is_independent_of_disabled_execution(
    client, db_session, evidence
):
    obs = evidence[3][1]
    response = client.get(f"/catalog/v1/observations?modelId={obs.catalog_model_id}")
    assert response.status_code == 200
    assert (
        "runtimeId" not in response.text and "verificationStatus" not in response.text
    )


def test_hold_precedes_database_selection_reference(db_session, evidence):
    from sqlalchemy.exc import DatabaseError

    from app.models import Project, User

    user = User(email="b6-db-hold@example.com", password_hash="placeholder")
    db_session.add(user)
    db_session.flush()
    project = Project(user_id=user.id, name="Hold contract")
    db_session.add(project)
    db_session.flush()
    rt, obs = evidence[0]
    db_session.add(
        ModelSelection(
            project_id=project.id,
            user_id=user.id,
            runtime_id=rt.id,
            catalog_model_id=rt.catalog_model_id,
            observation_id=obs.id,
            snapshot_id=obs.source_snapshot_id,
            method="supported_unranked",
            policy_snapshot=policy_for("ci_review", "single_call"),
        )
    )
    with pytest.raises(DatabaseError, match="hold"):
        db_session.flush()


def test_legacy_token_rotation_cannot_invalidate_an_explicit_token(
    client, db_session, evidence
):
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-token-compat@example.com")
    a = create(client, h, pick(*evidence[0]))
    pid = a["id"]
    token = client.post(f"/execution/v1/projects/{pid}/ci-token", headers=h).json()[
        "token"
    ]
    assert client.post(f"/projects/{pid}/ci-setup/rotate", headers=h).status_code == 422
    assert (
        client.get(
            f"/execution/v1/projects/{pid}/agent-config", headers={"X-CI-Token": token}
        ).status_code
        == 200
    )
    new_token = client.post(
        f"/execution/v1/projects/{pid}/ci-token/rotate", headers=h
    ).json()["token"]
    assert new_token != token
    assert (
        client.get(
            f"/execution/v1/projects/{pid}/agent-config", headers={"X-CI-Token": token}
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"/execution/v1/projects/{pid}/agent-config",
            headers={"X-CI-Token": new_token},
        ).status_code
        == 200
    )


def test_runtime_disable_serializes_with_selection(client, db_session, evidence):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-lock@example.com")
    choice = pick(*evidence[0])
    runtime_id = evidence[0][0].id
    engine = db_session.get_bind()
    db_session.rollback()
    with Session(engine) as disabling, ThreadPoolExecutor(max_workers=1) as pool:
        disabling.execute(
            text("UPDATE execution_runtime SET enabled=false WHERE id=:id"),
            {"id": runtime_id},
        )
        # The concurrent choice can only commit before the disable or reject after
        # it. Here disable already holds the row lock, so selection must reject.
        future = pool.submit(
            client.post,
            "/execution/v1/projects",
            headers=h,
            json={"name": "concurrent", "selection": choice},
        )
        disabling.commit()
        assert future.result(timeout=10).status_code == 422


def test_explicit_history_read_is_owner_and_project_scoped(
    client, db_session, evidence
):
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b6-history-read@example.com")
    other, _ = _register(client, db_session, "b6-history-outsider@example.com")
    a = create(client, h, pick(*evidence[0]))
    pid = a["id"]
    rev = a["executionRevisionId"]
    client.patch(
        f"/execution/v1/projects/{pid}",
        headers=h,
        json={"selection": pick(*evidence[1])},
    )
    path = f"/execution/v1/projects/{pid}/revisions/{rev}"
    assert (
        client.get(path, headers=h).json()["model"]["providerModelId"]
        == "fixture-alpha"
    )
    assert client.get(path, headers=other).status_code == 403
    b = create(client, h, pick(*evidence[1]))
    assert (
        client.get(
            f"/execution/v1/projects/{b['id']}/revisions/{rev}", headers=h
        ).status_code
        == 404
    )


def test_legacy_transition_preserves_token_and_recommendation_reference(
    client, db_session, evidence
):
    from app.catalog.seed import load_seed
    from app.models import Project
    from tests.test_ci import _make_project, _mint_token, _register

    load_seed(db_session)
    h, _ = _register(client, db_session, "b6-transition@example.com")
    pid = _make_project(client, h)
    legacy = db_session.get(Project, pid)
    option_id, baseline_id = legacy.selected_option_id, legacy.baseline_model_id
    token = _mint_token(client, h, pid)
    result = client.patch(
        f"/execution/v1/projects/{pid}",
        headers=h,
        json={"selection": pick(*evidence[0])},
    )
    assert result.status_code == 200, result.text
    selection = db_session.get(ModelSelection, result.json()["selectionId"])
    assert (
        selection.legacy_option_id == option_id
        and selection.legacy_baseline_model_id == baseline_id
    )
    config = client.get(
        f"/execution/v1/projects/{pid}/agent-config", headers={"X-CI-Token": token}
    )
    assert (
        config.status_code == 200
        and config.json()["model"]["providerModelId"] == "fixture-alpha"
    )
    assert result.json()["baselineModelId"] is None
