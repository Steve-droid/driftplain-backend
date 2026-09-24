import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select, text
from app.models import ExecutionRuntime
from tests.test_selections import evidence as evidence_fixture
from tests.test_task_contracts_api import project, envelope


@pytest.fixture
def evidence(db_session):
    return evidence_fixture.__wrapped__(db_session)


def setup(client, db_session, evidence):
    return project(
        client,
        db_session,
        evidence,
        task="ci_failure_diagnosis",
        mode="opencode",
        config={
            "inputs": {"diff": False},
            "diagnosis": {"stage": "Unit tests", "logArtifact": "upstream.log"},
        },
    )


def claim_body(p):
    return {
        "executionRevisionId": p["executionRevisionId"],
        "failure": {
            "buildId": "build-12",
            "stage": "Unit tests",
            "commit": "a" * 40,
            "exitStatus": 1,
            "originalStatus": "FAILURE",
            "logExcerpt": "TOKEN=privatevalue\nTest failed",
        },
    }


def test_claim_once_before_invocation_and_immutable(client, db_session, evidence):
    h, ci, p = setup(client, db_session, evidence)
    url = f"/execution/v1/projects/{p['id']}/failure-claims"
    b = claim_body(p)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: client.post(url, headers=ci, json=b), range(2))
        )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert sorted(r.json()["claimed"] for r in responses) == [False, True]
    winner = next(r.json() for r in responses if r.json()["claimed"])
    assert client.post(url, headers=h, json=b).status_code == 401
    assert client.post(url, headers={"X-CI-Token": "wrong"}, json=b).status_code == 401
    assert "privatevalue" not in str(
        db_session.execute(text("select context from diagnosis_claim")).scalar()
    )
    with pytest.raises(Exception):
        db_session.execute(text("update diagnosis_claim set build_id='changed'"))
        db_session.commit()
    db_session.rollback()
    # Config edits cannot provide a new claim for the same upstream build/stage.
    edit = client.patch(
        f"/execution/v1/projects/{p['id']}",
        headers=h,
        json={
            "taskConfiguration": {
                **p["configuration"]["taskConfiguration"],
                "instructions": "More detail",
            }
        },
    )
    assert edit.status_code == 200
    b["executionRevisionId"] = edit.json()["executionRevisionId"]
    assert client.post(url, headers=ci, json=b).json()["claimed"] is False
    body = envelope(p, gate="fail", jenkinsBuildId="build-12")
    body["taskResult"].update(
        baseCommit="a" * 40,
        failure={**claim_body(p)["failure"], "claimId": winner["claimId"]},
        report={
            "summary": "Likely failure",
            "cause": "unknown",
            "uncertainty": "Unknown root cause",
            "nextSteps": ["Investigate"],
            "noPatchReason": "Insufficient evidence",
        },
    )
    raw = b"TOKEN=[REDACTED]\nTest failed"
    body["taskResult"]["report"]["evidenceArtifactIds"] = ["failure-log"]
    body["taskResult"]["artifacts"] = [
        {
            "id": "failure-log",
            "kind": "other",
            "path": "failure.log",
            "sizeBytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    ]
    body["taskResult"]["runnerUsage"] = {
        "provider": p["configuration"]["model"]["provider"],
        "profileVersion": p["configuration"]["runtimeVersion"],
        "inputTokens": 1,
        "outputTokens": 2,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "reasoningTokens": 0,
        "reportedTotalTokens": 3,
        "attempts": 1,
        "completedSteps": 1,
        "toolCalls": 0,
    }
    body["cacheReadTokens"] = 0
    path = f"/projects/{p['id']}/ci-runs"
    bad = copy.deepcopy(body)
    bad["taskResult"]["failure"]["claimId"] = "f" * 32
    assert client.post(path, headers=ci, json=bad).status_code == 422
    bad = copy.deepcopy(body)
    bad["gate"] = "pass"
    assert client.post(path, headers=ci, json=bad).status_code == 422
    good = client.post(path, headers=ci, json=body)
    assert good.status_code == 201, good.text
    read = client.get(
        f"/execution/v1/projects/{p['id']}/runs/{good.json()['id']}/result", headers=h
    )
    assert read.status_code == 200
    assert read.json()["taskResult"]["failure"]["originalStatus"] == "FAILURE"
    assert "privatevalue" not in read.text


@pytest.mark.parametrize(
    "changes",
    [
        {"originalStatus": "SUCCESS"},
        {"originalStatus": "ABORTED"},
        {"stage": "Driftplain failure diagnosis"},
        {"logExcerpt": ""},
        {"exitStatus": 0},
    ],
)
def test_ineligible_failure_cannot_claim(client, db_session, evidence, changes):
    h, ci, p = setup(client, db_session, evidence)
    b = claim_body(p)
    b["failure"].update(changes)
    assert (
        client.post(
            f"/execution/v1/projects/{p['id']}/failure-claims", headers=ci, json=b
        ).status_code
        == 422
    )


def test_owner_scope_fix_reselection_setup_and_downgrade_guard(
    client, db_session, evidence, monkeypatch
):
    from alembic import command
    from alembic.config import Config
    from tests.test_ci import _register
    from app.config import get_settings

    h, ci, p = setup(client, db_session, evidence)
    root = f"/execution/v1/projects/{p['id']}"
    other, _ = _register(client, db_session, "b12-other@example.com")
    assert client.get(root, headers=other).status_code == 403
    assert client.get(root + "/ci-command", headers=other).status_code == 403
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_image", "launcher@sha256:" + "a" * 64)
    monkeypatch.setattr(settings, "agent_security_image", "editor@sha256:" + "b" * 64)
    setup_result = client.get(root + "/ci-command", headers=h)
    assert setup_result.status_code == 200, setup_result.text
    assert "DRIFTPLAIN_UPSTREAM_EXIT_STATUS" in setup_result.json()["command"]
    assert "Original upstream stage failed" in setup_result.json()["jenkinsStage"]
    selection = p["configuration"]["policy"]
    rt, obs = evidence[0]
    diagnosis_rt = db_session.scalar(
        select(ExecutionRuntime).where(ExecutionRuntime.task == "ci_failure_diagnosis")
    )
    changed = client.patch(
        root,
        headers=h,
        json={
            "selection": {
                "task": "ci_failure_diagnosis",
                "mode": "opencode",
                "proposeFix": True,
                "runtimeId": diagnosis_rt.id,
                "observationId": obs.id,
                "method": "supported_unranked",
            },
            "taskConfiguration": {
                "inputs": {"diff": False},
                "writePaths": ["src/app.py"],
                "diagnosis": {"stage": "Unit tests", "logArtifact": "upstream.log"},
            },
        },
    )
    assert changed.status_code == 422  # read-only runtime cannot become repair support
    assert client.post(root + "/failure-claims", headers=ci, json=claim_body(p)).json()[
        "claimed"
    ]
    with pytest.raises(Exception, match="Cannot discard diagnosis invocation history"):
        command.downgrade(Config("alembic.ini"), "e8f9a0b1c2d3")
    assert (
        db_session.execute(text("select count(*) from diagnosis_claim")).scalar() == 1
    )
