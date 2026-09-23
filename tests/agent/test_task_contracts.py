"""Negotiation is explicit and shares the backend contract; it enables no runner."""

import copy

import pytest

from agent.remote import RemoteError, fetch_execution_config
from app.selections.policy import policy_for
from app.task_contracts import TaskConfiguration, configure


def config():
    return {
        "contractVersion": 2,
        "projectId": 7,
        "executionRevisionId": 9,
        "selectionId": 1,
        "runtimeId": 2,
        "catalogModelId": 3,
        "deploymentId": 4,
        "runtimeVersion": "fixture",
        "taskType": "other",
        "executionMode": "single_call",
        "capability": "custom_single_call",
        "policy": policy_for("other", "single_call"),
        "reviewPreferences": None,
        "model": {
            "name": "Fixture",
            "provider": "anthropic",
            "providerModelId": "fixture",
            "authMode": "api_key",
            "credentialEnvVar": "ANTHROPIC_API_KEY",
        },
        **configure(
            "other",
            "single_call",
            TaskConfiguration(
                label="Report", system_prompt="Explain", instructions="Summarize"
            ),
        ),
    }


def test_opt_in_reader_uses_versioned_endpoint(monkeypatch):
    calls = []

    def request(*args):
        calls.append(args)
        return config()

    monkeypatch.setattr("agent.remote._request", request)
    body = fetch_execution_config("https://example.test/", 7, "fixture-token")
    assert calls == [
        (
            "GET",
            "https://example.test/execution/v1/projects/7/agent-config?taskContractVersion=1",
            "fixture-token",
            15,
        )
    ]
    assert body["taskType"] == "other"


@pytest.mark.parametrize(
    "change",
    [
        {"contractVersion": 3},
        {"taskContractVersion": 2},
        {"projectId": 8},
        {"executionRevisionId": None},
        {"taskType": "review"},
        {"capability": "ci_review"},
        {"resultKinds": ["findings"]},
        {"endpoint": "https://wrong.test"},
        {"runtimeVersion": ""},
        {"taskContractVersion": True},
    ],
)
def test_bad_negotiation_is_rejected_without_fallback(monkeypatch, change):
    body = {**config(), **change}
    monkeypatch.setattr("agent.remote._request", lambda *a: body)
    with pytest.raises(RemoteError, match="inconsistent"):
        fetch_execution_config("https://example.test", 7, "fixture-token")


def test_prompt_and_server_policy_do_not_widen_capability(monkeypatch):
    body = copy.deepcopy(config())
    body["capabilityPolicy"]["shell"] = True
    monkeypatch.setattr("agent.remote._request", lambda *a: body)
    with pytest.raises(RemoteError):
        fetch_execution_config("https://example.test", 7, "fixture-token")
