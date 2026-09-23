"""Explicit review execution with injected offline HTTP fixtures."""

import io
import json
from dataclasses import replace

import pytest

from agent.config import AgentConfig
from agent.errors import AgentConfigError
from agent.review_execution import read_diff, resolve_review_profile, run_review
from agent.review_providers import ReviewProvider
from app.review_contracts import REVIEW_PROFILES
from app.schemas.ci import CiRunIngest
from app.selections.policy import policy_for
from app.task_contracts import TaskConfiguration, configure
from tests.agent.test_review_providers import Transport, response
from tests.agent.test_task_contracts import config as base_config


def configuration(model="gpt-5.6-sol"):
    p = REVIEW_PROFILES[model]
    return {
        **base_config(),
        "taskType": "ci_review",
        "capability": "ci_review",
        "runtimeVersion": p.version,
        "policy": policy_for("ci_review", "single_call"),
        "model": {
            "name": model,
            "provider": p.provider,
            "providerModelId": model,
            "authMode": "api_key",
            "credentialEnvVar": p.credential_env,
        },
        **configure("ci_review", "single_call", TaskConfiguration()),
    }


def test_gemini_profile_matches_catalog_provider_vocabulary():
    from app.task_contracts import TaskResult, validate_result_configuration

    c = configuration("gemini-3.7-flash")
    assert c["model"]["provider"] == "google"
    p = REVIEW_PROFILES["gemini-3.7-flash"]
    r, code = run_review(
        "diff",
        c,
        AgentConfig(),
        client=ReviewProvider(p, transport=Transport(response("google", p.model))),
    )
    assert code == 0
    assert r["taskResult"]["providerUsage"]["provider"] == "google"
    validate_result_configuration(
        TaskResult.model_validate(r["taskResult"]), c, 9, "pass"
    )


def test_pending_and_wrong_version_rejected_before_execution(monkeypatch):
    c = configuration()
    with pytest.raises(AgentConfigError, match="pending"):
        resolve_review_profile(c)
    monkeypatch.setitem(
        REVIEW_PROFILES,
        "gpt-5.6-sol",
        replace(REVIEW_PROFILES["gpt-5.6-sol"], verification_status="verified"),
    )
    c["runtimeVersion"] = "other-version"
    with pytest.raises(AgentConfigError):
        resolve_review_profile(c)


@pytest.mark.parametrize(
    "text,code,status",
    [
        ('{"findings":[]}', 0, "completed"),
        (
            '{"findings":[{"severity":"high","category":"security","file":"x.py","line":1,"message":"unsafe"}]}',
            1,
            "completed",
        ),
        ('{"findings":[{"file":"private-text"}]}', 2, "failed"),
        ("not json", 2, "failed"),
    ],
)
def test_result_is_attributed_validated_and_no_output_leak(text, code, status):
    c = configuration()
    t = Transport(response("openai", "gpt-5.6-sol", text))
    client = ReviewProvider(REVIEW_PROFILES["gpt-5.6-sol"], transport=t)
    result, exit_code = run_review("diff", c, AgentConfig(), client=client)
    assert exit_code == code
    assert result["executionRevisionId"] == 9
    assert result["taskResult"]["executionStatus"] == status
    from agent.remote import build_ci_run_payload

    payload = CiRunIngest.model_validate(build_ci_run_payload(result, "b8-fixture"))
    assert payload.task_result.provider_usage.input_tokens == 100
    assert "private-text" not in json.dumps(result)
    assert len(t.calls) == 1


def test_refusal_preserves_usage_and_reports_failed_gate():
    c = configuration()
    body = response("openai", "gpt-5.6-sol")
    body["output"][0]["content"] = [{"type": "refusal"}]
    r, code = run_review(
        "diff",
        c,
        AgentConfig(),
        client=ReviewProvider(
            REVIEW_PROFILES["gpt-5.6-sol"], transport=Transport(body)
        ),
    )
    assert code == 3 and r["gate"] == "fail"
    assert r["taskResult"]["executionStatus"] == "refused"
    assert r["tokensIn"] == 100


def test_missing_usage_never_passes():
    body = response("openai", "gpt-5.6-sol")
    body.pop("usage")
    r, code = run_review(
        "diff",
        configuration(),
        AgentConfig(),
        client=ReviewProvider(
            REVIEW_PROFILES["gpt-5.6-sol"], transport=Transport(body)
        ),
    )
    assert code == 4 and r["gate"] == "fail"
    assert r["taskResult"]["providerUsage"]["inputTokens"] is None


def test_preflight_and_bounded_read_do_not_send_or_truncate():
    c = configuration()
    c["taskConfiguration"]["inputs"]["maxBytes"] = 2
    t = Transport(response("openai", "gpt-5.6-sol"))
    r, code = run_review(
        "too long",
        c,
        AgentConfig(),
        client=ReviewProvider(REVIEW_PROFILES["gpt-5.6-sol"], transport=t),
    )
    assert code == 124 and not t.calls
    assert r["taskResult"]["providerUsage"]["generationRequests"] == 0
    from agent.errors import CeilingExceeded

    with pytest.raises(CeilingExceeded):
        read_diff(None, 2, stdin=io.BytesIO(b"too long"))


def test_cli_opt_in_requires_complete_remote_config(monkeypatch, capsys):
    from agent.__main__ import main

    monkeypatch.setenv("MODELMATCH_EXECUTION_CONFIG", "true")
    assert main([]) == 4
    assert "requires" in capsys.readouterr().err


def test_legacy_cannot_bypass_pending_with_native_adapter(monkeypatch, capsys):
    from agent.__main__ import main

    monkeypatch.setenv("AGENT_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("LLM_CLIENT", "anthropic")
    assert main([]) == 4
    assert "pending" in capsys.readouterr().err


def test_cli_explicit_success_posts_exact_revision_and_only_one_request(
    monkeypatch, capsys
):
    import agent.review_providers as providers
    from agent.__main__ import main

    c = configuration()
    p = replace(REVIEW_PROFILES["gpt-5.6-sol"], verification_status="verified")
    monkeypatch.setitem(REVIEW_PROFILES, p.model, p)
    t = Transport(response(p.provider, p.model))
    monkeypatch.setattr(providers, "_http", t)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    monkeypatch.setenv("MODELMATCH_EXECUTION_CONFIG", "true")
    monkeypatch.setenv("MODELMATCH_API_URL", "https://example.test")
    monkeypatch.setenv("MODELMATCH_PROJECT_ID", "7")
    monkeypatch.setenv("MODELMATCH_CI_TOKEN", "fixture-token")
    monkeypatch.setenv("MODELMATCH_POST_RESULT", "true")
    monkeypatch.setenv("MODELMATCH_BUILD_ID", "b8-cli")
    monkeypatch.setattr("agent.review_execution.read_diff", lambda *a: "diff")
    requests = []

    def request(method, url, token, timeout, body=None):
        requests.append((method, url, body))
        return c if method == "GET" else {"id": 1}

    monkeypatch.setattr("agent.remote._request", request)
    assert main([]) == 0
    assert len(t.calls) == 1
    assert requests[0][1].endswith(
        "/execution/v1/projects/7/agent-config?taskContractVersion=1"
    )
    posted = CiRunIngest.model_validate(requests[1][2])
    assert posted.execution_revision_id == 9
    assert posted.task_result.provider_usage.profile_version == p.version
    assert json.loads(capsys.readouterr().out)["gate"] == "pass"


@pytest.mark.parametrize("model", list(REVIEW_PROFILES))
@pytest.mark.parametrize(
    "text",
    [
        '{"findings":[] , "unknown": 1}',
        '{"findings":[{"severity":"high","category":"security","file":"../x","message":"bad"}]}',
        "[]",
        '```json\n{"findings":[]}\n```',
    ],
)
def test_every_profile_rejects_malformed_findings(model, text):
    p = REVIEW_PROFILES[model]
    t = Transport(response(p.provider, model, text))
    result, code = run_review(
        "diff",
        configuration(model),
        AgentConfig(),
        client=ReviewProvider(p, transport=t),
    )
    assert code == 2 and result["gate"] == "fail"
    assert len(t.calls) == 1


def test_wall_deadline_interrupts_slow_transport():
    import time

    p = REVIEW_PROFILES["gpt-5.6-sol"]

    def slow(**kw):
        time.sleep(0.2)
        return 200, json.dumps(response(p.provider, p.model)).encode()

    started = time.monotonic()
    r, code = run_review(
        "diff",
        configuration(),
        AgentConfig().model_copy(update={"max_seconds": 0.02}),
        client=ReviewProvider(p, transport=slow),
    )
    assert time.monotonic() - started < 0.15
    assert code == 124 and r["taskResult"]["executionStatus"] == "timed_out"


def test_real_billing_categories_count_once_for_ceiling():
    p = REVIEW_PROFILES["claude-sonnet-5"]
    body = response(p.provider, p.model)
    body["usage"]["cache_read_input_tokens"] = 100_000
    r, code = run_review(
        "diff",
        configuration(p.model),
        AgentConfig(),
        client=ReviewProvider(p, transport=Transport(body)),
    )
    assert code == 124 and r["cacheReadTokens"] == 100_000
