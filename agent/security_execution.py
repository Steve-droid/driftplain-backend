"""Opt-in exact security execution. Legacy scans retain their existing contract."""

import json
import re
import tempfile
import time

from agent.errors import (
    AgentConfigError,
    AgentError,
    CeilingExceeded,
    MalformedFindings,
    ModelRefused,
    looks_like_refusal,
)
from agent.review import apply_gate
from agent.review_execution import deadline
from agent.security import build_task, load_system_prompt, map_severity
from agent.security_profile import (
    check_launch_boundary,
    inspect_workspace,
    isolated_environment,
)
from agent.security_stream import run_stream
from app.schemas.ci import IngestFinding
from app.security_contracts import SECURITY_PROFILES, RunnerUsage
from app.task_contracts import TaskConfiguration, TaskResult, validate_execution_config


def resolve_security_profile(configuration):
    try:
        validate_execution_config(configuration, configuration["projectId"])
        m = configuration["model"]
        p = SECURITY_PROFILES[m["providerModelId"]]
        if (
            configuration["taskType"],
            configuration["executionMode"],
            m["provider"],
            m["authMode"],
            m["credentialEnvVar"],
            configuration["runtimeVersion"],
        ) != (
            "security_analysis",
            "opencode",
            p.provider,
            "api_key",
            p.credential_env,
            p.version,
        ):
            raise ValueError()
        inputs = configuration["taskConfiguration"]["inputs"]
        if inputs["diff"] or inputs["files"] or inputs["artifacts"]:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise AgentConfigError(
            "Unsupported security execution profile or input options"
        ) from None
    if p.verification_status != "verified":
        raise AgentConfigError("Security integration pending exact live verification")
    return p


def parse_findings(text, files=None):
    try:
        obj = json.loads(text)
        if (
            not isinstance(obj, dict)
            or not set(obj) <= {"version", "results"}
            or not isinstance(obj.get("results"), list)
            or len(obj["results"]) > 500
        ):
            raise ValueError()
        findings = []
        for row in obj["results"]:
            extra, meta = row["extra"], row["extra"]["metadata"]
            path, line = row["path"], row["start"]["line"]
            cwes = meta["cwe"]
            if (
                not isinstance(cwes, list)
                or not cwes
                or any(
                    not isinstance(c, str)
                    or not re.fullmatch(r"CWE-[1-9][0-9]*(?:: [^\r\n]{1,180})?", c)
                    for c in cwes
                )
                or extra["severity"] not in {"ERROR", "WARNING", "INFO"}
                or meta["confidence"] not in {"HIGH", "MEDIUM", "LOW"}
                or type(line) is not int
                or line < 1
                or (files is not None and path not in files)
            ):
                raise ValueError()
            findings.append(
                IngestFinding(
                    severity=map_severity(extra["severity"], meta["confidence"]),
                    category="security",
                    file=path,
                    line=line,
                    message=extra["message"],
                    cwe=cwes[0],
                )
            )
        return findings
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        if looks_like_refusal(text):
            raise ModelRefused("Model declined the security audit") from None
        raise MalformedFindings("Security findings failed validation") from None


def run_security_execution(configuration, local, *, stream_result=None):
    # Injection is an offline test seam, never a CLI/HTTP option.
    p = SECURITY_PROFILES[configuration["model"]["providerModelId"]]
    cfg = TaskConfiguration.model_validate(configuration["taskConfiguration"])
    seconds = min(local.max_seconds, cfg.resources.max_seconds)
    tokens = min(local.effective_token_ceiling("security"), cfg.resources.max_tokens)
    steps = min(local.max_steps, cfg.resources.max_iterations)
    output = min(local.max_tokens, 16384, tokens)
    files = None
    attempts = 0
    r = stream_result
    status, reason, code, findings = "completed", None, 0, []
    if r is None:
        p = resolve_security_profile(configuration)
    try:
        if r is None:
            started = time.monotonic()
            with deadline(seconds):
                check_launch_boundary(local.workspace, cfg.resources)
                files = inspect_workspace(local.workspace, cfg.inputs)
                task = build_task(load_system_prompt(None))
                if cfg.instructions:
                    task += "\nProject instructions (literal):\n" + cfg.instructions
                if len(task.encode()) + 4096 + output > min(
                    tokens, cfg.inputs.max_context_tokens
                ):
                    raise CeilingExceeded(
                        "token",
                        "security prompt/output exceeds configured token budget",
                    )
            with tempfile.TemporaryDirectory(prefix="driftplain-security-") as home:
                env = isolated_environment(p, cfg, home, output, steps)
                cmd = [
                    "/usr/local/bin/opencode",
                    "run",
                    "--format",
                    "json",
                    "--agent",
                    "audit",
                    "--title",
                    "Driftplain security audit",
                    "-m",
                    p.route,
                    task,
                ]
                attempts = 1
                # Stream owns the remaining deadline and preserves captured usage on timeout.
                r = run_stream(
                    cmd,
                    local.workspace,
                    env=env,
                    max_seconds=max(0.001, seconds - (time.monotonic() - started)),
                    max_tokens=tokens,
                    max_steps=steps,
                    max_tools=steps,
                    max_bytes=cfg.resources.max_output_bytes,
                    max_context=cfg.inputs.max_context_tokens,
                    max_output_tokens=output,
                )
        else:
            attempts = 1
        code, reason = r.code, r.reason
        if code:
            status = "timed_out" if r.timed_out else "failed"
        else:
            findings = parse_findings(r.text, files)
    except AgentError as e:
        code, reason = e.exit_code, str(e)
        status = (
            "refused"
            if isinstance(e, ModelRefused)
            else "timed_out"
            if isinstance(e, CeilingExceeded) and e.which == "wall-clock"
            else "failed"
        )
    counts = r.counts if r else {}
    usage = RunnerUsage(
        provider=p.provider,
        profile_version=p.version,
        input_tokens=counts.get("input"),
        output_tokens=counts.get("output"),
        reasoning_tokens=counts.get("reasoning"),
        cache_read_tokens=counts.get("cache_read"),
        cache_write_tokens=counts.get("cache_write"),
        reported_total_tokens=counts.get("total"),
        attempts=attempts,
        completed_steps=r.steps if r else 0,
        tool_calls=r.tool_calls if r else 0,
    )
    gate, gate_reason = (
        apply_gate(
            findings,
            sorted(set(local.effective_fail_severities("security")) | {"critical"}),
        )
        if status == "completed"
        else ("fail", reason)
    )
    if status == "completed":
        code = 0 if gate == "pass" else 1
    metadata = TaskResult(
        version=1,
        kind="findings",
        task="security_analysis",
        mode="opencode",
        execution_status=status,
        execution_reason=reason,
        runner_usage=usage,
    )
    return {
        "findings": [f.model_dump(mode="json", by_alias=True) for f in findings],
        "tokensIn": usage.input_tokens or 0,
        "tokensOut": usage.output_tokens or 0,
        "cacheReadTokens": usage.cache_read_tokens,
        "model": p.model,
        "gate": gate,
        "gateReason": gate_reason,
        "executionRevisionId": configuration["executionRevisionId"],
        "taskResult": metadata.model_dump(mode="json", by_alias=True),
    }, code
