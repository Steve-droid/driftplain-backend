"""Named test profiles; fake evidence is never runtime activation."""

import copy
import hashlib

import pytest

from agent.errors import AgentConfigError
from app.selections.policy import policy_for
from app.task_contracts import TaskConfiguration, configure
from tests.agent.test_other_workspace import repository as repository_fixture
from tests.agent.test_task_contracts import config as base_config


@pytest.fixture
def repository(tmp_path):
    return repository_fixture.__wrapped__(tmp_path)


def configuration(language="python", model="gpt-5.6-sol", **options):
    from app.test_generation_contracts import TEST_PROFILES

    p = TEST_PROFILES[(language, model)]
    options.setdefault("inputs", {"diff": False})
    options.setdefault("write_paths", ["tests"])
    options.setdefault(
        "validation_commands",
        [
            {
                "id": "suite",
                "argv": ["python", "-m", "pytest", "tests"]
                if language == "python"
                else ["node", "--test", "tests"],
                "environmentImage": "fixture@sha256:" + "a" * 64,
            }
        ],
    )
    options.setdefault(
        "test_environment",
        {
            "profile": "pytest-v1" if language == "python" else "node-test-v1",
            "dependencyLock": "requirements.lock"
            if language == "python"
            else "package-lock.json",
            "dependencySha256": hashlib.sha256(b"locked\n").hexdigest(),
        },
    )
    return {
        **base_config(),
        "taskType": "test_generation",
        "executionMode": "opencode",
        "capability": "test_generation_" + language,
        "runtimeVersion": p.version,
        "policy": policy_for("test_generation", "opencode", language),
        "model": {
            "name": model,
            "provider": p.provider,
            "providerModelId": model,
            "authMode": "api_key",
            "credentialEnvVar": p.credential_env,
        },
        **configure(
            "test_generation", "opencode", TaskConfiguration(**options), language
        ),
    }


@pytest.mark.parametrize("language", ["python", "node"])
@pytest.mark.parametrize(
    "model", ["gpt-5.6-sol", "claude-sonnet-5", "gemini-3.7-flash"]
)
def test_exact_pending_profiles(language, model):
    from agent.test_generation import resolve_test_profile

    c = configuration(language, model)
    with pytest.raises(AgentConfigError, match="pending"):
        resolve_test_profile(c)
    c["runtimeVersion"] = c["runtimeVersion"].replace(language, "other")
    with pytest.raises(AgentConfigError, match="Unsupported"):
        resolve_test_profile(c)


@pytest.mark.parametrize(
    "path,operation",
    [
        ("src/code.py", "added"),
        ("tests/conftest.py", "added"),
        ("tests/test_old.py", "modified"),
        ("tests/test_old.py", "deleted"),
        ("tests/package.json", "added"),
        ("tests/pytest.ini", "added"),
    ],
)
def test_only_new_test_files(path, operation):
    from app.task_contracts import ChangedFile
    from app.test_generation_contracts import validate_test_paths

    with pytest.raises(ValueError):
        validate_test_paths("python", [ChangedFile(path=path, operation=operation)])


def test_report_parser_matches_generated_and_preserves_baseline():
    from agent.test_validation import evaluate_reports

    baseline = {
        "version": 1,
        "tests": [
            {
                "id": "tests/test_old.py::test_old",
                "path": "tests/test_old.py",
                "status": "passed",
            }
        ],
        "errors": [],
    }
    candidate = copy.deepcopy(baseline)
    candidate["tests"].append(
        {
            "id": "tests/test_new.py::test_new",
            "path": "tests/test_new.py",
            "status": "passed",
        }
    )
    e = evaluate_reports(baseline, candidate, ["tests/test_new.py"])
    assert e["generatedTestsDiscovered"] == e["generatedTestsExecuted"] == 1
    for mutate in (
        lambda c: c["tests"].pop(0),
        lambda c: c["tests"][-1].update(status="skipped"),
        lambda c: c["tests"].append(c["tests"][-1]),
        lambda c: c.update(errors=["collection failed"]),
    ):
        bad = copy.deepcopy(candidate)
        mutate(bad)
        with pytest.raises(ValueError):
            evaluate_reports(baseline, bad, ["tests/test_new.py"])
    with pytest.raises(ValueError):
        evaluate_reports(baseline, baseline, ["tests/test_new.py"])


def test_named_setup_uses_trusted_launcher():
    from app.selections.other_setup import build_execution_command

    c = configuration()
    command = build_execution_command(
        c, "launcher@sha256:" + "a" * 64, "editor@sha256:" + "b" * 64
    )
    assert "AGENT_OTHER_IMAGE=editor@sha256:" in command
    assert "-m agent" in command and "docker.sock" in command


def test_runner_report_cannot_be_forged_by_duplicate_frames():
    from agent.test_validation import parse_report

    raw = b'DRIFTPLAIN_TEST_REPORT_V1={"version":1,"tests":[],"errors":[]}\n'
    with pytest.raises(ValueError, match="ambiguous"):
        parse_report(raw + raw)
    with pytest.raises(ValueError):
        parse_report(b"0 tests passed\n")


@pytest.mark.parametrize(
    "argv",
    [
        ["sh", "-c", "true"],
        ["python", "-m", "pytest", "--collect-only", "tests"],
        ["python", "-m", "pytest", "-k", "none"],
        ["python", "-m", "pytest", "tests/../src"],
    ],
)
def test_named_environment_rejects_command_selection(argv):
    with pytest.raises(ValueError):
        configuration(
            validation_commands=[
                {
                    "id": "suite",
                    "argv": argv,
                    "environmentImage": "fixture@sha256:" + "a" * 64,
                }
            ]
        )


def test_node_never_borrows_python_ranking():
    assert policy_for("test_generation", "opencode", "node")["benchmark"] is None
    assert (
        policy_for("test_generation", "opencode", "python")["benchmark"]
        == "testgeneval"
    )


@pytest.mark.parametrize("language", ["python", "node"])
def test_named_cli_dispatch_and_pending_has_no_call(
    language, monkeypatch, tmp_path, capsys
):
    from dataclasses import replace

    from agent.__main__ import main
    from app.test_generation_contracts import TEST_PROFILES

    c = configuration(language)
    p = TEST_PROFILES[(language, "gpt-5.6-sol")]
    calls = []

    def request(method, url, token, timeout, body=None):
        calls.append((method, url, body))
        return c if method == "GET" else {"id": 1}

    monkeypatch.setattr("agent.remote._request", request)

    def run(body, local, **kw):
        assert body == c and body["taskType"] == "test_generation"
        return {
            "findings": [],
            "tokensIn": 0,
            "tokensOut": 0,
            "model": p.model,
            "gate": "fail",
            "executionRevisionId": 9,
            "taskResult": {
                "version": 1,
                "kind": "patch",
                "task": "test_generation",
                "mode": "opencode",
                "language": language,
                "executionStatus": "failed",
                "executionReason": "fixture",
            },
        }, 4

    monkeypatch.setattr("agent.other_opencode.run_opencode", run)
    for key, value in {
        "MODELMATCH_EXECUTION_CONFIG": "true",
        "MODELMATCH_API_URL": "https://example.test",
        "MODELMATCH_PROJECT_ID": "7",
        "MODELMATCH_CI_TOKEN": "fixture-token",
        "MODELMATCH_POST_RESULT": "true",
        "MODELMATCH_BUILD_ID": "b11-cli",
        "AGENT_IMAGE_TASK": "review",
    }.items():
        monkeypatch.setenv(key, value)
    assert main([]) == 4
    assert len(calls) == 1  # pending fails before any editor or result fabrication
    calls.clear()
    monkeypatch.setitem(
        TEST_PROFILES, (language, p.model), replace(p, verification_status="verified")
    )
    assert main([]) == 4
    assert len(calls) == 2 and calls[1][2]["executionRevisionId"] == 9
    assert calls[1][2]["taskResult"]["language"] == language


def test_failed_validation_cleanup_retains_workspace(repository, tmp_path):
    from agent.config import AgentConfig
    from agent.other_opencode import run_opencode
    from agent.security_stream import SecurityStream
    from app.task_contracts import ValidationCheck
    from tests.agent.test_other_execution import configuration as other_config

    root, commit = repository
    c = other_config(
        mode="opencode",
        write_paths=["src"],
        inputs={"diff": False},
        validation_commands=[
            {
                "id": "test",
                "argv": ["true"],
                "environmentImage": "fixture@sha256:" + "a" * 64,
            }
        ],
    )

    class Executor:
        cleanup_failed = True

        def run(self, workspace, cmd, **kw):
            self.retained = workspace.parent
            return ValidationCheck(
                command_id=cmd.id,
                status="unavailable",
                execution_revision_id=9,
                base_commit=commit,
                patch_sha256=kw["patch_sha256"],
                reason="Cleanup unconfirmed",
            ), None

    class Runner:
        def run(self, *args):
            return SecurityStream(
                text='{"summary":"No changes"}',
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

    executor = Executor()
    try:
        _result, code = run_opencode(
            c,
            AgentConfig(
                workspace=str(root),
                base_commit=commit,
                output_dir=str(tmp_path / "out"),
            ),
            runner=Runner(),
            executor=executor,
        )
        assert code == 1 and executor.retained.exists()
    finally:
        import shutil

        if hasattr(executor, "retained"):
            shutil.rmtree(executor.retained)  # Synthetic executor created no container.
