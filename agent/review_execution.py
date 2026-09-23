"""Opt-in B8 review runner. Strict v2 configuration, exact route, bounded one-call result."""

import json
import signal
import sys
import time
from contextlib import contextmanager

from pydantic import ValidationError

from agent.errors import (
    AgentConfigError,
    AgentError,
    CeilingExceeded,
    MalformedFindings,
    ModelRefused,
    ProviderError,
    looks_like_refusal,
)
from agent.review import (
    _log_agent_call,
    apply_gate,
    build_system_prompt,
    build_user_prompt,
)
from agent.review_providers import ReviewProvider
from app.review_contracts import REVIEW_PROFILES
from app.schemas.ci import IngestFinding
from app.task_contracts import TaskConfiguration, TaskResult, validate_execution_config


def resolve_review_profile(configuration):
    try:
        validate_execution_config(configuration, configuration["projectId"])
        if (configuration["taskType"], configuration["executionMode"]) != (
            "ci_review",
            "single_call",
        ):
            raise ValueError("unsupported task")
        m = configuration["model"]
        p = REVIEW_PROFILES[m["providerModelId"]]
        if (
            m["provider"],
            m["authMode"],
            m["credentialEnvVar"],
            configuration["runtimeVersion"],
        ) != (p.provider, "api_key", p.credential_env, p.version):
            raise ValueError("unrecognized profile")
        inputs = configuration["taskConfiguration"]["inputs"]
        if not inputs["diff"] or inputs["files"] or inputs["artifacts"]:
            raise ValueError("B8 accepts diff input only")
    except (ValueError, TypeError, KeyError):
        raise AgentConfigError(
            "Unsupported review execution profile or input options"
        ) from None
    if p.verification_status != "verified":
        raise AgentConfigError("Review integration pending exact live verification")
    return p


@contextmanager
def deadline(seconds):
    """Linux/macOS CLI main-thread wall deadline; no thread left calling after timeout."""

    def expired(*_):
        raise CeilingExceeded("wall-clock", "review deadline expired")

    previous = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer[0]:
        raise AgentConfigError("Review cannot replace an active process timer")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def read_diff(path, max_bytes, *, stdin=None):
    if path:
        with open(path, "rb") as f:
            raw = f.read(max_bytes + 1)
    else:
        raw = (stdin if stdin is not None else sys.stdin.buffer).read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise CeilingExceeded("input", "diff exceeds configured byte limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AgentConfigError("Diff must be UTF-8") from None


def _findings(text):
    # Unlike the legacy parser, the negotiated profile accepts ONLY the exact object.
    try:
        obj = json.loads(text)
        if (
            not isinstance(obj, dict)
            or set(obj) != {"findings"}
            or not isinstance(obj["findings"], list)
            or len(obj["findings"]) > 500
        ):
            raise ValueError("shape")
        findings = []
        for f in obj["findings"]:
            if not isinstance(f, dict) or not set(f) <= {
                "severity",
                "category",
                "file",
                "line",
                "message",
            }:
                raise ValueError("fields")
            findings.append(IngestFinding.model_validate(f))
        return findings
    except (ValueError, TypeError, ValidationError, RecursionError):
        if looks_like_refusal(text):
            raise ModelRefused("Model declined the review") from None
        raise MalformedFindings(
            "Model output did not satisfy the review findings contract"
        ) from None


def run_review(diff, configuration, local, *, client=None):
    # Injected client is a unit seam; normal CLI always checks the activation gate.
    p = REVIEW_PROFILES[configuration["model"]["providerModelId"]]
    cfg = TaskConfiguration.model_validate(configuration["taskConfiguration"])
    seconds = min(local.max_seconds, cfg.resources.max_seconds)
    if client is None:
        p = resolve_review_profile(configuration)
        client = ReviewProvider(
            p, timeout=seconds, max_bytes=cfg.resources.max_output_bytes
        )
    started = time.monotonic()
    findings, reason, status, code, error_kind = [], None, "completed", 0, None
    try:
        if len(diff.encode()) > cfg.inputs.max_bytes:
            raise CeilingExceeded("input", "diff exceeds configured byte limit")
        system = build_system_prompt(configuration["reviewPreferences"])
        user = build_user_prompt(diff)
        if cfg.instructions:
            user += "\n\nProject instructions (literal):\n" + cfg.instructions
        # UTF-8 byte bound plus conservative protocol reserve, not chars/4 or truncation.
        input_bound = len(system.encode()) + len(user.encode()) + 4096
        output_limit = min(local.max_tokens, 16384)
        token_limit = min(
            local.effective_token_ceiling("review"), cfg.resources.max_tokens
        )
        if (
            input_bound > cfg.inputs.max_context_tokens
            or input_bound + output_limit > token_limit
        ):
            raise CeilingExceeded(
                "token", "conservative input/output bound exceeds configured limit"
            )
        with deadline(seconds):
            response = client.complete(system, user, output_limit)
        u = client.usage
        if max(u.captured_tokens, u.reported_total_tokens or 0) > token_limit:
            raise CeilingExceeded("token", "reported usage exceeds configured limit")
        if u.total_tokens is None:
            raise ProviderError("Provider usage is incomplete; review cannot pass")
        actual_input = u.input_tokens + (
            (u.cache_read_tokens + u.cache_write_tokens)
            if p.provider == "anthropic"
            else 0
        )
        actual_output = u.output_tokens + (
            u.reasoning_tokens if p.provider == "google" else 0
        )
        if actual_input > cfg.inputs.max_context_tokens or actual_output > output_limit:
            raise CeilingExceeded(
                "token", "provider exceeded context/output token limit"
            )
        findings = _findings(response.text)
    except AgentError as e:
        reason, code = str(e), e.exit_code
        error_kind = type(e).__name__
        status = (
            "refused"
            if isinstance(e, ModelRefused)
            else "timed_out"
            if isinstance(e, CeilingExceeded) and e.which == "wall-clock"
            else "failed"
        )
    u = client.usage
    _log_agent_call(
        model=p.model,
        provider=p.provider,
        tokens_in=u.input_tokens or 0,
        tokens_out=u.output_tokens or 0,
        latency_ms=int((time.monotonic() - started) * 1000),
        status="ok" if status == "completed" else "error",
        error_kind=error_kind,
    )
    gate, gate_reason = (
        apply_gate(findings, local.effective_fail_severities("review"))
        if status == "completed"
        else ("fail", reason)
    )
    if status == "completed":
        code = 0 if gate == "pass" else 1
    metadata = TaskResult(
        version=1,
        kind="findings",
        task="ci_review",
        mode="single_call",
        execution_status=status,
        execution_reason=reason,
        provider_usage=u,
    )
    return {
        "findings": [f.model_dump(mode="json", by_alias=True) for f in findings],
        "tokensIn": u.input_tokens or 0,
        "tokensOut": u.output_tokens or 0,
        "cacheReadTokens": u.cache_read_tokens,
        "model": p.model,
        "gate": gate,
        "gateReason": gate_reason,
        "executionRevisionId": configuration["executionRevisionId"],
        "taskResult": metadata.model_dump(mode="json", by_alias=True),
    }, code


def explicit_main(config, path):
    from agent.__main__ import _check_image_task
    from agent.remote import build_ci_run_payload, fetch_execution_config, post_ci_run

    if not config.remote_configured:
        raise AgentConfigError(
            "Execution config opt-in requires API_URL + PROJECT_ID + CI_TOKEN"
        )
    if config.post_result and not config.build_id:
        raise AgentConfigError("Posting a result requires a build ID")
    _check_image_task("review")
    token = config.ci_token.get_secret_value()
    body = fetch_execution_config(
        config.api_url, config.project_id, token, config.http_timeout
    )
    resolve_review_profile(body)
    c = TaskConfiguration.model_validate(body["taskConfiguration"])
    seconds = min(config.max_seconds, c.resources.max_seconds)
    started = time.monotonic()
    with deadline(seconds):
        diff = read_diff(path, c.inputs.max_bytes)
    remaining = seconds - (time.monotonic() - started)
    if remaining <= 0:
        raise CeilingExceeded("wall-clock", "review input deadline expired")
    result, code = run_review(
        diff, body, config.model_copy(update={"max_seconds": remaining})
    )
    print(json.dumps(result))
    if config.post_result:
        post_ci_run(
            config.api_url,
            config.project_id,
            token,
            build_ci_run_payload(result, config.build_id),
            config.http_timeout,
        )
    return code
