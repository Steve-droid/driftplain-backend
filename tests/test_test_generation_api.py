"""Immutable named-test evidence and Jenkins dispatch, using only synthetic support."""

import copy

import pytest
from sqlalchemy import select

from app.models import ExecutionRuntime
from tests.agent.test_test_generation import configuration
from tests.test_selections import evidence as evidence_fixture
from tests.test_task_contracts_api import patch_result, project


@pytest.fixture
def evidence(db_session):
    return evidence_fixture.__wrapped__(db_session)


def add_evidence(body, p, language):
    c = p["configuration"]
    r = body["taskResult"]
    path = "tests/test_new.py" if language == "python" else "tests/new.test.cjs"
    r["patch"]["files"][0]["path"] = path
    v = r["validations"][0]
    v["commandId"] = "suite"
    v["testEvidence"] = {
        "profile": c["taskConfiguration"]["testEnvironment"]["profile"],
        "dependencySha256": c["taskConfiguration"]["testEnvironment"][
            "dependencySha256"
        ],
        "generatedPaths": [path],
        "existingTestsDiscovered": 2,
        "existingTestsExecuted": 2,
        "baselineIdentitySha256": "e" * 64,
        "existingIdentitySha256": "e" * 64,
    }
    r["runnerUsage"] = {
        "provider": c["model"]["provider"],
        "profileVersion": c["runtimeVersion"],
        "inputTokens": 1,
        "outputTokens": 2,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "reasoningTokens": 0,
        "reportedTotalTokens": 3,
        "attempts": 1,
        "completedSteps": 1,
        "toolCalls": 1,
    }
    body["cacheReadTokens"] = 0


@pytest.mark.parametrize("language", ["python", "node"])
def test_named_evidence_rejects_forgery_and_keeps_history(
    client, db_session, evidence, language, monkeypatch
):
    cfg = configuration(language)["taskConfiguration"]
    h, ci, p = project(
        client,
        db_session,
        evidence,
        task="test_generation",
        mode="opencode",
        language=language,
        config=cfg,
    )
    body = patch_result(p)
    add_evidence(body, p, language)
    path = f"/projects/{p['id']}/ci-runs"
    mutators = [
        lambda r: r["validations"][0]["testEvidence"].update(
            generatedPaths=["tests/wrong.test.cjs"]
        ),
        lambda r: r["validations"][0]["testEvidence"].update(dependencySha256="f" * 64),
        lambda r: r["validations"][0]["testEvidence"].update(
            existingIdentitySha256="f" * 64
        ),
        lambda r: r["validations"][0]["testEvidence"].update(existingTestsExecuted=1),
        lambda r: r["validations"][0].update(generatedTestsExecuted=2),
        lambda r: r["validations"][0].pop("testEvidence"),
        lambda r: r["validations"][0].update(patchSha256="f" * 64),
        lambda r: r["validations"][0].update(executionRevisionId=99999),
        lambda r: r["validations"][0].update(commandId="injected"),
        lambda r: r["patch"]["files"][0].update(operation="modified"),
        lambda r: r["runnerUsage"].update(profileVersion="wrong"),
    ]
    for mutate in mutators:
        bad = copy.deepcopy(body)
        mutate(bad["taskResult"])
        response = client.post(path, headers=ci, json=bad)
        assert response.status_code == 422, response.text
    created = client.post(path, headers=ci, json=body)
    assert created.status_code == 201, created.text
    assert created.json()["actualCost"] is None
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_image", "launcher@sha256:" + "a" * 64)
    monkeypatch.setattr(settings, "agent_security_image", "editor@sha256:" + "b" * 64)
    url = f"/execution/v1/projects/{p['id']}"
    setup = client.get(url + "/ci-command", headers=h)
    assert setup.status_code == 200, setup.text
    assert "stage('Generate tests')" in setup.json()["jenkinsStage"]
    assert "archiveArtifacts" in setup.json()["jenkinsStage"]
    assert client.get(url + "/ci-command", headers=ci).status_code == 401
    cfg["instructions"] = "Changed instructions"
    edit = client.patch(url, headers=h, json={"taskConfiguration": cfg})
    assert edit.status_code == 200, edit.text
    for runtime in db_session.scalars(
        select(ExecutionRuntime).where(ExecutionRuntime.task == "test_generation")
    ):
        runtime.enabled = False
    db_session.commit()
    body["jenkinsBuildId"] = "old-revision-after-disable"
    assert client.post(path, headers=ci, json=body).status_code == 201
    read = client.get(url + f"/runs/{created.json()['id']}/result", headers=h)
    assert read.status_code == 200
    assert client.get(url + "/ci-command", headers=h).status_code == 422
