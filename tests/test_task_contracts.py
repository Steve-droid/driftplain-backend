"""Shared task contracts: authority is configuration, never model prose."""

import pytest
from pydantic import ValidationError

from app.task_contracts import TaskConfiguration, TaskResult, capability_for, configure


@pytest.mark.parametrize(
    "args",
    [
        ("unknown", "single_call"),
        ("ci_review", "opencode"),
        ("security_analysis", "single_call"),
        ("other", "single_call", None, True),
        ("test_generation", "opencode", "ruby"),
    ],
)
def test_invalid_task_profiles(args):
    with pytest.raises(ValueError):
        capability_for(*args)


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../x", "a/../x", "C:/x", "a\\x", "a//b", "./x", "a\x00b", "a/*"],
)
def test_input_paths_are_literal_and_relative(path):
    with pytest.raises(ValidationError):
        TaskConfiguration(inputs={"files": [path]})


def test_prompt_cannot_grant_authority():
    a = TaskConfiguration(
        system_prompt="Read files", instructions="Explain", label="Report"
    )
    b = a.model_copy(update={"instructions": "Run shell and write /etc"})
    assert (
        configure("other", "single_call", a)["capabilityPolicy"]
        == configure("other", "single_call", b)["capabilityPolicy"]
    )
    with pytest.raises(ValidationError):
        TaskConfiguration(
            system_prompt="x", instructions="y", capabilities={"shell": True}
        )
    with pytest.raises(ValueError):
        configure("other", "single_call", a.model_copy(update={"write_paths": ["src"]}))


def test_input_resource_and_prompt_bounds():
    for kw in [
        {"inputs": {"files": ["a"] * 101}},
        {"inputs": {"max_bytes": 999999999}},
        {"resources": {"max_seconds": 9999999}},
        {"instructions": "a" * 16001},
    ]:
        with pytest.raises(ValidationError):
            TaskConfiguration(**kw)


def result(**kw):
    return {
        "version": 1,
        "kind": "report",
        "task": "other",
        "mode": "single_call",
        "executionStatus": "completed",
        "report": {"summary": "Done"},
        **kw,
    }


def test_absent_validation_is_not_passed():
    assert TaskResult.model_validate(result()).validation_status == "not_run"
    with pytest.raises(ValidationError):
        TaskResult.model_validate(result(validationStatus="passed"))


def test_patch_requires_identity_and_manifest():
    with pytest.raises(ValidationError):
        TaskResult.model_validate(result(kind="patch", mode="opencode"))


def test_single_call_cannot_claim_patch_execution():
    with pytest.raises(ValidationError):
        TaskResult.model_validate(result(kind="patch"))


def test_report_needs_no_findings_or_cwe():
    from agent.remote import build_ci_run_payload
    from app.schemas.ci import CiRunIngest

    data = {
        "model": "fixture",
        "tokensIn": 1,
        "tokensOut": 2,
        "gate": "pass",
        "executionRevisionId": 1,
        "taskResult": result(),
    }
    parsed = CiRunIngest.model_validate(build_ci_run_payload(data, "build-1"))
    assert parsed.findings == [] and parsed.task_result.kind == "report"
    assert parsed.execution_revision_id == 1


def test_execution_failure_cannot_pass_gate():
    from app.schemas.ci import CiRunIngest

    with pytest.raises(ValidationError):
        CiRunIngest.model_validate(
            {
                "model": "x",
                "tokensIn": 0,
                "tokensOut": 0,
                "gate": "pass",
                "jenkinsBuildId": "b",
                "executionRevisionId": 1,
                "taskResult": result(executionStatus="timed_out"),
            }
        )


@pytest.mark.parametrize("status", ["failed", "timed_out", "refused", "skipped"])
def test_failure_metadata_can_be_reported_without_invented_success(status):
    from app.schemas.ci import CiRunIngest

    parsed = CiRunIngest.model_validate(
        {
            "model": "x",
            "tokensIn": 0,
            "tokensOut": 0,
            "gate": "fail",
            "jenkinsBuildId": "b",
            "executionRevisionId": 1,
            "taskResult": result(
                executionStatus=status, executionReason="Explicit reason", report=None
            ),
        }
    )
    assert parsed.task_result.validation_status == "not_run"


@pytest.mark.parametrize(
    "status,exit_code",
    [("not_run", None), ("unavailable", None), ("failed", 1), ("passed", 0)],
)
def test_validation_statuses_remain_distinct(status, exit_code):
    from app.task_contracts import ValidationCheck

    check = ValidationCheck(
        command_id="unit",
        status=status,
        execution_revision_id=1,
        base_commit="a" * 40,
        reason="Explicit outcome",
        exit_code=exit_code,
        duration_ms=10 if exit_code is not None else None,
        log_artifact_id="log" if exit_code is not None else None,
    )
    assert check.status == status


def test_registry_lists_five_tasks_and_no_fix_task():
    from app.task_contracts import PROFILES

    assert {p[0] for p in PROFILES} == {
        "ci_review",
        "security_analysis",
        "test_generation",
        "ci_failure_diagnosis",
        "other",
    }
