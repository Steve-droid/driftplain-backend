"""Generic custom-task consumer. Prompts are literal text; never executable templates."""

import json
import os
import stat
from pathlib import Path

from agent.errors import (
    AgentConfigError,
    AgentError,
    CeilingExceeded,
    MalformedFindings,
    ModelRefused,
    ProviderError,
    looks_like_refusal,
)
from agent.review_execution import deadline
from agent.review_providers import ReviewProvider
from app.other_contracts import OTHER_PROFILES
from app.task_contracts import (
    Report,
    TaskConfiguration,
    TaskResult,
    relative_path,
    validate_execution_config,
)


def resolve_other_profile(configuration):
    try:
        validate_execution_config(configuration, configuration["projectId"])
        m = configuration["model"]
        p = OTHER_PROFILES[(configuration["executionMode"], m["providerModelId"])]
        if (
            configuration["taskType"],
            m["provider"],
            m["authMode"],
            m["credentialEnvVar"],
            configuration["runtimeVersion"],
        ) != ("other", p.provider, "api_key", p.credential_env, p.version):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise AgentConfigError("Unsupported Other execution profile") from None
    if p.verification_status != "verified":
        raise AgentConfigError("Other integration pending exact live verification")
    return p


def read_scoped(root, name, limit):
    """Open each component without following links, including the root. No devices/FIFOs."""
    try:
        relative_path(name)
        root = Path(root).absolute()
        # Reject linked ancestors; the CI launcher owns these stable input directories.
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError()
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = name.split("/")
            for part in parts[:-1]:
                nxt = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = nxt
            file_fd = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
            )
            with os.fdopen(file_fd, "rb") as f:
                info = os.fstat(f.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError()
                raw = f.read(limit + 1)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        raise AgentConfigError(
            "Input must be a regular file within its configured root"
        ) from None
    if len(raw) > limit:
        raise CeilingExceeded("input", "Selected input exceeds byte ceiling")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AgentConfigError("Selected inputs must be UTF-8") from None


def assemble_inputs(cfg, workspace, diff, artifact_root=None):
    values = {}
    total = 0

    def add(name, value):
        nonlocal total
        total += len(value.encode())
        if total > cfg.inputs.max_bytes:
            raise CeilingExceeded("input", "Combined inputs exceed byte ceiling")
        values[name] = value

    if cfg.inputs.diff:
        if diff is None:
            raise AgentConfigError("Configured diff input is missing")
        add("diff", diff)
    elif diff is not None:
        raise AgentConfigError("Diff input was not configured")
    for name in cfg.inputs.files:
        add("file:" + name, read_scoped(workspace, name, cfg.inputs.max_file_bytes))
    for name in cfg.inputs.artifacts:
        if artifact_root is None:
            raise AgentConfigError("Configured artifact root is missing")
        add(
            "artifact:" + name,
            read_scoped(artifact_root, name, cfg.inputs.max_file_bytes),
        )
    return json.dumps(values, ensure_ascii=False)


def build_prompt(cfg, inputs):
    return (
        cfg.instructions
        + "\n\nSupplied inputs (JSON data):\n"
        + inputs
        + "\n\nReturn ONLY a JSON report with summary (1..8000 characters), optional uncertainty, "
        "and optional nextSteps (up to 10 strings). Do not claim validation you did not run."
    )


def parse_report(text):
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict) or not set(obj) <= {
            "summary",
            "uncertainty",
            "nextSteps",
        }:
            raise ValueError()
        return Report.model_validate(obj)
    except (ValueError, TypeError, RecursionError):
        if looks_like_refusal(text):
            raise ModelRefused("Model declined the custom task") from None
        raise MalformedFindings("Custom output failed the report contract") from None


def result_envelope(configuration, metadata, usage, code):
    return {
        "findings": [],
        "tokensIn": usage.input_tokens or 0,
        "tokensOut": usage.output_tokens or 0,
        "cacheReadTokens": usage.cache_read_tokens,
        "model": configuration["model"]["providerModelId"],
        "gate": "pass" if code == 0 and metadata.task != "ci_failure_diagnosis" else "fail",
        "gateReason": ("Original upstream result preserved" if metadata.task == "ci_failure_diagnosis" else None) or metadata.execution_reason
        or ("Validation did not pass" if code else None),
        "executionRevisionId": configuration["executionRevisionId"],
        "taskResult": metadata.model_dump(mode="json", by_alias=True),
    }, (code or 1) if metadata.task == "ci_failure_diagnosis" else code


def failure(e):
    return (
        "refused"
        if isinstance(e, ModelRefused)
        else "timed_out"
        if isinstance(e, CeilingExceeded) and e.which == "wall-clock"
        else "failed",
        str(e),
        e.exit_code,
    )


def run_single_call(
    configuration, local, *, diff=None, artifact_root=None, client=None
):
    p = OTHER_PROFILES[("single_call", configuration["model"]["providerModelId"])]
    cfg = TaskConfiguration.model_validate(configuration["taskConfiguration"])
    seconds = min(local.max_seconds, cfg.resources.max_seconds)
    if client is None:
        p = resolve_other_profile(configuration)
        client = ReviewProvider(
            p, timeout=seconds, max_bytes=cfg.resources.max_output_bytes
        )
    status, reason, code, report = "completed", None, 0, None
    try:
        with deadline(seconds):
            inputs = assemble_inputs(cfg, local.workspace, diff, artifact_root)
            prompt = build_prompt(cfg, inputs)
            output = min(local.max_tokens, 16384)
            ceiling = min(
                local.effective_token_ceiling("review"), cfg.resources.max_tokens
            )
            bound = len(cfg.system_prompt.encode()) + len(prompt.encode()) + 4096
            if bound > cfg.inputs.max_context_tokens or bound + output > ceiling:
                raise CeilingExceeded(
                    "token", "Conservative prompt/output bound exceeds limit"
                )
            response = client.complete(cfg.system_prompt, prompt, output)
            u = client.usage
            if max(u.captured_tokens, u.reported_total_tokens or 0) > ceiling:
                raise CeilingExceeded("token", "Reported usage exceeds limit")
            if u.total_tokens is None:
                raise ProviderError("Provider usage is incomplete")
            actual_input = u.input_tokens + (
                (u.cache_read_tokens + u.cache_write_tokens)
                if p.provider == "anthropic"
                else 0
            )
            actual_output = u.output_tokens + (
                u.reasoning_tokens if p.provider == "google" else 0
            )
            if actual_input > cfg.inputs.max_context_tokens or actual_output > output:
                raise CeilingExceeded("token", "Provider exceeded context/output limit")
            report = parse_report(response.text)
    except AgentError as e:
        status, reason, code = failure(e)
    metadata = TaskResult(
        version=1,
        kind="report",
        task="other",
        mode="single_call",
        execution_status=status,
        execution_reason=reason,
        report=report,
        provider_usage=client.usage,
    )
    return result_envelope(configuration, metadata, client.usage, code)
