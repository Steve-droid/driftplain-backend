"""Synthetic runtime fixtures prove persistence, never production enablement."""

import copy

import pytest
from sqlalchemy import select

from app.models import CiRun
from tests.test_selections import evidence as evidence_fixture
from tests.test_task_contracts_api import envelope, project


@pytest.fixture
def evidence(db_session):
    return evidence_fixture.__wrapped__(db_session)


def test_runner_usage_roundtrip_and_revision_binding(client, db_session, evidence):
    headers, ci, p = project(
        client,
        db_session,
        evidence,
        task="security_analysis",
        mode="opencode",
        config={"inputs": {"diff": False}},
    )
    path = f"/projects/{p['id']}/ci-runs"
    c = p["configuration"]
    payload = envelope(p)
    payload["cacheReadTokens"] = 4
    payload["taskResult"].update(
        kind="findings",
        report=None,
        runnerUsage={
            "version": 1,
            "provider": c["model"]["provider"],
            "profileVersion": c["runtimeVersion"],
            "inputTokens": 1,
            "outputTokens": 2,
            "reasoningTokens": 3,
            "cacheReadTokens": 4,
            "cacheWriteTokens": 5,
            "reportedTotalTokens": 15,
            "attempts": 1,
            "completedSteps": 1,
            "toolCalls": 1,
        },
    )
    for field, value in [
        ("profileVersion", "wrong"),
        ("provider", "google"),
        ("reportedTotalTokens", 2),
        ("billingComplete", True),
        ("transportRetries", 0),
        ("completedSteps", 100),
    ]:
        bad = copy.deepcopy(payload)
        bad["taskResult"]["runnerUsage"][field] = value
        assert client.post(path, headers=ci, json=bad).status_code == 422
    bad = copy.deepcopy(payload)
    bad["tokensOut"] = 999
    assert client.post(path, headers=ci, json=bad).status_code == 422
    response = client.post(path, headers=ci, json=payload)
    assert response.status_code == 201, response.text
    row = db_session.scalar(select(CiRun).where(CiRun.id == response.json()["id"]))
    assert row.task_result["runnerUsage"]["reasoningTokens"] == 3
    assert row.task_result["runnerUsage"]["billingComplete"] is False
    assert row.actual_cost is None
    result_path = f"/execution/v1/projects/{p['id']}/runs/{row.id}/result"
    assert client.get(result_path, headers=headers).status_code == 200
    edited = client.patch(
        f"/execution/v1/projects/{p['id']}",
        headers=headers,
        json={
            "taskConfiguration": {
                "instructions": "New config",
                "inputs": {"diff": False},
            }
        },
    )
    assert edited.status_code == 200
    payload["jenkinsBuildId"] = "old-revision-failed"
    payload["taskResult"].update(
        executionStatus="timed_out", executionReason="Fixture timeout"
    )
    payload["taskResult"]["runnerUsage"]["reportedTotalTokens"] = None
    payload["gate"] = "fail"
    assert client.post(path, headers=ci, json=payload).status_code == 201
