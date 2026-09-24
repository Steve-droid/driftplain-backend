"""B10 offline custom execution; fixtures never activate production profiles."""

import json

import pytest

from agent.config import AgentConfig
from agent.errors import AgentConfigError, CeilingExceeded
from agent.other_execution import (
    assemble_inputs,
    resolve_other_profile,
    run_single_call,
)
from agent.review_providers import ReviewProvider
from app.other_contracts import OTHER_PROFILES
from app.selections.policy import policy_for
from app.task_contracts import (
    TaskConfiguration,
    TaskResult,
    configure,
    validate_result_configuration,
)
from tests.agent.test_review_providers import Transport, response
from tests.agent.test_task_contracts import config as base_config


def configuration(model="gpt-5.6-sol", mode="single_call", **options):
    p = OTHER_PROFILES[(mode, model)]
    cfg = TaskConfiguration(
        label="Custom",
        system_prompt="Explain literally $(whoami)",
        instructions="Summarize ${BUILD_TAG}",
        **options,
    )
    return {
        **base_config(),
        "executionMode": mode,
        "capability": "custom_" + mode,
        "runtimeVersion": p.version,
        "policy": policy_for("other", mode),
        "model": {
            "name": model,
            "provider": p.provider,
            "providerModelId": model,
            "authMode": "api_key",
            "credentialEnvVar": p.credential_env,
        },
        **configure("other", mode, cfg),
    }


@pytest.mark.parametrize("mode", ["single_call", "opencode"])
@pytest.mark.parametrize(
    "model", ["gpt-5.6-sol", "claude-sonnet-5", "gemini-3.7-flash"]
)
def test_exact_pending_matrix(mode, model):
    c = configuration(
        model, mode, **({"write_paths": ["src"]} if mode == "opencode" else {})
    )
    with pytest.raises(AgentConfigError, match="pending"):
        resolve_other_profile(c)
    c["executionMode"] = "single_call" if mode == "opencode" else "opencode"
    with pytest.raises(AgentConfigError, match="Unsupported"):
        resolve_other_profile(c)


@pytest.mark.parametrize(
    "model", ["gpt-5.6-sol", "claude-sonnet-5", "gemini-3.7-flash"]
)
def test_single_request_generic_report_literal_prompts(model, tmp_path):
    c = configuration(model)
    p = OTHER_PROFILES[("single_call", model)]
    transport = Transport(
        response(p.provider, model, '{"summary":"Done","nextSteps":["Review"]}')
    )
    result, code = run_single_call(
        c,
        AgentConfig(workspace=str(tmp_path)),
        diff="diff",
        client=ReviewProvider(p, transport=transport),
    )
    assert code == 0 and result["findings"] == []
    assert result["taskResult"]["kind"] == "report"
    assert result["taskResult"]["validationStatus"] == "not_run"
    assert len(transport.calls) == 1
    sent = json.dumps(transport.calls)
    assert "$(whoami)" in sent and "${BUILD_TAG}" in sent
    assert "CWE" not in sent
    validate_result_configuration(
        TaskResult.model_validate(result["taskResult"]), c, 9, "pass"
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("not json", 2),
        ('{"findings":[]}', 2),
        ('{"summary":"ok","command":"touch x"}', 2),
    ],
)
def test_malformed_output_never_executes(text, expected, tmp_path):
    c = configuration()
    p = OTHER_PROFILES[("single_call", "gpt-5.6-sol")]
    result, code = run_single_call(
        c,
        AgentConfig(workspace=str(tmp_path)),
        diff="d",
        client=ReviewProvider(
            p, transport=Transport(response(p.provider, p.model, text))
        ),
    )
    assert code == expected and result["gate"] == "fail"
    assert not (tmp_path / "x").exists()


def test_selected_files_artifacts_bounded_and_no_links(tmp_path):
    repo, artifacts = tmp_path / "repo", tmp_path / "artifacts"
    repo.mkdir()
    artifacts.mkdir()
    (repo / "a.txt").write_text("selected")
    (repo / "unselected").write_text("never upload")
    (artifacts / "build-log").write_text("log")
    cfg = TaskConfiguration(
        inputs={"diff": False, "files": ["a.txt"], "artifacts": ["build-log"]}
    )
    text = assemble_inputs(cfg, repo, None, artifacts)
    assert "selected" in text and "log" in text and "never upload" not in text
    (repo / "a.txt").unlink()
    (repo / "a.txt").symlink_to(artifacts / "build-log")
    with pytest.raises(AgentConfigError):
        assemble_inputs(cfg, repo, None, artifacts)
    (repo / "a.txt").unlink()
    (repo / "a.txt").write_text("x" * 65537)
    with pytest.raises(CeilingExceeded):
        assemble_inputs(cfg, repo, None, artifacts)


def test_missing_usage_refusal_and_timeout(tmp_path):
    p = OTHER_PROFILES[("single_call", "gpt-5.6-sol")]
    body = response(p.provider, p.model, '{"summary":"ok"}')
    body.pop("usage")
    result, code = run_single_call(
        configuration(),
        AgentConfig(workspace=str(tmp_path)),
        diff="d",
        client=ReviewProvider(p, transport=Transport(body)),
    )
    assert code == 4 and result["gate"] == "fail"
    body = response(p.provider, p.model)
    body["output"][0]["content"] = [{"type": "refusal"}]
    result, code = run_single_call(
        configuration(),
        AgentConfig(workspace=str(tmp_path)),
        diff="d",
        client=ReviewProvider(p, transport=Transport(body)),
    )
    assert code == 3 and result["taskResult"]["executionStatus"] == "refused"

    def timeout(**kw):
        raise TimeoutError()

    result, code = run_single_call(
        configuration(),
        AgentConfig(workspace=str(tmp_path)),
        diff="d",
        client=ReviewProvider(p, transport=timeout),
    )
    assert code == 124 and result["taskResult"]["executionStatus"] == "timed_out"


@pytest.mark.parametrize("mode", ["single_call", "opencode"])
def test_explicit_cli_dispatch_and_post_exact_model_revision(
    mode, monkeypatch, capsys, tmp_path
):
    from dataclasses import replace

    from agent.__main__ import main
    from agent.security_stream import SecurityStream
    from tests.agent.test_other_workspace import git

    c = configuration(
        mode=mode,
        inputs={"diff": False},
        **({"write_paths": ["src"]} if mode == "opencode" else {}),
    )
    p = OTHER_PROFILES[(mode, "gpt-5.6-sol")]
    monkeypatch.setitem(
        OTHER_PROFILES, (mode, p.model), replace(p, verification_status="verified")
    )
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "src").mkdir()
    (root / "src" / "a").write_text("base")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=f@example.test",
        "commit",
        "-qm",
        "base",
    )
    if mode == "single_call":
        monkeypatch.setattr(
            "agent.other_execution.ReviewProvider",
            lambda profile, **kw: ReviewProvider(
                profile,
                transport=Transport(
                    response(p.provider, p.model, '{"summary":"Report"}')
                ),
            ),
        )
    else:

        class Runner:
            def run(self, work, *args):
                (work.path / "src" / "a").write_text("updated")
                return SecurityStream(
                    text='{"summary":"Patched"}',
                    steps=1,
                    counts={
                        "input": 1,
                        "output": 1,
                        "reasoning": 0,
                        "cache_read": 0,
                        "cache_write": 0,
                        "total": 2,
                    },
                )

        monkeypatch.setattr("agent.other_opencode.DockerEditor", lambda image: Runner())
    calls = []

    def request(method, url, token, timeout, body=None):
        calls.append((method, url, body))
        return c if method == "GET" else {"id": 1}

    monkeypatch.setattr("agent.remote._request", request)
    for key, value in {
        "MODELMATCH_EXECUTION_CONFIG": "true",
        "MODELMATCH_API_URL": "https://example.test",
        "MODELMATCH_PROJECT_ID": "7",
        "MODELMATCH_CI_TOKEN": "fixture-token",
        "MODELMATCH_POST_RESULT": "true",
        "MODELMATCH_BUILD_ID": "b10-fixture",
        "AGENT_IMAGE_TASK": "review",
        "AGENT_WORKSPACE": str(root),
        "AGENT_BASE_COMMIT": git(root, "rev-parse", "HEAD"),
        "AGENT_OUTPUT_DIR": str(tmp_path / "out"),
    }.items():
        monkeypatch.setenv(key, value)
    assert main([]) == 0
    assert len(calls) == 2 and "/execution/v1/" in calls[0][1]
    assert calls[1][2]["model"] == p.model and calls[1][2]["executionRevisionId"] == 9
    r = json.loads(capsys.readouterr().out)
    assert r["taskResult"]["kind"] == ("report" if mode == "single_call" else "patch")
    assert (root / "src" / "a").read_text() == "base"
    monkeypatch.setenv("AGENT_IMAGE_TASK", "security")
    assert main([]) == 4
