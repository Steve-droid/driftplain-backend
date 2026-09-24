"""Explicit negotiated dispatch; no legacy fallback or implicit task conversion."""

import json
import time

from agent import review_execution
from agent.errors import AgentConfigError, CeilingExceeded
from agent.remote import build_ci_run_payload, fetch_execution_config, post_ci_run
from agent.review_execution import deadline, resolve_review_profile, run_review
from agent.security_execution import resolve_security_profile, run_security_execution
from app.task_contracts import TaskConfiguration


def explicit_main(config, path):
    from agent.__main__ import _check_image_task

    if not config.remote_configured:
        raise AgentConfigError(
            "Execution config opt-in requires API_URL + PROJECT_ID + CI_TOKEN"
        )
    if config.post_result and not config.build_id:
        raise AgentConfigError("Posting a result requires a build ID")
    token = config.ci_token.get_secret_value()
    body = fetch_execution_config(
        config.api_url, config.project_id, token, config.http_timeout
    )
    if body["taskType"] == "ci_review":
        _check_image_task("review")
        resolve_review_profile(body)
        cfg = TaskConfiguration.model_validate(body["taskConfiguration"])
        seconds = min(config.max_seconds, cfg.resources.max_seconds)
        started = time.monotonic()
        with deadline(seconds):
            diff = review_execution.read_diff(path, cfg.inputs.max_bytes)
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise CeilingExceeded("wall-clock", "review input deadline expired")
        result, code = run_review(
            diff, body, config.model_copy(update={"max_seconds": remaining})
        )
    elif body["taskType"] == "security_analysis":
        _check_image_task("security")
        if path:
            raise AgentConfigError("Security execution does not accept --diff")
        resolve_security_profile(body)
        result, code = run_security_execution(body, config)
    elif body["taskType"] in ("other", "test_generation"):
        from agent.other_execution import resolve_other_profile, run_single_call
        from agent.other_opencode import run_opencode

        _check_image_task(
            "review"
        )  # trusted launcher/single-call image, never the security CLI
        if body["taskType"] == "test_generation":
            from agent.test_generation import resolve_test_profile

            resolve_test_profile(body)
        else:
            resolve_other_profile(body)
        cfg = TaskConfiguration.model_validate(body["taskConfiguration"])
        seconds = min(config.max_seconds, cfg.resources.max_seconds)
        started = time.monotonic()
        diff = None
        if cfg.inputs.diff:
            with deadline(min(config.max_seconds, cfg.resources.max_seconds)):
                diff = review_execution.read_diff(path, cfg.inputs.max_bytes)
        elif path:
            raise AgentConfigError("Diff input was not configured")
        runner = (
            run_single_call if body["executionMode"] == "single_call" else run_opencode
        )
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise CeilingExceeded("wall-clock", "Other input deadline expired")
        result, code = runner(
            body,
            config.model_copy(update={"max_seconds": remaining}),
            diff=diff,
            artifact_root=config.input_artifacts,
        )
    else:
        raise AgentConfigError("Task has no explicit executable consumer")
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
