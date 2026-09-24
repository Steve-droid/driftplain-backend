"""B7 populated contracts, immutable history and owner scoping, no real providers."""

import copy

import pytest
from sqlalchemy import select

from app.models import CiFinding, CiRun, ExecutionRevision, ExecutionRuntime
from app.task_contracts import capability_for
from tests.test_ci import _register
from tests.test_selections import evidence as evidence_fixture


@pytest.fixture
def evidence(db_session):
    return evidence_fixture.__wrapped__(db_session)


def project(
    client,
    db,
    evidence,
    *,
    task="other",
    mode="single_call",
    language=None,
    fix=False,
    config=None,
):
    rt, obs = evidence[0]
    new = ExecutionRuntime(
        **{
            k: getattr(rt, k)
            for k in (
                "catalog_model_id",
                "deployment_id",
                "provider",
                "provider_model_id",
                "auth_mode",
                "credential_env_var",
                "runtime_version",
                "verification_status",
                "verification_ref",
                "verified_at",
                "enabled",
            )
        },
        task=task,
        mode=mode,
        capability=capability_for(task, mode, language, fix),
    )
    db.add(new)
    db.commit()
    h, _ = _register(client, db, "b7-owner@example.com")
    response = client.post(
        "/execution/v1/projects",
        headers=h,
        json={
            "name": "B7 project",
            "selection": {
                "task": task,
                "mode": mode,
                "language": language,
                "proposeFix": fix,
                "runtimeId": new.id,
                "observationId": obs.id,
                "method": "supported_unranked",
            },
            "taskConfiguration": config
            or {
                "label": "Custom report",
                "systemPrompt": "Explain",
                "instructions": "Summarize",
            },
        },
    )
    assert response.status_code == 201, response.text
    p = response.json()
    token = client.post(f"/execution/v1/projects/{p['id']}/ci-token", headers=h).json()[
        "token"
    ]
    return h, {"X-CI-Token": token}, p


def envelope(p, **kw):
    c = p["configuration"]
    policy = c["policy"]
    return {
        "jenkinsBuildId": "b7-1",
        "executionRevisionId": p["executionRevisionId"],
        "model": c["model"]["providerModelId"],
        "tokensIn": 1,
        "tokensOut": 2,
        "gate": "pass",
        "taskResult": {
            "version": 1,
            "kind": "report",
            "task": policy["task"],
            "mode": policy["mode"],
            "language": policy["language"],
            "proposeFix": policy["proposeFix"],
            "executionStatus": "completed",
            "report": {"summary": "Bounded owner report"},
        },
        **kw,
    }


def writable_config():
    return {
        "label": "Custom edits",
        "systemPrompt": "Help",
        "instructions": "Change permitted files",
        "writePaths": ["tests"],
        "validationCommands": [
            {
                "id": "unit",
                "argv": ["pytest", "tests"],
                "environmentImage": "python@sha256:" + "a" * 64,
            }
        ],
    }


def patch_result(p):
    data = envelope(p)
    data["taskResult"].update(
        {
            "kind": "patch",
            "baseCommit": "a" * 40,
            "patch": {
                "sha256": "b" * 64,
                "artifactId": "patch",
                "files": [{"path": "tests/test_new.py", "operation": "added"}],
            },
            "validationStatus": "passed",
            "artifacts": [
                {
                    "id": "patch",
                    "kind": "patch",
                    "path": "result.patch",
                    "sha256": "b" * 64,
                    "sizeBytes": 10,
                },
                {
                    "id": "log",
                    "kind": "validation_log",
                    "path": "pytest.log",
                    "sha256": "c" * 64,
                    "sizeBytes": 20,
                },
            ],
            "validations": [
                {
                    "commandId": "unit",
                    "status": "passed",
                    "executionRevisionId": p["executionRevisionId"],
                    "baseCommit": "a" * 40,
                    "patchSha256": "b" * 64,
                    "exitCode": 0,
                    "durationMs": 50,
                    "logArtifactId": "log",
                    "generatedTestsDiscovered": 1,
                    "generatedTestsExecuted": 1,
                }
            ],
        }
    )
    return data


def test_report_roundtrip_history_and_owner_scope(client, db_session, evidence):
    h, ci, p = project(client, db_session, evidence)
    pid = p["id"]
    path = f"/execution/v1/projects/{pid}"
    assert client.get(path + "/agent-config", headers=ci).status_code == 409
    cfg = client.get(path + "/agent-config?taskContractVersion=1", headers=ci)
    assert cfg.status_code == 200
    assert cfg.json()["taskConfiguration"]["resources"]["maxIterations"] == 1
    edited = client.patch(
        path,
        headers=h,
        json={
            "taskConfiguration": {
                "label": "Changed",
                "systemPrompt": "Explain",
                "instructions": "Now run shell",
            }
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["selectionId"] == p["selectionId"]
    assert (
        edited.json()["configuration"]["capabilityPolicy"]
        == p["configuration"]["capabilityPolicy"]
    )
    old = client.get(path + f"/revisions/{p['executionRevisionId']}", headers=h).json()
    assert old["taskConfiguration"]["instructions"] == "Summarize"
    # Completed old revision remains attributable after edits.
    r = client.post(f"/projects/{pid}/ci-runs", headers=ci, json=envelope(p))
    assert r.status_code == 201, r.text
    assert (
        r.json()["findingsCount"] == 0
        and r.json()["taskResult"]["validationStatus"] == "not_run"
    )
    run_id = r.json()["id"]
    row = db_session.get(CiRun, run_id)
    assert row.task_result["report"]["summary"] == "Bounded owner report"
    assert (
        list(db_session.scalars(select(CiFinding).where(CiFinding.ci_run_id == run_id)))
        == []
    )
    outsider, _ = _register(client, db_session, "b7-outsider@example.com")
    read = path + f"/runs/{run_id}/result"
    assert client.get(read, headers=h).json()["taskResult"] == r.json()["taskResult"]
    assert client.get(read, headers=outsider).status_code == 403
    assert client.get(read).status_code == 401
    assert (
        client.patch(path, headers=outsider, json={"taskConfiguration": {}}).status_code
        == 403
    )
    assert client.get(path + "/runs/99999/result", headers=h).status_code == 404
    from tests.test_selections import create, pick

    second = create(client, h, pick(*evidence[0]))
    assert (
        client.get(
            f"/execution/v1/projects/{second['id']}/runs/{run_id}/result", headers=h
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "change", ["task", "revision", "patch", "command", "missing", "gate", "findings"]
)
def test_forged_or_missing_validation_rejected(client, db_session, evidence, change):
    h, ci, p = project(
        client, db_session, evidence, mode="opencode", config=writable_config()
    )
    data = patch_result(p)
    if change == "task":
        data["taskResult"]["task"] = "test_generation"
        data["taskResult"]["language"] = "python"
    elif change == "revision":
        data["taskResult"]["validations"][0]["executionRevisionId"] += 1
    elif change == "patch":
        data["taskResult"]["validations"][0]["patchSha256"] = "d" * 64
    elif change == "command":
        data["taskResult"]["validations"][0]["commandId"] = "made-up"
    elif change == "missing":
        data["taskResult"]["validations"] = []
        data["taskResult"]["validationStatus"] = "not_run"
    elif change == "gate":
        data["taskResult"]["executionStatus"] = "failed"
    else:
        data["findings"] = [
            {"severity": "low", "category": "style", "file": "x", "message": "fake"}
        ]
    response = client.post(f"/projects/{p['id']}/ci-runs", headers=ci, json=data)
    assert response.status_code == 422, response.text
    assert list(db_session.scalars(select(CiRun))) == []


def test_patch_roundtrip_and_populated_downgrade_guard(client, db_session, evidence):
    from alembic import command
    from alembic.config import Config

    h, ci, p = project(
        client, db_session, evidence, mode="opencode", config=writable_config()
    )
    data = patch_result(p)
    r = client.post(f"/projects/{p['id']}/ci-runs", headers=ci, json=data)
    assert r.status_code == 201, r.text
    assert r.json()["taskResult"]["patch"]["sha256"] == "b" * 64
    db_session.commit()
    with pytest.raises(Exception, match="Cannot discard billing history"):
        command.downgrade(Config("alembic.ini"), "d7e8f9a0b1c2")
    db_session.expire_all()
    assert (
        db_session.get(CiRun, r.json()["id"]).task_result["validationStatus"]
        == "passed"
    )


@pytest.mark.parametrize(
    "task,language,fix",
    [
        ("test_generation", "python", False),
        ("test_generation", "node", False),
        ("ci_failure_diagnosis", None, False),
        ("ci_failure_diagnosis", None, True),
    ],
)
def test_named_task_config_and_result_contracts(
    client, db_session, evidence, task, language, fix
):
    config = (
        writable_config()
        if fix or task == "test_generation"
        else {"instructions": "Explain the failure"}
    )
    config.pop("systemPrompt", None)
    config.pop("label", None)
    if task == "test_generation":
        from tests.agent.test_test_generation import configuration

        config = configuration(language)["taskConfiguration"]
    h, ci, p = project(
        client,
        db_session,
        evidence,
        task=task,
        mode="opencode",
        language=language,
        fix=fix,
        config=config,
    )
    data = patch_result(p) if fix or task == "test_generation" else envelope(p)
    if task == "test_generation":
        from tests.test_test_generation_api import add_evidence

        add_evidence(data, p, language)
    if task == "ci_failure_diagnosis":
        # Successful diagnosis cannot turn the original failed CI build green.
        data["gate"] = "fail"
    path = f"/projects/{p['id']}/ci-runs"
    missing = copy.deepcopy(data)
    del missing["taskResult"]
    assert client.post(path, headers=ci, json=missing).status_code == 422
    if task == "test_generation":
        zero = copy.deepcopy(data)
        zero["taskResult"]["validations"][0]["generatedTestsExecuted"] = 0
        assert client.post(path, headers=ci, json=zero).status_code == 422
    response = client.post(path, headers=ci, json=data)
    assert response.status_code == 201, response.text


def test_config_survives_same_profile_repick_and_legacy_preference_edit(
    client, db_session, evidence
):
    from tests.test_selections import create, pick

    h, _ = _register(client, db_session, "b7-review@example.com")
    p = create(client, h, pick(*evidence[0]))
    path = f"/execution/v1/projects/{p['id']}"
    changed = client.patch(
        path, headers=h, json={"taskConfiguration": {"inputs": {"maxBytes": 12345}}}
    )
    assert changed.status_code == 200
    again = client.patch(path, headers=h, json={"selection": pick(*evidence[1])})
    assert (
        again.json()["configuration"]["taskConfiguration"]["inputs"]["maxBytes"]
        == 12345
    )
    prefs = client.patch(
        f"/projects/{p['id']}",
        headers=h,
        json={"reviewPreferences": "Check boundaries"},
    )
    assert prefs.status_code == 200
    current = client.get(path, headers=h).json()
    assert current["configuration"]["taskConfiguration"]["inputs"]["maxBytes"] == 12345
    old = db_session.get(ExecutionRevision, p["executionRevisionId"])
    assert old.configuration["taskConfiguration"]["inputs"]["maxBytes"] == 262144


def test_result_metadata_is_immutable(client, db_session, evidence):
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError

    _, ci, p = project(client, db_session, evidence)
    r = client.post(f"/projects/{p['id']}/ci-runs", headers=ci, json=envelope(p))
    with pytest.raises(DatabaseError, match="immutable"):
        db_session.execute(
            text("UPDATE ci_run SET task_result=NULL WHERE id=:id"),
            {"id": r.json()["id"]},
        )
    db_session.rollback()


def test_invalid_profile_repick_returns_422(client, db_session, evidence):
    h, _, p = project(client, db_session, evidence)
    response = client.patch(
        f"/execution/v1/projects/{p['id']}",
        headers=h,
        json={
            "selection": {
                "task": "ci_review",
                "mode": "opencode",
                "runtimeId": evidence[0][0].id,
                "observationId": evidence[0][1].id,
                "method": "supported_unranked",
            }
        },
    )
    assert response.status_code == 422
    assert (
        client.get(f"/execution/v1/projects/{p['id']}", headers=h).json()[
            "executionRevisionId"
        ]
        == p["executionRevisionId"]
    )


def test_outside_patch_paths_and_manifest_size_rejected(client, db_session, evidence):
    _, ci, p = project(
        client, db_session, evidence, mode="opencode", config=writable_config()
    )
    for change in ("path", "size"):
        data = patch_result(p)
        if change == "path":
            data["taskResult"]["patch"]["files"][0]["path"] = "src/app.py"
        else:
            data["taskResult"]["artifacts"][0]["sizeBytes"] = 4194304
        assert (
            client.post(
                f"/projects/{p['id']}/ci-runs", headers=ci, json=data
            ).status_code
            == 422
        )


def test_invalid_configuration_rolls_back_selection_and_hold(
    client, db_session, evidence
):
    from app.models import CatalogSnapshotLifecycle, ModelSelection, Project
    from tests.test_selections import pick

    h, uid = _register(client, db_session, "b7-rollback@example.com")
    response = client.post(
        "/execution/v1/projects",
        headers=h,
        json={
            "name": "Bad config",
            "selection": pick(*evidence[0]),
            "taskConfiguration": {"writePaths": ["src"]},
        },
    )
    assert response.status_code == 422
    assert list(db_session.scalars(select(Project).where(Project.user_id == uid))) == []
    assert list(db_session.scalars(select(ModelSelection))) == []
    assert (
        db_session.get(CatalogSnapshotLifecycle, evidence[0][1].source_snapshot_id)
        is None
    )


def test_registry_requires_auth_and_is_not_activation(client, db_session):
    assert client.get("/execution/v1/tasks").status_code == 401
    h, _ = _register(client, db_session, "b7-registry@example.com")
    registry = client.get("/execution/v1/tasks", headers=h).json()
    assert (
        registry["version"] == 1 and len({p["task"] for p in registry["profiles"]}) == 5
    )
    assert list(db_session.scalars(select(ExecutionRuntime))) == []


def test_validation_overrun_remains_reportable_as_failure(client, db_session, evidence):
    _, ci, p = project(
        client, db_session, evidence, mode="opencode", config=writable_config()
    )
    data = patch_result(p)
    data["taskResult"]["validations"][0]["durationMs"] = 130000
    path = f"/projects/{p['id']}/ci-runs"
    assert client.post(path, headers=ci, json=data).status_code == 422
    data["gate"] = "fail"
    data["taskResult"]["validationStatus"] = "failed"
    data["taskResult"]["validations"][0].update(status="failed", exitCode=124)
    assert client.post(path, headers=ci, json=data).status_code == 201
