import copy

import pytest

from tests.test_selections import evidence as evidence_fixture
from tests.test_task_contracts_api import (
    envelope,
    patch_result,
    project,
    writable_config,
)


@pytest.fixture
def evidence(db_session):
    return evidence_fixture.__wrapped__(db_session)


@pytest.mark.parametrize("mode", ["single_call", "opencode"])
def test_other_usage_bound_to_immutable_revision(client, db_session, evidence, mode):
    headers, ci, p = project(
        client,
        db_session,
        evidence,
        mode=mode,
        config=writable_config() if mode == "opencode" else None,
    )
    body = patch_result(p) if mode == "opencode" else envelope(p)
    usage = {
        "provider": p["configuration"]["model"]["provider"],
        "profileVersion": p["configuration"]["runtimeVersion"],
        "inputTokens": 1,
        "outputTokens": 2,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "reasoningTokens": 0,
    }
    key = "runnerUsage" if mode == "opencode" else "providerUsage"
    if mode == "opencode":
        usage.update(reportedTotalTokens=3, attempts=1, completedSteps=1, toolCalls=1)
    body["taskResult"][key] = usage
    body["cacheReadTokens"] = 0
    url = f"/projects/{p['id']}/ci-runs"
    for change in [
        {"profileVersion": "wrong"},
        {"inputTokens": None},
        {"provider": "google"},
    ]:
        bad = copy.deepcopy(body)
        bad["taskResult"][key].update(change)
        assert client.post(url, headers=ci, json=bad).status_code == 422
    response = client.post(url, headers=ci, json=body)
    assert response.status_code == 201, response.text
    assert response.json()["actualCost"] is None
    config = copy.deepcopy(p["configuration"]["taskConfiguration"])
    config["instructions"] = "Revised"
    updated = client.patch(
        f"/execution/v1/projects/{p['id']}",
        headers=headers,
        json={"taskConfiguration": config},
    )
    assert updated.status_code == 200, updated.text
    body["jenkinsBuildId"] = "after-edit"
    assert client.post(url, headers=ci, json=body).status_code == 201


def test_other_setup_selects_launcher_and_worker_without_prompt_injection():
    from app.selections.other_setup import build_other_command
    from tests.agent.test_other_execution import configuration

    launcher = "ghcr.io/steve-droid/driftplain-agent@sha256:" + "a" * 64
    editor = "ghcr.io/steve-droid/driftplain-agent-security@sha256:" + "b" * 64
    for mode in ("single_call", "opencode"):
        c = configuration(
            mode=mode, **({"write_paths": ["src"]} if mode == "opencode" else {})
        )
        script = build_other_command(c, launcher, editor)
        assert launcher in script and "-m agent" in script
        assert ("AGENT_OTHER_IMAGE=" + editor in script) == (mode == "opencode")
        assert "$(whoami)" not in script and "${BUILD_TAG}" not in script
        assert ("docker.sock" in script) == (mode == "opencode")


def test_owner_scoped_other_command_requires_digest_settings(
    client, db_session, evidence, monkeypatch
):
    from app.config import get_settings

    headers, ci, p = project(client, db_session, evidence)
    url = f"/execution/v1/projects/{p['id']}/ci-command"
    assert client.get(url, headers=ci).status_code == 401
    assert client.get(url, headers=headers).status_code == 409
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "agent_image",
        "ghcr.io/steve-droid/driftplain-agent@sha256:" + "a" * 64,
    )
    monkeypatch.setattr(
        settings,
        "agent_security_image",
        "ghcr.io/steve-droid/driftplain-agent-security@sha256:" + "b" * 64,
    )
    r = client.get(url, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["executionRevisionId"] == p["executionRevisionId"]
    assert r.json()["editorImage"] is None
    assert "-m agent" in r.json()["command"]
