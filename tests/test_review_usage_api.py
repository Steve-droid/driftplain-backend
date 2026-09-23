"""B8 PostgreSQL/API evidence with synthetic runtimes; no activation or live calls."""

import copy

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.models import CiRun
from tests.test_task_contracts_api import evidence as evidence_fixture
from tests.test_task_contracts_api import project


@pytest.fixture
def evidence(db_session):
    return evidence_fixture.__wrapped__(db_session)


def setup_run(client, db, evidence):
    h, ci, p = project(
        client,
        db,
        evidence,
        task="ci_review",
        config={"instructions": "Review the diff"},
    )
    usage = {
        "version": 1,
        "provider": "anthropic",
        "profileVersion": p["configuration"]["runtimeVersion"],
        "inputTokens": 10,
        "outputTokens": 5,
        "cacheReadTokens": 20,
        "cacheWriteTokens": 30,
        "reasoningTokens": 4,
        "generationRequests": 1,
        "transportRetries": 0,
    }
    payload = {
        "jenkinsBuildId": "b8-fixture",
        "executionRevisionId": p["executionRevisionId"],
        "model": p["configuration"]["model"]["providerModelId"],
        "tokensIn": 10,
        "tokensOut": 5,
        "cacheReadTokens": 20,
        "gate": "pass",
        "findings": [],
        "taskResult": {
            "version": 1,
            "kind": "findings",
            "task": "ci_review",
            "mode": "single_call",
            "executionStatus": "completed",
            "providerUsage": usage,
        },
    }
    return h, ci, p, payload


def test_usage_roundtrip_owner_history_and_immutability(client, db_session, evidence):
    h, ci, p, payload = setup_run(client, db_session, evidence)
    url = f"/projects/{p['id']}/ci-runs"
    r = client.post(url, headers=ci, json=payload)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["actualCost"] is None and data["savings"] is None
    assert data["taskResult"]["providerUsage"]["cacheWriteTokens"] == 30
    result_url = f"/execution/v1/projects/{p['id']}/runs/{data['id']}/result"
    assert (
        client.get(result_url, headers=h).json()["taskResult"]["providerUsage"][
            "reasoningTokens"
        ]
        == 4
    )
    from tests.test_ci import _register

    other, _ = _register(client, db_session, "b8-other@example.com")
    assert client.get(result_url, headers=other).status_code in (403, 404)
    db_session.commit()
    with pytest.raises(DatabaseError, match="immutable"):
        db_session.execute(
            text(
                "UPDATE ci_run SET task_result = jsonb_set(task_result, '{providerUsage,inputTokens}', '999') WHERE id=:id"
            ),
            {"id": data["id"]},
        )
        db_session.commit()
    db_session.rollback()
    assert (
        db_session.get(CiRun, data["id"]).task_result["providerUsage"]["inputTokens"]
        == 10
    )


def test_usage_binding_unknowns_and_failed_attempts(client, db_session, evidence):
    _, ci, p, original = setup_run(client, db_session, evidence)
    url = f"/projects/{p['id']}/ci-runs"
    for field, value in [
        ("profileVersion", "wrong"),
        ("provider", "openai"),
        ("transportRetries", 1),
        ("inputTokens", -1),
        ("inputTokens", None),
        ("reasoningTokens", 6),
        ("rawResponse", "unwanted"),
    ]:
        body = copy.deepcopy(original)
        body["taskResult"]["providerUsage"][field] = value
        r = client.post(url, headers=ci, json=body)
        assert r.status_code == 422, (field, r.text)
    body = copy.deepcopy(original)
    body.update(tokensIn=0, tokensOut=0, cacheReadTokens=None, gate="fail")
    body["taskResult"].update(
        executionStatus="timed_out",
        executionReason="Provider timeout",
        providerUsage={
            "provider": "anthropic",
            "profileVersion": p["configuration"]["runtimeVersion"],
            "generationRequests": 1,
        },
    )
    r = client.post(url, headers=ci, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["taskResult"]["providerUsage"]["inputTokens"] is None


def test_pending_matrix_does_not_enable_or_seed_any_runtime(client, db_session):
    from sqlalchemy import select

    from app.models import ExecutionRuntime
    from app.review_contracts import REVIEW_PROFILES
    from tests.test_ci import _register

    assert len(REVIEW_PROFILES) == 6
    assert all(p.verification_status == "pending" for p in REVIEW_PROFILES.values())
    assert list(db_session.scalars(select(ExecutionRuntime))) == []
    h, _ = _register(client, db_session, "b8-empty@example.com")
    r = client.get(
        "/execution/v1/candidates?task=ci_review&mode=single_call", headers=h
    )
    assert r.status_code == 200 and r.json()["items"] == []
