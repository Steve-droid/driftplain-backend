"""Trusted orchestration of a disposable editor and separate networkless validation.

Only this launcher may access the engine. No model-authored command, path, image,
environment or mount is accepted by the validation protocol.
"""

import json
import os
import re
import time
import uuid
from pathlib import Path

from agent.bounded_process import run_bounded
from agent.errors import AgentConfigError, AgentError, CeilingExceeded
from agent.other_execution import (
    assemble_inputs,
    build_prompt,
    failure,
    parse_report,
    read_scoped,
    resolve_other_profile,
    result_envelope,
)
from agent.other_profile import ALLOWED_TOOLS
from agent.other_workspace import DisposableWorkspace, inspect_tree
from agent.security_stream import run_stream
from agent.validation_executor import DockerValidationExecutor
from app.other_contracts import OTHER_PROFILES
from app.security_contracts import RunnerUsage
from app.task_contracts import TaskConfiguration, TaskResult, ValidationCheck


def validation_status(checks):
    statuses = {c.status for c in checks}
    return (
        "failed"
        if "failed" in statuses
        else "unavailable"
        if "unavailable" in statuses
        else "not_run"
        if not statuses or "not_run" in statuses
        else "passed"
    )


class DockerEditor:
    def __init__(self, image):
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}", image or ""
        ):
            raise AgentConfigError(
                "Other OpenCode requires a maintainer-pinned editor image digest"
            )
        self.image = image

    def run(self, work, configuration, prompt, local, validate, remaining):
        if configuration["taskType"] == "test_generation":
            from agent.test_generation import profile

            p = profile(configuration)
        else:
            p = OTHER_PROFILES[("opencode", configuration["model"]["providerModelId"])]
        cfg = work.cfg
        r = cfg.resources
        name = "driftplain-editor-" + uuid.uuid4().hex
        bridge = work.root / "bridge"
        control = work.root / "control"
        if os.getuid() == 0:
            raise AgentConfigError("Other launcher must run as a non-root CI user")
        bridge.mkdir()
        bridge.chmod(0o700)
        control.mkdir()
        control.chmod(0o755)
        output = min(local.max_tokens, 16384, r.max_tokens)
        steps = min(local.max_steps, r.max_iterations)
        body = {
            **configuration,
            "prompt": prompt,
            "outputTokens": output,
            "steps": steps,
        }
        (control / "job.json").write_text(json.dumps(body))
        (control / "job.json").chmod(0o444)
        args = [
            "docker",
            "run",
            "--name",
            name,
            "--pull=never",
            "--platform=linux/amd64",
            "--read-only",
            f"--user={os.getuid()}:{os.getgid()}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={r.max_processes}",
            f"--memory={r.memory_mib}m",
            f"--memory-swap={r.memory_mib}m",
            f"--cpus={r.cpu_millis / 1000}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m",
            "--mount",
            f"type=bind,src={work.path},dst=/workspace",
            "--mount",
            f"type=bind,src={bridge},dst=/bridge",
            "--mount",
            f"type=bind,src={control},dst=/control,readonly",
            "-e",
            p.credential_env,
            "--entrypoint",
            "/venv/bin/python",
            self.image,
            "-m",
            "agent.other_profile",
        ]
        key = os.environ.get(p.credential_env)
        if not key:
            raise AgentConfigError("Selected provider credential is missing")
        # docker client receives only its executable path and selected key; no CI credentials.
        env = {"PATH": os.environ.get("PATH", os.defpath), p.credential_env: key}
        seen = set()
        poll_at = 0

        def control_command(action):
            result = run_bounded(["docker", action, name], seconds=10, max_bytes=8192)
            if result.code or result.reason:
                raise AgentConfigError("Editor boundary control failed")

        def tick():
            nonlocal poll_at
            now = time.monotonic()
            if now < poll_at:
                return
            poll_at = now + 0.1
            if not (bridge / "request.json").exists():
                return
            req = json.loads(read_scoped(bridge, "request.json", 1024))
            if (
                set(req) != {"version", "nonce"}
                or req["version"] != 1
                or not re.fullmatch("[a-f0-9]{32}", req.get("nonce", ""))
            ):
                raise AgentConfigError("Invalid validation request")
            if req["nonce"] in seen:
                return
            if len(seen) >= steps:
                raise CeilingExceeded("tool", "Validation request ceiling exceeded")
            seen.add(req["nonce"])
            control_command("pause")
            try:
                # The entire editor container is frozen: no concurrent file mutation during checks.
                response = validate(remaining())
                raw = json.dumps({"nonce": req["nonce"], **response})
                tmp = bridge / "reply.tmp"
                fd = os.open(
                    tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644
                )
                with os.fdopen(fd, "w") as f:
                    f.write(raw)
                os.replace(tmp, bridge / "response.json")
            finally:
                control_command("unpause")

        try:
            return run_stream(
                args,
                str(work.root),
                env=env,
                max_seconds=remaining(),
                max_tokens=min(local.effective_token_ceiling("review"), r.max_tokens),
                max_steps=steps,
                max_tools=steps,
                max_bytes=r.max_output_bytes,
                max_context=cfg.inputs.max_context_tokens,
                max_output_tokens=output,
                allowed_tools=ALLOWED_TOOLS,
                tick=tick,
            )
        finally:
            # Force deletion terminates every container process, even after CLI failure.
            cleanup = run_bounded(
                ["docker", "rm", "-f", name], seconds=10, max_bytes=8192
            )
            if cleanup.code or cleanup.reason:
                work.preserve = True
                raise AgentConfigError(
                    "Editor cleanup unconfirmed; job scratch retained for operator cleanup"
                )


def run_opencode(
    configuration, local, *, diff=None, artifact_root=None, runner=None, executor=None
):
    named = configuration["taskType"] == "test_generation"
    if named:
        from agent import test_generation as task
        from agent.test_validation import TestValidationExecutor
        from app.test_generation_contracts import validate_test_environment

        p = task.profile(configuration)
    else:
        p = OTHER_PROFILES[("opencode", configuration["model"]["providerModelId"])]
    cfg = TaskConfiguration.model_validate(configuration["taskConfiguration"])
    if runner is None:
        p = (
            task.resolve_test_profile(configuration)
            if named
            else resolve_other_profile(configuration)
        )
        runner = DockerEditor(local.other_image)
    if named:
        try:
            validate_test_environment(cfg, p.language)
        except ValueError as e:
            raise AgentConfigError(str(e)) from None
    executor = executor or (
        TestValidationExecutor(cfg, p.language)
        if named
        else DockerValidationExecutor(cfg.resources)
    )
    # Output is a fresh job directory outside both execution containers and the original tree.
    if not isinstance(local.base_commit, str) or not re.fullmatch(
        r"(?:[a-f0-9]{40}|[a-f0-9]{64})", local.base_commit
    ):
        raise AgentConfigError("Other OpenCode requires an exact base commit")
    out = Path(local.output_dir).absolute()
    if out.exists() or any(x.is_symlink() for x in out.parents):
        raise AgentConfigError(
            "Other output must be a fresh directory outside the checkout"
        )
    out = out.resolve()
    source = Path(local.workspace).resolve()
    if out == source or source in out.parents:
        raise AgentConfigError("Other output must be outside the original checkout")
    out.mkdir(parents=True, mode=0o700)
    started = time.monotonic()
    seconds = min(local.max_seconds, cfg.resources.max_seconds)

    def remaining():
        return max(0, seconds - (time.monotonic() - started))

    stream = None
    attempts = 0
    report = None
    patch = None
    artifacts = []
    checks = []
    status, reason, code = "completed", None, 0
    try:
        with DisposableWorkspace(
            source, local.base_commit, cfg, max_seconds=remaining()
        ) as work:
            if named:
                task.prepare(work, executor, p.language)
            inputs = assemble_inputs(cfg, work.path, diff, artifact_root)
            prompt = (
                (task.prompt(cfg, inputs) if named else build_prompt(cfg, inputs))
                + "\nEdit only the configured write paths. Use validation_validate for configured checks. Never use shell, publish, or invent test results."
            )
            prompt += "\nPermitted write paths: " + json.dumps(cfg.write_paths)
            bound = (
                len(prompt.encode())
                + len((task.SYSTEM_PROMPT if named else cfg.system_prompt).encode())
                + 4096
            )
            if bound > cfg.inputs.max_context_tokens or bound + min(
                local.max_tokens, 16384
            ) > min(cfg.resources.max_tokens, local.effective_token_ceiling("review")):
                raise CeilingExceeded(
                    "token", "Conservative prompt/output bound exceeds limit"
                )

            def validate(budget):
                nonlocal patch, artifacts, checks
                patch, a = work.capture(out)
                artifacts = [a] if a else []
                checks = []
                if named:
                    task.check_patch(work, patch, p.language)
                    executor.generated = sorted(f.path for f in patch.files)
                digest = patch.sha256 if patch else None
                before = inspect_tree(
                    work.path,
                    cfg.resources.max_output_bytes,
                    cfg.resources.max_output_bytes,
                )
                for cmd in cfg.validation_commands:
                    if budget <= 0:
                        c = ValidationCheck(
                            command_id=cmd.id,
                            status="unavailable",
                            execution_revision_id=configuration["executionRevisionId"],
                            base_commit=local.base_commit,
                            patch_sha256=digest,
                            reason="Task wall-clock budget exhausted",
                        )
                        a = None
                    else:
                        c, a = executor.run(
                            work.path,
                            cmd,
                            revision=configuration["executionRevisionId"],
                            base_commit=local.base_commit,
                            patch_sha256=digest,
                            out=out,
                            seconds=min(budget, remaining()),
                            max_bytes=max(
                                0,
                                cfg.resources.max_output_bytes
                                - sum(a.size_bytes for a in artifacts),
                            ),
                        )
                    if getattr(executor, "cleanup_failed", False):
                        work.preserve = True
                    checks.append(c)
                    if a:
                        artifacts.append(a)
                    budget = remaining()
                if (
                    inspect_tree(
                        work.path,
                        cfg.resources.max_output_bytes,
                        cfg.resources.max_output_bytes,
                    )
                    != before
                ):
                    raise AgentConfigError("Validation changed the candidate tree")
                if (
                    sum(a.size_bytes for a in artifacts)
                    > cfg.resources.max_output_bytes
                ):
                    raise CeilingExceeded(
                        "output", "Combined artifacts exceed output ceiling"
                    )
                return {
                    "status": validation_status(checks),
                    "patchSha256": digest,
                    "checks": [
                        {
                            "commandId": c.command_id,
                            "status": c.status,
                            "exitCode": c.exit_code,
                            "reason": c.reason,
                            "logExcerpt": next(
                                (
                                    (out / a.path)
                                    .read_bytes()[:1024]
                                    .decode("utf-8", errors="replace")
                                    for a in artifacts
                                    if a.id == c.log_artifact_id
                                ),
                                None,
                            ),
                        }
                        for c in checks
                    ],
                }

            attempts = 1
            stream = runner.run(work, configuration, prompt, local, validate, remaining)
            if stream.code:
                code, reason = stream.code, stream.reason
                patch, artifacts, checks = None, [], []
                status = "timed_out" if stream.timed_out else "failed"
            else:
                report = parse_report(stream.text)
                # Always rerun required configuration against the FINAL tree after editor termination.
                # Intermediate tool results can never validate later edits.
                validate(remaining())
                if validation_status(checks) in ("failed", "unavailable"):
                    code = 1
                if remaining() <= 0:
                    raise CeilingExceeded(
                        "wall-clock", "Task wall-clock budget exhausted"
                    )
    except AgentError as e:
        status, reason, code = failure(e)
        # Do not attribute intermediate evidence to an unknown/failing final tree.
        patch = None
        artifacts = []
        checks = []
    except (OSError, ValueError, TypeError, KeyError):
        status, reason, code = "failed", "Custom execution boundary failed", 4
        patch = None
        artifacts = []
        checks = []
    counts = stream.counts if stream else {}
    usage = RunnerUsage(
        provider=p.provider,
        profile_version=p.version,
        input_tokens=counts.get("input"),
        output_tokens=counts.get("output"),
        cache_read_tokens=counts.get("cache_read"),
        cache_write_tokens=counts.get("cache_write"),
        reasoning_tokens=counts.get("reasoning"),
        reported_total_tokens=counts.get("total")
        if stream and not stream.code
        else None,
        attempts=attempts,
        completed_steps=stream.steps if stream else 0,
        tool_calls=stream.tool_calls if stream else 0,
    )
    if status == "completed" and not usage.complete:
        status, reason, code = "failed", "Runner usage is incomplete", 4
    metadata = TaskResult(
        version=1,
        kind="patch" if named or patch else "report",
        task=configuration["taskType"],
        language=p.language if named else None,
        mode="opencode",
        execution_status=status,
        execution_reason=reason,
        validation_status=validation_status(checks),
        base_commit=local.base_commit,
        patch=patch,
        report=report,
        artifacts=artifacts,
        validations=checks,
        runner_usage=usage,
    )
    return result_envelope(configuration, metadata, usage, code)
