from datetime import UTC, datetime, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from app.billing.rates import register_rate
from app.models import CiRun
from tests.test_review_usage_api import evidence, setup_run
from tests.test_billing import schedule


def test_pinned_rates_edit_inflight_feedback_and_owner(client, db_session, evidence):
    h, ci, p, payload = setup_run(client, db_session, evidence)
    now = datetime.now(UTC)
    rt = p["configuration"]["runtimeId"]
    register_rate(
        db_session,
        rt,
        now - timedelta(days=1),
        now + timedelta(days=30),
        schedule(input=2, output=3, cache_read=1, cache_write_5m=4, cache_write_1h=6),
    )
    db_session.commit()
    p2 = client.patch(
        f"/execution/v1/projects/{p['id']}",
        headers=h,
        json={"reviewPreferences": "new preference"},
    ).json()
    payload["executionRevisionId"] = p2["executionRevisionId"]
    payload["taskResult"]["providerUsage"].update(
        cacheWrite5MTokens=10,
        cacheWrite1HTokens=20,
        serviceTier="standard",
        reportedModelId=payload["model"],
    )
    register_rate(
        db_session,
        rt,
        now,
        now + timedelta(days=30),
        schedule(
            input=200,
            output=300,
            cache_read=100,
            cache_write_5m=400,
            cache_write_1h=600,
        ),
    )
    db_session.commit()
    client.patch(
        f"/execution/v1/projects/{p['id']}",
        headers=h,
        json={"reviewPreferences": "later preference"},
    )
    other_runtime, other_observation = evidence[1]
    repick = client.patch(
        f"/execution/v1/projects/{p['id']}",
        headers=h,
        json={
            "selection": {
                "task": "ci_review",
                "mode": "single_call",
                "runtimeId": other_runtime.id,
                "observationId": other_observation.id,
                "method": "supported_unranked",
            }
        },
    )
    assert repick.status_code == 200, repick.text
    assert (
        repick.json()["configuration"]["model"]["providerModelId"] != payload["model"]
    )
    created = client.post(f"/projects/{p['id']}/ci-runs", headers=ci, json=payload)
    assert created.status_code == 201, created.text
    url = f"/projects/{p['id']}/usage/v1"
    response = client.get(url, headers=h)
    assert response.status_code == 200, response.text
    data = response.json()
    assert Decimal(data["totals"]["completeCost"]) == Decimal("0.000215")
    run = data["runs"][0]
    assert run["executionRevisionId"] == p2["executionRevisionId"]
    assert run["model"] == payload["model"]
    assert run["billing"]["rateSnapshot"]["rateVersion"] == "fixture-1"
    assert data["feedback"] == {"accepted": 0, "rejected": 0, "rated": 0, "total": 0}
    assert db_session.get(CiRun, run["id"]).actual_cost is None
    from tests.test_ci import _register

    other, _ = _register(client, db_session, "b15-other@example.com")
    assert client.get(url, headers=other).status_code == 403
    db_session.commit()
    for sql in [
        "UPDATE ci_run SET billing=NULL WHERE id=:id",
        "UPDATE execution_revision SET billing_snapshot=NULL WHERE id=:revision",
        "UPDATE billing_rate SET schedule='{}'",
        "DELETE FROM billing_rate",
    ]:
        with pytest.raises(DatabaseError, match="immutable"):
            db_session.execute(
                text(sql), {"id": run["id"], "revision": p2["executionRevisionId"]}
            )
            db_session.commit()
        db_session.rollback()


def test_unpriced_aborted_and_historical_runs_not_zero(client, db_session, evidence):
    h, ci, p, body = setup_run(client, db_session, evidence)
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
    assert (
        client.post(f"/projects/{p['id']}/ci-runs", headers=ci, json=body).status_code
        == 201
    )
    legacy = CiRun(
        project_id=p["id"],
        jenkins_build_id="legacy",
        tokens_in=10,
        tokens_out=20,
        actual_cost=Decimal("1.234567"),
        savings=Decimal("4.56"),
    )
    db_session.add(legacy)
    db_session.commit()
    d = client.get(f"/projects/{p['id']}/usage/v1", headers=h).json()
    assert d["totals"]["completeCost"] is None and d["totals"]["partialCost"] is None
    assert d["totals"]["unavailableRuns"] == 1 and d["totals"]["legacyRuns"] == 1
    old = next(r for r in d["runs"] if r["jenkinsBuildId"] == "legacy")
    assert all(r["billing"]["knownCost"] is None for r in d["runs"])
    assert old["billing"]["basis"] == "legacy_input_output_only"
    assert Decimal(old["legacyCost"]) == Decimal("1.234567")
    assert db_session.get(CiRun, legacy.id).savings == Decimal("4.56")


def test_feedback_cannot_change_cost_and_paging(client, db_session, evidence):
    h, ci, p, body = setup_run(client, db_session, evidence)
    body["findings"] = [
        {
            "severity": "low",
            "category": "style",
            "file": "x.py",
            "line": 1,
            "message": "Review this",
        }
    ]
    posted = client.post(f"/projects/{p['id']}/ci-runs", headers=ci, json=body)
    assert posted.status_code == 201, posted.text
    url = f"/projects/{p['id']}/usage/v1"
    before = client.get(url, headers=h).json()
    fid = client.get(
        f"/projects/{p['id']}/runs/{posted.json()['id']}/findings", headers=h
    ).json()["findings"][0]["id"]
    assert (
        client.post(
            f"/findings/{fid}/feedback", headers=h, json={"verdict": "reject"}
        ).status_code
        == 200
    )
    after = client.get(url, headers=h).json()
    assert before["totals"] == after["totals"]
    assert after["feedback"] == {"accepted": 0, "rejected": 1, "rated": 1, "total": 1}
    assert client.get(url + "?offset=1", headers=h).json()["runs"] == []
    assert client.get(url + "?range=bad", headers=h).status_code == 422
    assert client.get(url + "?limit=101", headers=h).status_code == 422


def test_named_patch_metadata_survives_usage_read(client, db_session, evidence):
    from tests.test_task_contracts_api import project, patch_result
    from tests.test_test_generation_api import add_evidence
    from tests.agent.test_test_generation import configuration

    h, ci, p = project(
        client,
        db_session,
        evidence,
        task="test_generation",
        mode="opencode",
        language="python",
        config=configuration("python")["taskConfiguration"],
    )
    body = patch_result(p)
    add_evidence(body, p, "python")
    response = client.post(f"/projects/{p['id']}/ci-runs", headers=ci, json=body)
    assert response.status_code == 201, response.text
    result = client.get(f"/projects/{p['id']}/usage/v1", headers=h).json()["runs"][0]
    assert result["task"] == "test_generation"
    assert result["taskResult"]["patch"] == body["taskResult"]["patch"]
    assert result["taskResult"]["validations"][0]["testEvidence"]["generatedPaths"] == [
        "tests/test_new.py"
    ]
    assert result["billing"]["status"] == "unavailable"


def test_snapshot_expiry_is_unavailable_and_new_rates_do_not_reprice_history(
    client, db_session, evidence, monkeypatch
):
    from app.models import ExecutionRevision
    from app.billing import rates

    h, ci, p, body = setup_run(client, db_session, evidence)
    now = datetime.now(UTC)
    rt = p["configuration"]["runtimeId"]
    schedule_row = register_rate(
        db_session,
        rt,
        now - timedelta(days=2),
        now - timedelta(days=1),
        schedule(input=2, output=3, cache_read=1),
    )
    db_session.commit()

    # Simulate a revision made during the rate's actual validity, then a late report.
    class Earlier(datetime):
        @classmethod
        def now(cls, tz=None):
            return now - timedelta(days=1, hours=1)

    monkeypatch.setattr(rates, "datetime", Earlier)
    changed = client.patch(
        f"/execution/v1/projects/{p['id']}",
        headers=h,
        json={"reviewPreferences": "New revision"},
    ).json()
    revision = db_session.get(ExecutionRevision, changed["executionRevisionId"])
    assert revision.billing_snapshot["rateId"] == schedule_row.id
    body["executionRevisionId"] = revision.id
    body["taskResult"]["providerUsage"]["serviceTier"] = "standard"
    posted = client.post(f"/projects/{p['id']}/ci-runs", headers=ci, json=body)
    assert posted.status_code == 201, posted.text
    r = client.get(f"/projects/{p['id']}/usage/v1", headers=h).json()["runs"][0]
    assert r["billing"]["knownCost"] is None
    assert "pinned_rate_schedule_expired_at_ingestion" in r["billing"]["reasons"]
