import pytest
from app.models import ExecutionRuntime
from tests.test_selections import evidence as evidence_fixture
from tests.test_task_contracts_api import project


@pytest.fixture
def evidence(db_session):
    return evidence_fixture.__wrapped__(db_session)


@pytest.mark.parametrize(
    "task,mode", [("ci_review", "single_call"), ("security_analysis", "opencode")]
)
def test_named_setup_exact_images_boundary_and_disabling(
    client, db_session, evidence, monkeypatch, task, mode
):
    h, ci, p = project(
        client,
        db_session,
        evidence,
        task=task,
        mode=mode,
        config={"inputs": {"diff": task == "ci_review"}},
    )
    url = f"/execution/v1/projects/{p['id']}/ci-command"
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "agent_image", "launcher@sha256:" + "a" * 64)
    monkeypatch.setattr(s, "agent_security_image", "scanner@sha256:" + "b" * 64)
    response = client.get(url, headers=h)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executionRevisionId"] == p["executionRevisionId"]
    cmd = body["command"]
    assert "MODELMATCH_EXECUTION_CONFIG=true" in cmd
    assert "docker.sock" not in cmd
    assert "archiveArtifacts" in body["jenkinsStage"]
    assert "--read-only" in cmd and "--cap-drop ALL" in cmd
    if task == "security_analysis":
        assert "scanner@sha256:" in cmd and "launcher@sha256:" not in cmd
        assert "--cpus 2" in cmd and "--pids-limit 64" in cmd
        assert "--diff" not in cmd
    else:
        assert "--diff /inputs/change.diff" in cmd
    assert client.get(url, headers=ci).status_code == 401
    rt = db_session.get(ExecutionRuntime, p["configuration"]["runtimeId"])
    rt.enabled = False
    db_session.commit()
    assert client.get(url, headers=h).status_code == 422


def test_exact_candidate_filters_preserve_identity_and_rank(
    client, db_session, evidence
):
    from tests.test_ci import _register

    h, _ = _register(client, db_session, "b13-filter@example.com")
    url = "/execution/v1/candidates"
    params = {"task": "ci_review", "mode": "single_call"}
    all_rows = client.get(url, params=params, headers=h).json()
    group = all_rows["groups"][0]["id"]
    params["group"] = group
    ranked = client.get(url, params=params, headers=h).json()["items"]
    item = ranked[1]
    exact = client.get(
        url,
        params={
            **params,
            "catalogModelId": item["catalogModelId"],
            "observationId": item["observationId"],
        },
        headers=h,
    ).json()
    assert exact["items"] == [item]
    assert exact["total"] == 1
    wrong = client.get(
        url,
        params={
            **params,
            "catalogModelId": item["catalogModelId"],
            "observationId": ranked[0]["observationId"],
        },
        headers=h,
    ).json()
    assert wrong["items"] == []
    assert (
        client.get(url, params={**params, "catalogModelId": -1}, headers=h).status_code
        == 422
    )


@pytest.mark.parametrize("task", ["ci_review", "security_analysis"])
def test_named_setup_rejects_unpinned_images_and_wrong_inputs(task):
    from app.selections.named_setup import build_named_command

    c = {
        "taskType": task,
        "taskContractVersion": 1,
        "model": {"credentialEnvVar": "OPENAI_API_KEY"},
        "taskConfiguration": {"inputs": {"diff": task == "ci_review"}},
    }
    with pytest.raises(ValueError, match="digests"):
        build_named_command(c, "image:latest", "image:latest")
    c["taskConfiguration"]["inputs"]["files"] = ["src/a.py"]
    with pytest.raises(ValueError, match="diff only"):
        build_named_command(c, "image@sha256:" + "a" * 64, "image@sha256:" + "b" * 64)


def test_historical_named_setup_fails_cleanly():
    from app.selections.named_setup import build_named_command

    with pytest.raises(ValueError, match="historical configuration"):
        build_named_command({"taskType": "ci_review"}, "", "")


def test_named_shell_syntax_and_no_prompt_interpolation():
    import subprocess
    from app.selections.named_setup import build_named_command

    for task in ("ci_review", "security_analysis"):
        command = build_named_command(
            {
                "taskType": task,
                "taskContractVersion": 1,
                "model": {"credentialEnvVar": "OPENAI_API_KEY"},
                "taskConfiguration": {
                    "instructions": "$(touch /tmp/do-not-run)",
                    "inputs": {"diff": task == "ci_review"},
                },
            },
            "review@sha256:" + "a" * 64,
            "scan@sha256:" + "b" * 64,
        )
        assert "touch" not in command
        subprocess.run(["sh", "-n"], input=command, text=True, check=True)
