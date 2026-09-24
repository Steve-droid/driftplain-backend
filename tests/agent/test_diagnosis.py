import json
from dataclasses import replace

import pytest

from agent.config import AgentConfig
from agent.errors import AgentConfigError
from app.selections.policy import policy_for
from app.task_contracts import TaskConfiguration, configure
from tests.agent.test_task_contracts import config as base_config
from tests.agent.test_other_workspace import repository


def configuration(fix=False, model="gpt-5.6-sol", **options):
    from app.diagnosis_contracts import DIAGNOSIS_PROFILES

    p = DIAGNOSIS_PROFILES[(fix, model)]
    options.setdefault("inputs", {"diff": False, "files": ["src/a.txt"]})
    options.setdefault(
        "diagnosis", {"stage": "Unit tests", "logArtifact": "upstream.log"}
    )
    if fix:
        options.setdefault("write_paths", ["src/app.py"])
    return {
        **base_config(),
        "taskType": "ci_failure_diagnosis",
        "executionMode": "opencode",
        "capability": "diagnosis_fix" if fix else "diagnosis_readonly",
        "runtimeVersion": p.version,
        "policy": policy_for("ci_failure_diagnosis", "opencode", propose_fix=fix),
        "model": {
            "name": model,
            "provider": p.provider,
            "providerModelId": model,
            "authMode": "api_key",
            "credentialEnvVar": p.credential_env,
        },
        **configure(
            "ci_failure_diagnosis",
            "opencode",
            TaskConfiguration(**options),
            propose_fix=fix,
        ),
    }


@pytest.mark.parametrize("fix", [False, True])
@pytest.mark.parametrize(
    "model", ["gpt-5.6-sol", "claude-sonnet-5", "gemini-3.7-flash"]
)
def test_profiles_remain_pending(fix, model):
    from agent.diagnosis import resolve_diagnosis_profile

    with pytest.raises(AgentConfigError, match="pending"):
        resolve_diagnosis_profile(configuration(fix, model))


def test_redaction_and_report_contract():
    from app.diagnosis_contracts import redact
    from agent.diagnosis import parse_report

    raw = "Authorization: Bearer abcdefghijk\nTOKEN=veryprivate\nhttps://bob:pass@example.test/x\n"
    cleaned = redact(raw, ["provider-value"])
    assert (
        "abcdefghijk" not in cleaned
        and "veryprivate" not in cleaned
        and "bob:pass" not in cleaned
    )
    assert "provider-value" not in redact("provider-value", ["provider-value"])
    for text in [
        "{}",
        '{"summary":"cause"}',
        '{"summary":"cause","uncertainty":"maybe","nextSteps":[],"cause":"unknown"}',
    ]:
        with pytest.raises(Exception):
            parse_report(text)
    r = parse_report(
        json.dumps(
            {
                "summary": "Likely dependency failure",
                "uncertainty": "Cannot confirm remotely",
                "nextSteps": ["Check dependency availability"],
                "cause": "external",
                "evidenceArtifactIds": ["failure-log"],
                "noPatchReason": "External service unavailable",
            }
        )
    )
    assert r.uncertainty


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_a.py",
        "src/test_a.py",
        "src/conftest.py",
        ".github/workflows/ci.yml",
        "package.json",
        "src/pytest.ini",
        "src/check.sh",
    ],
)
def test_fix_does_not_allow_quality_changes(path):
    with pytest.raises(ValueError):
        configuration(True, write_paths=[path])


def test_readonly_profile_has_no_edit_or_validation_authority():
    from app.diagnosis_contracts import DIAGNOSIS_PROFILES
    from agent.other_profile import configuration as editor_config

    c = configuration()
    cfg = editor_config(
        DIAGNOSIS_PROFILES[(False, "gpt-5.6-sol")],
        TaskConfiguration.model_validate(c["taskConfiguration"]),
        1024,
        4,
    )
    assert cfg["permission"].get("edit", "deny") == "deny"
    assert cfg.get("mcp", {}) == {}
    assert cfg["permission"].get("bash", "deny") == "deny"


@pytest.fixture
def diagnosis_repo(repository):
    from tests.agent.test_other_workspace import git

    root, _ = repository
    (root / "src/app.py").write_text("value = 1\n")
    (root / "tests").mkdir()
    (root / "tests/test_app.py").write_text("assert True\n")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-qm",
        "source",
    )
    return root, git(root, "rev-parse", "HEAD")


def local_config(tmp_path, repo, **kw):
    root, commit = repo
    inputs = tmp_path / "inputs"
    inputs.mkdir(exist_ok=True)
    (inputs / "upstream.log").write_text(
        "TOKEN=privatevalue\nExpected value 2, got 1\n"
    )
    return AgentConfig(
        workspace=str(root),
        base_commit=commit,
        output_dir=str(tmp_path / "out"),
        input_artifacts=str(inputs),
        build_id="build-12",
        failed_stage="Unit tests",
        upstream_status="FAILURE",
        upstream_exit_status=1,
        **kw,
    )


class Runner:
    def __init__(self, edit=False, cause="repository", text=None, code=0):
        self.calls = 0
        self.edit = edit
        self.cause = cause
        self.text = text
        self.code = code

    def run(self, work, configuration, prompt, local, validate, remaining):
        from agent.security_stream import SecurityStream

        self.calls += 1
        assert "privatevalue" not in prompt
        assert "[REDACTED]" in prompt
        if self.edit:
            (work.path / "src/app.py").write_text("value = 2\n")
        else:
            assert not (work.path / "tests/test_app.py").exists()
        return SecurityStream(
            text=self.text
            if self.text is not None
            else json.dumps(
                {
                    "summary": "Expected 2 but got 1",
                    "uncertainty": "Limited to captured evidence",
                    "nextSteps": ["Inspect source"],
                    "cause": self.cause,
                    "evidenceArtifactIds": ["failure-log"],
                    "noPatchReason": "Read-only or insufficient evidence"
                    if not self.edit
                    else None,
                }
            ),
            code=self.code,
            reason="Runner failed" if self.code else None,
            steps=1,
            counts={
                "input": 1,
                "output": 1,
                "cache_read": 0,
                "cache_write": 0,
                "reasoning": 0,
                "total": 2,
            },
        )


def claimed(_):
    return {"claimed": True, "claimId": "a" * 32}


@pytest.mark.parametrize("cause", ["repository", "unknown", "external"])
def test_readonly_report_and_original_failure(diagnosis_repo, tmp_path, cause):
    from agent.diagnosis import run_diagnosis
    from app.task_contracts import TaskResult, validate_result_configuration

    c = configuration(inputs={"diff": False, "files": ["src/app.py"]})
    runner = Runner(cause=cause)
    result, code = run_diagnosis(
        c, local_config(tmp_path, diagnosis_repo), claim=claimed, runner=runner
    )
    assert code == 1 and result["gate"] == "fail"
    r = TaskResult.model_validate(result["taskResult"])
    assert (
        r.execution_status == "completed"
        and r.validation_status == "not_run"
        and r.patch is None
    )
    assert r.failure.original_status == "FAILURE" and r.report.cause == cause
    validate_result_configuration(r, c, c["executionRevisionId"], "fail")
    assert (tmp_path / "out/failure.log").exists() and "privatevalue" not in (
        tmp_path / "out/failure.log"
    ).read_text()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"upstream_status": "SUCCESS"}, "did not fail"),
        ({"upstream_status": "ABORTED"}, "cancelled"),
        ({"failed_stage": "Driftplain failure diagnosis"}, "agent stages"),
        ({"upstream_exit_status": None}, "Infrastructure"),
    ],
)
def test_skip_never_claims_or_calls(diagnosis_repo, tmp_path, change, reason):
    from agent.diagnosis import run_diagnosis

    local = local_config(tmp_path, diagnosis_repo).model_copy(update=change)

    def forbidden(*a):
        raise AssertionError("must not claim")

    runner = Runner()
    r, code = run_diagnosis(configuration(), local, claim=forbidden, runner=runner)
    assert (
        code == 1 and runner.calls == 0 and reason in r["taskResult"]["executionReason"]
    )


def test_duplicate_and_missing_logs_no_calls(diagnosis_repo, tmp_path):
    from agent.diagnosis import run_diagnosis

    local = local_config(tmp_path, diagnosis_repo)
    runner = Runner()
    r, code = run_diagnosis(
        configuration(), local, claim=lambda _: {"claimed": False}, runner=runner
    )
    assert runner.calls == 0 and "Duplicate" in r["taskResult"]["executionReason"]
    (tmp_path / "inputs/upstream.log").unlink()
    r, code = run_diagnosis(configuration(), local, claim=claimed, runner=runner)
    assert runner.calls == 0 and "No usable" in r["taskResult"]["executionReason"]


@pytest.mark.parametrize("text,code", [("not JSON", 0), ("{}", 0), ("", 124)])
def test_failed_output_keeps_upstream_failure(diagnosis_repo, tmp_path, text, code):
    from agent.diagnosis import run_diagnosis

    r, rc = run_diagnosis(
        configuration(inputs={"diff": False, "files": ["src/app.py"]}),
        local_config(tmp_path, diagnosis_repo),
        claim=claimed,
        runner=Runner(text=text, code=code),
    )
    assert (
        rc and r["gate"] == "fail" and r["taskResult"]["executionStatus"] != "completed"
    )
    assert r["taskResult"]["failure"]["originalStatus"] == "FAILURE"


@pytest.mark.parametrize("status", ["passed", "failed", "unavailable", "not_run"])
def test_fix_validation_is_separate_from_upstream(diagnosis_repo, tmp_path, status):
    from agent.diagnosis import run_diagnosis
    from app.task_contracts import (
        Artifact,
        TaskResult,
        ValidationCheck,
        validate_result_configuration,
    )
    import hashlib

    commands = (
        []
        if status == "not_run"
        else [
            {
                "id": "reproduce",
                "argv": ["python", "-c", "assert True"],
                "environmentImage": "fixture@sha256:" + "a" * 64,
            }
        ]
    )
    c = configuration(
        True,
        inputs={"diff": False, "files": ["src/app.py"]},
        validation_commands=commands,
    )

    class Executor:
        def run(self, workspace, cmd, **kw):
            artifact = None
            if status != "unavailable":
                raw = b"validation result\n"
                (kw["out"] / "validation.log").write_bytes(raw)
                artifact = Artifact(
                    id="validation",
                    kind="validation_log",
                    path="validation.log",
                    sha256=hashlib.sha256(raw).hexdigest(),
                    size_bytes=len(raw),
                )
            return ValidationCheck(
                command_id=cmd.id,
                status=status,
                execution_revision_id=c["executionRevisionId"],
                base_commit=kw["base_commit"],
                patch_sha256=kw["patch_sha256"],
                exit_code=0
                if status == "passed"
                else 1
                if status == "failed"
                else None,
                duration_ms=1,
                log_artifact_id="validation" if artifact else None,
                reason="Unavailable" if status == "unavailable" else None,
            ), artifact

    r, code = run_diagnosis(
        c,
        local_config(tmp_path, diagnosis_repo),
        claim=claimed,
        runner=Runner(edit=True),
        executor=Executor(),
    )
    assert code and r["gate"] == "fail"
    result = TaskResult.model_validate(r["taskResult"])
    assert result.patch and result.validation_status == status
    assert (diagnosis_repo[0] / "src/app.py").read_text() == "value = 1\n"
    validate_result_configuration(result, c, c["executionRevisionId"], "fail")
    assert (tmp_path / "out/changes.patch").exists()


def test_setup_wraps_selected_stage_and_preserves_failure():
    from app.selections.diagnosis_setup import build_diagnosis_stage

    text = build_diagnosis_stage("docker run pinned", configuration())
    assert "returnStatus: true" in text and "currentBuild.result = 'FAILURE'" in text
    assert "if (upstreamStatus != 0)" in text and "finally" in text
    assert "archiveArtifacts" in text and "upstreamStatus == 0" in text
    assert "error('Original upstream stage failed')" in text


@pytest.mark.parametrize("delivery_fails", [False, True])
def test_cli_deduplicates_before_call_and_delivery_failure_is_separate(
    diagnosis_repo, tmp_path, monkeypatch, capsys, delivery_fails
):
    from agent.__main__ import main
    from agent.remote import RemoteError
    from app.diagnosis_contracts import DIAGNOSIS_PROFILES
    from agent.other_opencode import run_opencode as real_run

    c = configuration(inputs={"diff": False, "files": ["src/app.py"]})
    p = DIAGNOSIS_PROFILES[(False, "gpt-5.6-sol")]
    monkeypatch.setitem(
        DIAGNOSIS_PROFILES, (False, p.model), replace(p, verification_status="verified")
    )
    local = local_config(tmp_path, diagnosis_repo)
    runner = Runner()
    claims = []
    posts = []

    def request(method, url, token, timeout, body=None):
        if method == "GET":
            return c
        if url.endswith("/failure-claims"):
            claims.append(body)
            return {
                "claimed": len(claims) == 1,
                "claimId": "a" * 32 if len(claims) == 1 else None,
            }
        posts.append(body)
        if delivery_fails:
            raise RemoteError("Unavailable")
        return {"id": 1}

    monkeypatch.setattr("agent.remote._request", request)
    monkeypatch.setattr(
        "agent.other_opencode.run_opencode",
        lambda body, conf, **kw: real_run(body, conf, **{**kw, "runner": runner}),
    )
    for key, value in {
        "MODELMATCH_EXECUTION_CONFIG": "true",
        "MODELMATCH_API_URL": "https://example.test",
        "MODELMATCH_PROJECT_ID": "7",
        "MODELMATCH_CI_TOKEN": "fixture-token",
        "MODELMATCH_POST_RESULT": "true",
        "MODELMATCH_BUILD_ID": "build-12",
        "AGENT_IMAGE_TASK": "review",
        "AGENT_BASE_COMMIT": local.base_commit,
        "AGENT_WORKSPACE": local.workspace,
        "AGENT_OUTPUT_DIR": local.output_dir,
        "AGENT_INPUT_ARTIFACTS": local.input_artifacts,
        "DRIFTPLAIN_FAILED_STAGE": "Unit tests",
        "DRIFTPLAIN_UPSTREAM_STATUS": "FAILURE",
        "DRIFTPLAIN_UPSTREAM_EXIT_STATUS": "1",
    }.items():
        monkeypatch.setenv(key, value)
    assert main([]) == (4 if delivery_fails else 1)
    result = json.loads(capsys.readouterr().out)
    assert result["gate"] == "fail"
    if delivery_fails:
        assert result["agentError"]["kind"] == "result_delivery_failed"
    assert main([]) == 1
    assert len(claims) == 2 and len(posts) == 1 and runner.calls == 1


def test_claim_uncertainty_is_not_retried(diagnosis_repo, tmp_path):
    from agent.diagnosis import run_diagnosis
    from agent.remote import RemoteError

    runner = Runner()

    def uncertain(_):
        raise RemoteError("Lost response")

    r, code = run_diagnosis(
        configuration(),
        local_config(tmp_path, diagnosis_repo),
        claim=uncertain,
        runner=runner,
    )
    assert (
        code == 4
        and runner.calls == 0
        and r["agentError"]["kind"] == "claim_unavailable"
    )


def test_fix_rejects_weakened_tests_even_if_model_reports_success(
    diagnosis_repo, tmp_path
):
    from agent.diagnosis import run_diagnosis

    class BadRunner(Runner):
        def run(self, work, *args):
            (work.path / "tests/test_app.py").write_text("assert False\n")
            return super().run(work, *args)

    r, code = run_diagnosis(
        configuration(True, inputs={"diff": False, "files": ["src/app.py"]}),
        local_config(tmp_path, diagnosis_repo),
        claim=claimed,
        runner=BadRunner(edit=True),
    )
    assert (
        code
        and r["taskResult"]["executionStatus"] == "failed"
        and r["taskResult"]["patch"] is None
    )
    assert (diagnosis_repo[0] / "tests/test_app.py").read_text() == "assert True\n"


def test_external_cause_cannot_leave_patch_artifact(diagnosis_repo, tmp_path):
    from agent.diagnosis import run_diagnosis

    r, code = run_diagnosis(
        configuration(True, inputs={"diff": False, "files": ["src/app.py"]}),
        local_config(tmp_path, diagnosis_repo),
        claim=claimed,
        runner=Runner(edit=True, cause="external"),
    )
    assert (
        code
        and r["taskResult"]["executionStatus"] == "failed"
        and r["taskResult"]["patch"] is None
    )
    assert not (tmp_path / "out/changes.patch").exists()


def test_external_fix_report_can_explicitly_propose_no_patch(diagnosis_repo, tmp_path):
    from agent.diagnosis import run_diagnosis
    from agent.security_stream import SecurityStream

    class NoPatch:
        def run(self, *args):
            return SecurityStream(
                text=json.dumps(
                    {
                        "summary": "Dependency host unavailable",
                        "cause": "external",
                        "uncertainty": "Only log evidence",
                        "nextSteps": [
                            "Retry the upstream build when service is restored"
                        ],
                        "evidenceArtifactIds": ["failure-log"],
                        "noPatchReason": "The failure is outside this repository",
                    }
                ),
                steps=1,
                counts={
                    "input": 1,
                    "output": 1,
                    "cache_read": 0,
                    "cache_write": 0,
                    "reasoning": 0,
                    "total": 2,
                },
            )

    r, code = run_diagnosis(
        configuration(True, inputs={"diff": False, "files": ["src/app.py"]}),
        local_config(tmp_path, diagnosis_repo),
        claim=claimed,
        runner=NoPatch(),
    )
    assert (
        code == 1
        and r["taskResult"]["executionStatus"] == "completed"
        and r["taskResult"]["patch"] is None
    )
    assert r["taskResult"]["report"]["noPatchReason"]


def test_redaction_json_flags_and_report_values():
    from app.diagnosis_contracts import redact

    assert "opaque-value" not in redact('{"api_key":"opaque-value"}')
    assert "opaque-value" not in redact("--password opaque-value")


def test_api_excerpt_byte_ceiling_and_failed_partial_usage():
    from app.task_contracts import (
        FailureContext,
        TaskResult,
        validate_result_configuration,
    )
    from app.security_contracts import RunnerUsage

    c = configuration()
    ctx = {
        "buildId": "b",
        "stage": "Unit tests",
        "commit": "a" * 40,
        "exitStatus": 1,
        "originalStatus": "FAILURE",
        "claimId": "a" * 32,
    }
    with pytest.raises(ValueError, match="byte ceiling"):
        FailureContext(**ctx, logExcerpt="😀" * 3000)
    r = TaskResult(
        version=1,
        task="ci_failure_diagnosis",
        mode="opencode",
        kind="report",
        execution_status="failed",
        execution_reason="Token ceiling exceeded",
        base_commit="a" * 40,
        failure=FailureContext(**ctx, logExcerpt="Failure"),
        runner_usage=RunnerUsage(
            provider="openai",
            profile_version=c["runtimeVersion"],
            input_tokens=100001,
            attempts=1,
            completed_steps=1,
            tool_calls=0,
        ),
    )
    validate_result_configuration(r, c, c["executionRevisionId"], "fail")
