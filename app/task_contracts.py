"""Version 1 task/config/result vocabulary shared by API and agent.

Pure contracts only: no file reads, command execution, provider access or activation.
Paths are literal workspace-relative names; executors must additionally enforce
symlink/containment and byte limits when materializing them (B10).
"""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.review_contracts import ProviderUsage
from app.security_contracts import RunnerUsage

TASK_CONTRACT_VERSION = 1
LegacyTaskType = Literal["ci_review", "security_analysis"]
LegacyAgentTask = Literal["review", "security"]
LEGACY_AGENT_TASKS = {"ci_review": "review", "security_analysis": "security"}
TaskType = Literal[
    "ci_review", "security_analysis", "test_generation", "ci_failure_diagnosis", "other"
]
ExecutionMode = Literal["single_call", "opencode"]
ResultKind = Literal["findings", "report", "patch"]
ValidationStatus = Literal["passed", "failed", "not_run", "unavailable"]

# (task, mode, language, propose_fix) -> capability, allowed result kinds, writes.
PROFILES = {
    ("ci_review", "single_call", None, False): ("ci_review", ("findings",), False),
    ("security_analysis", "opencode", None, False): (
        "security_analysis",
        ("findings",),
        False,
    ),
    ("test_generation", "opencode", "python", False): (
        "test_generation_python",
        ("patch",),
        True,
    ),
    ("test_generation", "opencode", "node", False): (
        "test_generation_node",
        ("patch",),
        True,
    ),
    ("ci_failure_diagnosis", "opencode", None, False): (
        "diagnosis_readonly",
        ("report",),
        False,
    ),
    ("ci_failure_diagnosis", "opencode", None, True): (
        "diagnosis_fix",
        ("report", "patch"),
        True,
    ),
    ("other", "single_call", None, False): ("custom_single_call", ("report",), False),
    ("other", "opencode", None, False): ("custom_opencode", ("report", "patch"), True),
}


def profile_for(task, mode, language=None, propose_fix=False):
    try:
        return PROFILES[(task, mode, language, propose_fix)]
    except KeyError:
        raise ValueError("Unsupported task, mode or capability profile") from None


def capability_for(task, mode, language=None, propose_fix=False):
    return profile_for(task, mode, language, propose_fix)[0]


class Contract(BaseModel):
    # Standalone: importing this module in the agent must not load backend schemas.
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


def relative_path(value: str) -> str:
    if (
        not re.fullmatch(r"[A-Za-z0-9_. /@+()-]+", value)
        or any(p in ("", ".", "..") for p in value.split("/"))
        or value != value.strip()
    ):
        raise ValueError("Path must be a literal relative workspace path")
    return value


Path = Annotated[
    str, Field(min_length=1, max_length=512), AfterValidator(relative_path)
]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Commit = Annotated[str, Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]


class InputSpec(Contract):
    diff: bool = True
    files: list[Path] = Field(default_factory=list, max_length=100)
    artifacts: list[Identifier] = Field(default_factory=list, max_length=20)
    max_bytes: int = Field(default=262144, ge=1, le=1048576)
    max_file_bytes: int = Field(default=65536, ge=1, le=262144)
    max_context_tokens: int = Field(default=32768, ge=1, le=131072)


class ResourceLimits(Contract):
    max_seconds: int = Field(default=600, ge=1, le=1800)
    max_tokens: int = Field(default=100000, ge=1, le=1000000)
    max_iterations: int = Field(default=20, ge=1, le=40)
    max_attempts: int = Field(default=1, ge=1, le=3)
    max_processes: int = Field(default=64, ge=1, le=128)
    cpu_millis: int = Field(default=2000, ge=100, le=4000)
    memory_mib: int = Field(default=2048, ge=128, le=4096)
    max_output_bytes: int = Field(default=1048576, ge=1, le=4194304)


class ValidationCommand(Contract):
    id: Identifier
    # This is maintainer configuration, never interpolated/generated shell text.
    argv: list[
        Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[^\x00\r\n]+$")]
    ] = Field(min_length=1, max_length=32)
    environment_image: str = Field(
        max_length=300, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:[a-f0-9]{64}$"
    )
    required: bool = True
    max_seconds: int = Field(default=120, ge=1, le=600)


class TestEnvironment(Contract):
    profile: Literal["pytest-v1", "node-test-v1"]
    dependency_lock: Path
    dependency_sha256: Digest


class TestEvidence(Contract):
    profile: Literal["pytest-v1", "node-test-v1"]
    dependency_sha256: Digest
    generated_paths: list[Path] = Field(min_length=1, max_length=100)
    existing_tests_discovered: int = Field(gt=0, le=10000)
    existing_tests_executed: int = Field(gt=0, le=10000)
    baseline_identity_sha256: Digest
    existing_identity_sha256: Digest


class DiagnosisConfiguration(Contract):
    stage: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$"
    )
    log_artifact: Path

    @model_validator(mode="after")
    def upstream_only(self):
        from app.diagnosis_contracts import AGENT_STAGE

        if self.stage.casefold().startswith("driftplain") or self.stage == AGENT_STAGE:
            raise ValueError("Cannot diagnose an agent stage")
        return self


class FailureContext(Contract):
    build_id: str = Field(min_length=1, max_length=255)
    stage: str = Field(min_length=1, max_length=100)
    commit: Commit
    exit_status: int | None = Field(default=None, ge=0, le=255)
    original_status: Literal["FAILURE", "SUCCESS", "ABORTED", "NOT_BUILT"]
    log_excerpt: str = Field(default="", max_length=8192)
    claim_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="after")
    def redacted(self):
        from app.diagnosis_contracts import redact

        self.log_excerpt = redact(self.log_excerpt)
        if len(self.log_excerpt.encode()) > 8192:
            raise ValueError("Redacted failure excerpt exceeds byte ceiling")
        return self


class TaskConfiguration(Contract):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=8000)
    instructions: str | None = Field(default=None, min_length=1, max_length=16000)
    inputs: InputSpec = Field(default_factory=InputSpec)
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    write_paths: list[Path] = Field(default_factory=list, max_length=30)
    validation_commands: list[ValidationCommand] = Field(
        default_factory=list, max_length=5
    )

    diagnosis: DiagnosisConfiguration | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    test_environment: TestEnvironment | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def distinct_commands(self):
        ids = [c.id for c in self.validation_commands]
        if len(ids) != len(set(ids)):
            raise ValueError("Validation command IDs must be unique")
        return self


def configure(task, mode, config=None, language=None, propose_fix=False):
    capability, kinds, writable = profile_for(task, mode, language, propose_fix)
    config = config or TaskConfiguration()
    if task == "other" and not (
        config.label and config.system_prompt and config.instructions
    ):
        raise ValueError("Other requires label, system prompt and task instructions")
    if task != "other" and (config.label or config.system_prompt):
        raise ValueError("Custom label and system prompt belong to Other")
    if not writable and (config.write_paths or config.validation_commands):
        raise ValueError(
            "Read-only profiles cannot configure writes or validation execution"
        )
    if writable and not config.write_paths:
        raise ValueError("Writable profiles require explicit permitted paths")
    if task == "test_generation" and not any(
        c.required for c in config.validation_commands
    ):
        raise ValueError("Test generation requires configured validation")
    if config.test_environment:
        if task != "test_generation":
            raise ValueError("Test environment belongs to named test generation")
        from app.test_generation_contracts import validate_test_environment

        validate_test_environment(config, language)
    if config.diagnosis:
        if (
            task != "ci_failure_diagnosis"
            or config.inputs.diff
            or config.inputs.artifacts
        ):
            raise ValueError(
                "Diagnosis uses selected files and its single configured log artifact"
            )
        if propose_fix:
            from app.diagnosis_contracts import validate_fix_paths

            validate_fix_paths(config.write_paths)
    if mode == "single_call":
        # Persist the actual one-generation limit, independent of prompt content.
        config = config.model_copy(
            update={
                "resources": config.resources.model_copy(
                    update={"max_iterations": 1, "max_attempts": 1}
                )
            }
        )
    return {
        "taskContractVersion": TASK_CONTRACT_VERSION,
        "taskConfiguration": config.model_dump(mode="json", by_alias=True),
        "capabilityPolicy": {
            "version": 1,
            "profile": capability,
            "workspace": "disposable" if writable else "readonly",
            "writePaths": config.write_paths,
            "validationExecutor": "credential_free"
            if config.validation_commands
            else None,
            "shell": False,
            "network": False,
            "hostAccess": False,
            "publish": False,
        },
        "resultKinds": list(kinds),
        "resultContractVersion": 1,
    }


class Artifact(Contract):
    id: Identifier
    kind: Literal["report", "patch", "validation_log", "proposed_code", "other"]
    path: Path
    sha256: Digest
    size_bytes: int = Field(ge=0, le=4194304)


class Report(Contract):
    cause: Literal["repository", "external", "unknown"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    no_patch_reason: str | None = Field(
        default=None, min_length=1, max_length=2000, exclude_if=lambda v: v is None
    )
    summary: str = Field(min_length=1, max_length=8000)
    uncertainty: str | None = Field(default=None, max_length=2000)
    next_steps: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=10
    )
    evidence_artifact_ids: list[Identifier] = Field(default_factory=list, max_length=20)


class ChangedFile(Contract):
    path: Path
    operation: Literal["added", "modified", "deleted"]


class Patch(Contract):
    sha256: Digest
    artifact_id: Identifier
    files: list[ChangedFile] = Field(min_length=1, max_length=100)


class ValidationCheck(Contract):
    command_id: Identifier
    status: ValidationStatus
    execution_revision_id: int = Field(gt=0)
    base_commit: Commit
    patch_sha256: Digest | None = None
    exit_code: int | None = Field(default=None, ge=-255, le=255)
    duration_ms: int | None = Field(default=None, ge=0, le=1800000)
    log_artifact_id: Identifier | None = None
    test_evidence: TestEvidence | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    generated_tests_discovered: int | None = Field(default=None, ge=0, le=1000000)
    generated_tests_executed: int | None = Field(default=None, ge=0, le=1000000)
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def outcome(self):
        if self.status in ("passed", "failed"):
            if (
                self.exit_code is None
                or self.duration_ms is None
                or self.log_artifact_id is None
            ):
                raise ValueError(
                    "Executed validation requires exit, duration and log identity"
                )
            if (self.status == "passed") != (self.exit_code == 0):
                raise ValueError("Validation status must agree with exit status")
        elif self.exit_code is not None or not self.reason:
            raise ValueError("Unexecuted validation needs a reason and no exit status")
        return self


class TaskResult(Contract):
    version: Literal[1]
    kind: ResultKind
    task: TaskType
    mode: ExecutionMode
    language: Literal["python", "node"] | None = None
    propose_fix: bool = False
    execution_status: Literal["completed", "failed", "timed_out", "refused", "skipped"]
    execution_reason: str | None = Field(default=None, max_length=2000)
    validation_status: ValidationStatus = "not_run"
    base_commit: Commit | None = None
    patch: Patch | None = None
    report: Report | None = None
    artifacts: list[Artifact] = Field(default_factory=list, max_length=30)
    validations: list[ValidationCheck] = Field(default_factory=list, max_length=5)
    failure: FailureContext | None = Field(default=None, exclude_if=lambda v: v is None)
    # B8 additive evidence; persisted inside the already immutable JSON envelope.
    provider_usage: ProviderUsage | None = None
    runner_usage: RunnerUsage | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def consistent_result(self):
        if self.failure and self.report:
            from app.diagnosis_contracts import redact

            self.report = self.report.model_copy(
                update={
                    "summary": redact(self.report.summary),
                    "uncertainty": redact(self.report.uncertainty)
                    if self.report.uncertainty
                    else None,
                    "next_steps": [redact(x) for x in self.report.next_steps],
                    "no_patch_reason": redact(self.report.no_patch_reason)
                    if self.report.no_patch_reason
                    else None,
                }
            )
        if self.provider_usage and self.runner_usage:
            raise ValueError("Native and runner usage cannot coexist")
        if self.execution_status != "completed" and not self.execution_reason:
            raise ValueError("Incomplete execution requires an explicit reason")
        _, kinds, _ = profile_for(self.task, self.mode, self.language, self.propose_fix)
        if self.kind not in kinds:
            raise ValueError("Result kind does not match task profile")
        artifacts = {a.id: a for a in self.artifacts}
        if len(artifacts) != len(self.artifacts):
            raise ValueError("Artifact IDs must be unique")
        if (
            self.kind == "patch"
            and self.execution_status == "completed"
            and not self.patch
        ):
            raise ValueError("Completed patch result needs patch identity")
        if self.patch:
            a = artifacts.get(self.patch.artifact_id)
            if (
                self.kind != "patch"
                or not self.base_commit
                or not a
                or a.kind != "patch"
                or a.sha256 != self.patch.sha256
            ):
                raise ValueError(
                    "Patch requires matching base commit and artifact digest"
                )
        if any(
            a.kind == "patch" and (not self.patch or a.id != self.patch.artifact_id)
            for a in self.artifacts
        ):
            raise ValueError("Patch artifacts require an explicit patch identity")
        if (
            self.kind != "findings"
            and self.execution_status == "completed"
            and not self.report
        ):
            raise ValueError("Completed report/patch needs a bounded summary")
        if self.report and any(
            i not in artifacts for i in self.report.evidence_artifact_ids
        ):
            raise ValueError("Unknown report evidence artifact")
        ids = [v.command_id for v in self.validations]
        if len(ids) != len(set(ids)):
            raise ValueError("Validation command IDs must be unique")
        for v in self.validations:
            if v.base_commit != self.base_commit or v.patch_sha256 != (
                self.patch.sha256 if self.patch else None
            ):
                raise ValueError(
                    "Validation must identify the final base commit and patch"
                )
            if v.log_artifact_id:
                a = artifacts.get(v.log_artifact_id)
                if not a or a.kind != "validation_log":
                    raise ValueError("Validation log must reference its manifest entry")
        statuses = [v.status for v in self.validations]
        expected = (
            "failed"
            if "failed" in statuses
            else "unavailable"
            if "unavailable" in statuses
            else "not_run"
            if not statuses or "not_run" in statuses
            else "passed"
        )
        if self.validation_status != expected:
            raise ValueError(
                "Validation summary must agree with actual checks; missing is not passed"
            )
        return self


def validate_result_configuration(
    result: TaskResult, configuration: dict, revision_id: int, gate: str
):
    """Bind supplied metadata to the immutable executed configuration, not current state."""
    p = configuration["policy"]
    if (result.task, result.mode, result.language, result.propose_fix) != (
        p["task"],
        p["mode"],
        p["language"],
        p["proposeFix"],
    ):
        raise ValueError("Reported task profile differs from executed configuration")
    if configuration.get("taskContractVersion") != 1:
        raise ValueError("This revision predates the task result contract")
    c = TaskConfiguration.model_validate(configuration["taskConfiguration"])
    if result.failure:
        if result.task != "ci_failure_diagnosis" or not c.diagnosis or gate != "fail":
            raise ValueError(
                "Failure evidence requires configured diagnosis and a failing gate"
            )
        if result.failure.commit != result.base_commit:
            raise ValueError("Failure commit differs from executed base")
        if result.execution_status == "completed":
            if (
                not result.failure.claim_id
                or result.failure.original_status != "FAILURE"
                or result.failure.exit_status in (None, 0)
                or not result.failure.log_excerpt
                or result.failure.stage != c.diagnosis.stage
            ):
                raise ValueError(
                    "Completed diagnosis requires claimed upstream failure evidence"
                )
            if (
                not result.report
                or not result.report.uncertainty
                or not result.report.cause
                or not result.report.next_steps
            ):
                raise ValueError(
                    "Diagnosis requires explicit cause classification and uncertainty"
                )
            import hashlib

            evidence = next(
                (a for a in result.artifacts if a.id == "failure-log"), None
            )
            raw = result.failure.log_excerpt.encode()
            if (
                result.report.evidence_artifact_ids != ["failure-log"]
                or evidence is None
                or evidence.kind != "other"
                or evidence.size_bytes != len(raw)
                or evidence.sha256 != hashlib.sha256(raw).hexdigest()
            ):
                raise ValueError(
                    "Diagnosis evidence must bind the retained redacted log excerpt"
                )
            if (
                not result.runner_usage
                or result.runner_usage.attempts != 1
                or result.runner_usage.completed_steps < 1
            ):
                raise ValueError(
                    "Completed diagnosis requires captured runner evidence"
                )
            if not result.patch and not result.report.no_patch_reason:
                raise ValueError("No patch requires an explanation")
            if result.patch and result.report.cause != "repository":
                raise ValueError("External or unknown causes cannot propose a patch")
        if result.patch:
            from app.diagnosis_contracts import validate_fix_paths

            validate_fix_paths([f.path for f in result.patch.files])
            if any(f.operation != "modified" for f in result.patch.files):
                raise ValueError("Repair v1 modifies existing production files only")
    elif c.diagnosis and result.task == "ci_failure_diagnosis":
        raise ValueError("Configured diagnosis requires failure context")
    usage = result.provider_usage
    if usage:
        if result.task not in ("ci_review", "other") or result.mode != "single_call":
            raise ValueError("Native usage requires a single-call profile")
        if (usage.provider, usage.profile_version) != (
            configuration["model"]["provider"],
            configuration["runtimeVersion"],
        ):
            raise ValueError("Usage differs from executed provider profile")
        if result.execution_status == "completed" and (
            usage.total_tokens is None or usage.generation_requests != 1
        ):
            raise ValueError("Completed review requires complete usage")
        if (
            gate == "pass"
            and max(usage.captured_tokens, usage.reported_total_tokens or 0)
            > c.resources.max_tokens
        ):
            raise ValueError("Usage exceeding the configured ceiling cannot pass")
    runner = result.runner_usage
    if runner:
        if (
            result.task
            not in (
                "security_analysis",
                "other",
                "test_generation",
                "ci_failure_diagnosis",
            )
            or result.mode != "opencode"
        ):
            raise ValueError("Runner usage requires an OpenCode profile")
        if (runner.provider, runner.profile_version) != (
            configuration["model"]["provider"],
            configuration["runtimeVersion"],
        ):
            raise ValueError("Runner usage differs from executed provider profile")
        if result.execution_status == "completed" and not runner.complete:
            raise ValueError("Completed security requires complete runner events")
        if (
            gate == "pass"
            or result.task == "ci_failure_diagnosis"
            and result.execution_status == "completed"
        ) and (
            max(runner.captured_tokens, runner.reported_total_tokens or 0)
            > c.resources.max_tokens
            or runner.completed_steps > c.resources.max_iterations
            or runner.tool_calls > c.resources.max_iterations
            or runner.attempts > c.resources.max_attempts
        ):
            raise ValueError("Runner usage exceeds configured ceiling")
    if result.patch and any(
        not any(
            f.path == path or f.path.startswith(path + "/") for path in c.write_paths
        )
        for f in result.patch.files
    ):
        raise ValueError(
            "Patch metadata contains paths outside the permitted write paths"
        )
    if result.task == "test_generation" and result.patch:
        from app.test_generation_contracts import validate_test_paths

        validate_test_paths(result.language, result.patch.files)
    if sum(a.size_bytes for a in result.artifacts) > c.resources.max_output_bytes:
        raise ValueError("Artifact manifest exceeds configured output ceiling")
    commands = {v.id: v for v in c.validation_commands}
    checks = {v.command_id: v for v in result.validations}
    for v in result.validations:
        if v.execution_revision_id != revision_id or v.command_id not in commands:
            raise ValueError(
                "Validation does not belong to this configuration and command"
            )
        if (
            v.status == "passed"
            and v.duration_ms is not None
            and v.duration_ms > commands[v.command_id].max_seconds * 1000
        ):
            raise ValueError("Validation exceeding the configured ceiling cannot pass")
    if result.validation_status == "passed" and set(commands) != set(checks):
        raise ValueError("Passed validation must account for every configured command")
    for v in result.validations:
        e = v.test_evidence
        if e:
            if (
                result.task != "test_generation"
                or not c.test_environment
                or not result.patch
            ):
                raise ValueError(
                    "Named test evidence requires its environment and patch"
                )
            if (e.profile, e.dependency_sha256) != (
                c.test_environment.profile,
                c.test_environment.dependency_sha256,
            ):
                raise ValueError(
                    "Test evidence differs from the configured environment"
                )
            if sorted(e.generated_paths) != sorted(f.path for f in result.patch.files):
                raise ValueError(
                    "Test evidence must cover exactly the final generated paths"
                )
            if (
                e.baseline_identity_sha256 != e.existing_identity_sha256
                or e.existing_tests_discovered != e.existing_tests_executed
            ):
                raise ValueError(
                    "Existing test identities and execution must be preserved"
                )
            if (
                not v.generated_tests_discovered
                or v.generated_tests_discovered != v.generated_tests_executed
            ):
                raise ValueError("Every discovered generated test must execute")
    if gate == "pass":
        if result.execution_status != "completed":
            raise ValueError("Incomplete execution cannot pass")
        if any(
            v.required and (v.id not in checks or checks[v.id].status != "passed")
            for v in commands.values()
        ):
            raise ValueError("Required validation did not pass")
        if result.validation_status in ("failed", "unavailable"):
            raise ValueError("Failed or unavailable validation cannot pass")
        if result.task == "ci_failure_diagnosis":
            raise ValueError("Diagnosis must preserve the original failed build gate")
        if result.task == "test_generation" and (
            not result.patch
            or not any(
                v.status == "passed"
                and v.test_evidence is not None
                and (v.generated_tests_discovered or 0) > 0
                and (v.generated_tests_executed or 0) > 0
                for v in result.validations
            )
        ):
            raise ValueError(
                "Test generation requires discovered and executed new tests"
            )


def validate_execution_config(body: dict, project_id: int):
    """Reject unsupported negotiation or authority inconsistent with the shared registry."""
    expected_keys = {
        "contractVersion",
        "taskContractVersion",
        "resultContractVersion",
        "projectId",
        "executionRevisionId",
        "selectionId",
        "runtimeId",
        "runtimeVersion",
        "catalogModelId",
        "deploymentId",
        "taskType",
        "executionMode",
        "capability",
        "policy",
        "model",
        "reviewPreferences",
        "taskConfiguration",
        "capabilityPolicy",
        "resultKinds",
    }
    if set(body) != expected_keys:
        raise ValueError("Unsupported configuration fields")
    for key, version in (
        ("contractVersion", 2),
        ("taskContractVersion", 1),
        ("resultContractVersion", 1),
    ):
        if type(body[key]) is not int or body[key] != version:
            raise ValueError("Unsupported execution contract")
    if (
        body["projectId"] != project_id
        or type(body["executionRevisionId"]) is not int
        or body["executionRevisionId"] < 1
    ):
        raise ValueError("Invalid execution identity")
    p = body["policy"]
    capability = capability_for(p["task"], p["mode"], p["language"], p["proposeFix"])
    if (body["taskType"], body["executionMode"], body["capability"]) != (
        p["task"],
        p["mode"],
        capability,
    ):
        raise ValueError("Inconsistent profile")
    config = TaskConfiguration.model_validate(body["taskConfiguration"])
    expected = configure(p["task"], p["mode"], config, p["language"], p["proposeFix"])
    for key, value in expected.items():
        if body[key] != value:
            raise ValueError("Inconsistent execution authority")
    for name in ("runtimeId", "selectionId", "catalogModelId", "deploymentId"):
        if type(body[name]) is not int or body[name] < 1:
            raise ValueError("Invalid runtime identity")
    model = body["model"]
    if set(model) != {
        "name",
        "provider",
        "providerModelId",
        "authMode",
        "credentialEnvVar",
    }:
        raise ValueError("Unsupported model fields")
    if not all(
        isinstance(model[k], str) and 0 < len(model[k]) <= 255
        for k in ("name", "provider", "providerModelId")
    ):
        raise ValueError("Invalid model identity")
    if not isinstance(body["runtimeVersion"], str) or not body["runtimeVersion"]:
        raise ValueError("Missing runtime version")
    if model["authMode"] == "api_key":
        if not isinstance(model["credentialEnvVar"], str) or not re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,127}", model["credentialEnvVar"]
        ):
            raise ValueError("Invalid credential variable name")
    elif model["authMode"] != "aws_iam" or model.get("credentialEnvVar") is not None:
        raise ValueError("Unsupported authentication contract")
