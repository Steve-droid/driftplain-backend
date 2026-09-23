"""B9 uses synthetic subprocess events, never provider connections."""

import copy
import json
import sys
import time
from dataclasses import replace

import pytest

from agent.config import AgentConfig
from agent.errors import AgentConfigError
from agent.security_execution import resolve_security_profile, run_security_execution
from agent.security_stream import run_stream
from app.security_contracts import SECURITY_PROFILES
from app.selections.policy import policy_for
from app.task_contracts import (
    TaskConfiguration,
    TaskResult,
    configure,
    validate_result_configuration,
)
from tests.agent.test_task_contracts import config as base_config


def configuration(model="gpt-5.6-sol"):
    p = SECURITY_PROFILES[model]
    return {
        **base_config(),
        "taskType": "security_analysis",
        "executionMode": "opencode",
        "capability": "security_analysis",
        "runtimeVersion": p.version,
        "policy": policy_for("security_analysis", "opencode"),
        "model": {
            "name": model,
            "provider": p.provider,
            "providerModelId": model,
            "authMode": "api_key",
            "credentialEnvVar": p.credential_env,
        },
        **configure(
            "security_analysis", "opencode", TaskConfiguration(inputs={"diff": False})
        ),
    }


def events(text='{"results":[]}', **tokens):
    return [
        {"type": "step_start", "part": {"id": "start1"}},
        {"type": "text", "part": {"id": "text1", "text": text}},
        {
            "type": "step_finish",
            "part": {
                "id": "finish1",
                "reason": "stop",
                "tokens": {
                    "input": 10,
                    "output": 5,
                    "reasoning": 3,
                    "cache": {"read": 2, "write": 1},
                    "total": 21,
                    **tokens,
                },
            },
        },
    ]


def stream(tmp_path, data, **limits):
    script = tmp_path / "runner.py"
    script.write_text(
        "import sys\n"
        + "\n".join(f"print({json.dumps(json.dumps(e))}, flush=True)" for e in data)
    )
    return run_stream(
        [sys.executable, str(script)],
        str(tmp_path),
        env={},
        max_seconds=2,
        max_tokens=100,
        max_steps=3,
        max_tools=3,
        max_bytes=8192,
        max_context=100,
        max_output_tokens=20,
        **limits,
    )


@pytest.mark.parametrize("model", SECURITY_PROFILES)
def test_frozen_pending_profiles_and_identity(model):
    p = SECURITY_PROFILES[model]
    assert p.route == f"{p.provider}/{model}"
    assert "1.18.20" in p.version
    with pytest.raises(AgentConfigError, match="pending"):
        resolve_security_profile(configuration(model))


@pytest.mark.parametrize(
    "field,value", [("runtimeVersion", "wrong"), ("taskType", "other")]
)
def test_wrong_profile_never_runs(monkeypatch, field, value):
    p = SECURITY_PROFILES["gpt-5.6-sol"]
    monkeypatch.setitem(
        SECURITY_PROFILES, p.model, replace(p, verification_status="verified")
    )
    c = configuration()
    c[field] = value
    with pytest.raises(AgentConfigError):
        resolve_security_profile(c)


def test_stream_preserves_disjoint_normalized_categories(tmp_path):
    r = stream(tmp_path, events())
    assert r.code == 0
    assert r.counts == {
        "input": 10,
        "output": 5,
        "reasoning": 3,
        "cache_read": 2,
        "cache_write": 1,
        "total": 21,
    }
    assert r.steps == 1


def test_missing_usage_is_unknown_and_cannot_pass(tmp_path):
    r = stream(tmp_path, events(total=None))
    assert r.code == 4 and r.counts["total"] is None


def test_duplicate_usage_event_is_not_counted_twice(tmp_path):
    e = events()
    e.append(copy.deepcopy(e[-1]))
    r = stream(tmp_path, e)
    assert r.code == 0 and r.counts["total"] == 21


@pytest.mark.parametrize(
    "tail",
    [
        "import time; time.sleep(30)",
        "import sys; sys.stderr.write('x'*1000000); sys.stderr.flush()",
    ],
)
def test_silent_and_stderr_flood_are_bounded(tmp_path, tail):
    script = tmp_path / "block.py"
    script.write_text(tail)
    before = time.monotonic()
    r = run_stream(
        [sys.executable, str(script)],
        str(tmp_path),
        env={},
        max_seconds=0.15,
        max_tokens=100,
        max_steps=3,
        max_tools=3,
        max_bytes=1024,
        max_context=100,
        max_output_tokens=20,
    )
    assert r.code == 124 and time.monotonic() - before < 3


def test_cache_and_reasoning_count_toward_ceiling(tmp_path):
    r = stream(tmp_path, events(reasoning=100, total=118))
    assert r.code == 124


@pytest.mark.parametrize(
    "extra",
    [
        {"type": "error", "error": {"message": "private provider content"}},
        {
            "type": "tool_use",
            "part": {"tool": "bash", "state": {"status": "completed"}},
        },
    ],
)
def test_error_or_forbidden_tool_never_clean(tmp_path, extra):
    r = stream(tmp_path, events() + [extra])
    assert r.code == 4 and "private provider content" not in r.reason


@pytest.mark.parametrize(
    "text,code",
    [
        ('{"results":[]}', 0),
        ("bad", 2),
        ("I cannot fulfill this request.", 3),
        ('{"results":[{"path":"../escape","extra":{}}]}', 2),
    ],
)
def test_explicit_result_attribution_and_failures(tmp_path, text, code):
    c = configuration()
    r = stream(tmp_path, events(text))
    result, exit_code = run_security_execution(
        c, AgentConfig(workspace=str(tmp_path)), stream_result=r
    )
    assert exit_code == code
    assert result["model"] == "gpt-5.6-sol" and result["executionRevisionId"] == 9
    u = result["taskResult"]["runnerUsage"]
    assert u["reasoningTokens"] == 3 and u["transportRetries"] is None
    assert u["billingComplete"] is False
    metadata = TaskResult.model_validate(result["taskResult"])
    validate_result_configuration(metadata, c, 9, result["gate"])
    from agent.remote import build_ci_run_payload
    from app.schemas.ci import CiRunIngest

    CiRunIngest.model_validate(build_ci_run_payload(result, "b9-fixture"))


def test_pending_cannot_be_bypassed_by_legacy_cli(run_agent, workspace, fake_opencode):
    r = run_agent(
        {
            "MODELMATCH_TASK": "security",
            "AGENT_MODEL": "openai/gpt-5.6-sol",
            "AGENT_WORKSPACE": str(workspace),
            **fake_opencode,
        }
    )
    assert r.returncode == 4 and "pending" in r.stderr


def test_profile_environment_ignores_host_and_checkout_overrides(tmp_path, monkeypatch):
    from agent.security_profile import isolated_environment

    monkeypatch.setenv("GEMINI_API_KEY", "fixture-key")
    monkeypatch.setenv("GOOGLE_GENERATIVE_AI_API_KEY", "wrong-key")
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"permission":"allow"}')
    monkeypatch.setenv("HTTPS_PROXY", "http://wrong.test")
    monkeypatch.setenv("MODELMATCH_CI_TOKEN", "fixture-ci-token")
    p = SECURITY_PROFILES["gemini-3.7-flash"]
    env = isolated_environment(
        p, TaskConfiguration(inputs={"diff": False}), str(tmp_path), 1024, 4
    )
    assert env["GOOGLE_GENERATIVE_AI_API_KEY"] == "fixture-key"
    assert "HTTPS_PROXY" not in env and "MODELMATCH_CI_TOKEN" not in env
    cfg = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert cfg["permission"]["*"] == "deny"
    assert (
        cfg["provider"]["google"]["models"][p.model]["options"]["thinkingConfig"][
            "thinkingLevel"
        ]
        == "low"
    )
    assert cfg["agent"]["title"]["disable"] and cfg["compaction"]["auto"] is False
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"


def test_workspace_links_and_byte_limits_fail_before_runner(tmp_path):
    from agent.errors import CeilingExceeded
    from agent.security_profile import inspect_workspace

    inputs = TaskConfiguration().inputs
    (tmp_path / "x.py").write_text("hello")
    (tmp_path / "escape").symlink_to("/etc/passwd")
    with pytest.raises(AgentConfigError):
        inspect_workspace(str(tmp_path), inputs)
    (tmp_path / "escape").unlink()
    inputs.max_file_bytes = 2
    with pytest.raises(CeilingExceeded):
        inspect_workspace(str(tmp_path), inputs)


def test_critical_gate_cannot_be_disabled_and_paths_must_exist(tmp_path):
    text = json.dumps(
        {
            "results": [
                {
                    "path": "x.py",
                    "start": {"line": 1},
                    "extra": {
                        "message": "Unsafe query",
                        "severity": "ERROR",
                        "metadata": {"confidence": "HIGH", "cwe": ["CWE-89"]},
                    },
                }
            ]
        }
    )
    r = stream(tmp_path, events(text))
    result, code = run_security_execution(
        configuration(), AgentConfig(fail_severities=[]), stream_result=r
    )
    assert code == 1 and result["findings"][0]["cwe"] == "CWE-89"
    from agent.errors import MalformedFindings
    from agent.security_execution import parse_findings

    with pytest.raises(MalformedFindings):
        parse_findings(text, {"different.py"})


def test_dispatch_security_posts_bare_model_revision_and_no_legacy_fetch(
    monkeypatch, capsys, tmp_path
):
    from agent import execution
    from agent.__main__ import main

    p = SECURITY_PROFILES["gpt-5.6-sol"]
    monkeypatch.setitem(
        SECURITY_PROFILES, p.model, replace(p, verification_status="verified")
    )
    c = configuration()
    result = run_security_execution(
        c, AgentConfig(), stream_result=stream(tmp_path, events())
    )
    monkeypatch.setattr(execution, "run_security_execution", lambda *args: result)
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
        "MODELMATCH_BUILD_ID": "b9-fixture",
        "AGENT_IMAGE_TASK": "security",
    }.items():
        monkeypatch.setenv(key, value)
    assert main([]) == 0
    assert len(calls) == 2 and "/execution/v1/" in calls[0][1]
    assert calls[1][2]["model"] == p.model and calls[1][2]["executionRevisionId"] == 9
    assert (
        json.loads(capsys.readouterr().out)["taskResult"]["validationStatus"]
        == "not_run"
    )
    monkeypatch.setenv("AGENT_IMAGE_TASK", "review")
    assert main([]) == 4


def test_terminated_parent_cannot_leave_child_running(tmp_path):
    import os

    script = tmp_path / "fork.py"
    marker = tmp_path / "escaped"
    script.write_text(
        f"import os,time\nif os.fork()==0:\n time.sleep(.5)\n open({str(marker)!r},'w').write('escaped')\nelse:\n time.sleep(10)\n"
    )
    r = run_stream(
        [sys.executable, str(script)],
        str(tmp_path),
        env={},
        max_seconds=0.1,
        max_tokens=100,
        max_steps=3,
        max_tools=3,
        max_bytes=1024,
        max_context=100,
        max_output_tokens=20,
    )
    time.sleep(0.6)
    assert r.code == 124 and not os.path.exists(marker)


@pytest.mark.parametrize(
    "change",
    [
        {"reasoning": -1},
        {"input": True},
        {"input": "10"},
        {"total": 1},
    ],
)
def test_bad_usage_cannot_pass(tmp_path, change):
    assert stream(tmp_path, events(**change)).code == 4


def test_truncated_output_with_valid_json_is_not_success(tmp_path):
    e = events()
    e[-1]["part"]["reason"] = "length"
    assert stream(tmp_path, e).code == 4


def test_timeout_retains_captured_usage(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text(
        "import time\n"
        + "\n".join(f"print({json.dumps(json.dumps(e))},flush=True)" for e in events())
        + "\ntime.sleep(30)\n"
    )
    r = run_stream(
        [sys.executable, str(script)],
        str(tmp_path),
        env={},
        max_seconds=0.15,
        max_tokens=100,
        max_steps=3,
        max_tools=3,
        max_bytes=8192,
        max_context=100,
        max_output_tokens=20,
    )
    result, code = run_security_execution(
        configuration(), AgentConfig(), stream_result=r
    )
    assert code == 124 and result["tokensIn"] == 10
    assert result["taskResult"]["executionStatus"] == "timed_out"


@pytest.mark.parametrize("model", SECURITY_PROFILES)
def test_profile_to_subprocess_and_result_offline(monkeypatch, tmp_path, model):
    import agent.security_execution as execution

    p = SECURITY_PROFILES[model]
    monkeypatch.setitem(
        SECURITY_PROFILES, model, replace(p, verification_status="verified")
    )
    monkeypatch.setenv(p.credential_env, "fixture-key")
    monkeypatch.setattr(execution, "check_launch_boundary", lambda *args: None)
    (tmp_path / "app.py").write_text("# fixture\n")
    fake = tmp_path / "runner.py"
    fake.write_text(
        "import json,os,sys\n"
        + "\n".join(f"print({json.dumps(json.dumps(e))},flush=True)" for e in events())
    )
    invoked = []

    def fixture_stream(cmd, cwd, **options):
        invoked.append(cmd)
        cfg = json.loads(options["env"]["OPENCODE_CONFIG_CONTENT"])
        assert cfg["model"] == p.route and cfg["agent"]["audit"]["steps"] == 20
        assert cmd[cmd.index("-m") + 1] == p.route
        assert "MODELMATCH_CI_TOKEN" not in options["env"]
        return run_stream([sys.executable, str(fake)], cwd, **options)

    monkeypatch.setattr(execution, "run_stream", fixture_stream)
    result, code = execution.run_security_execution(
        configuration(model), AgentConfig(workspace=str(tmp_path))
    )
    assert code == 0 and len(invoked) == 1
    assert (
        result["model"] == model
        and result["taskResult"]["runnerUsage"]["attempts"] == 1
    )


def test_launch_requires_real_readonly_container(tmp_path):
    from agent.security_profile import check_launch_boundary

    with pytest.raises(AgentConfigError):
        check_launch_boundary(str(tmp_path), TaskConfiguration().resources)


def test_review_wire_result_does_not_gain_null_security_extension():
    metadata = TaskResult(
        version=1,
        kind="findings",
        task="ci_review",
        mode="single_call",
        execution_status="completed",
    )
    assert "runnerUsage" not in metadata.model_dump(mode="json", by_alias=True)


def test_workspace_root_link_is_rejected(tmp_path):
    from agent.security_profile import inspect_workspace

    real = tmp_path / "real"
    real.mkdir()
    (real / "x.py").write_text("# fixture")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(AgentConfigError):
        inspect_workspace(str(linked), TaskConfiguration().inputs)
