"""Only the trusted CI launcher owns Docker. The executor has no network or credentials."""

import hashlib
import uuid
from pathlib import Path

from agent.bounded_process import run_bounded
from app.task_contracts import Artifact, ValidationCheck


class DockerValidationExecutor:
    def __init__(self, resources):
        self.resources = resources
        self.cleanup_failed = False

    def argv(self, name, workspace, command):
        r = self.resources
        return [
            "docker",
            "run",
            "--name",
            name,
            "--pull=never",
            "--network=none",
            "--read-only",
            "--user=10001:10001",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={r.max_processes}",
            f"--memory={r.memory_mib}m",
            f"--memory-swap={r.memory_mib}m",
            f"--cpus={r.cpu_millis / 1000}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=64m",
            "--mount",
            f"type=bind,src={Path(workspace).resolve()},dst=/workspace,readonly",
            "--workdir",
            "/workspace",
            "--entrypoint",
            "/usr/bin/env",
            command.environment_image,
            "-i",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "HOME=/tmp",
            "TMPDIR=/tmp",
            "PYTHONDONTWRITEBYTECODE=1",
            *command.argv,
        ]

    def run(
        self,
        workspace,
        command,
        *,
        revision,
        base_commit,
        patch_sha256,
        out,
        seconds,
        max_bytes=None,
    ):
        name = "driftplain-validation-" + uuid.uuid4().hex
        reason = None
        result = None
        try:
            result = run_bounded(
                self.argv(name, workspace, command),
                seconds=min(seconds, command.max_seconds),
                max_bytes=min(self.resources.max_output_bytes, max_bytes)
                if max_bytes is not None
                else self.resources.max_output_bytes,
            )
            reason = result.reason
            if result.code in (125, 126, 127) and not reason:
                reason = "Validation image, dependency or executable unavailable"
        except OSError:
            reason = "Validation executor unavailable"
        finally:
            # Killing a docker CLI does not kill a container. Always remove the job container.
            try:
                cleanup = run_bounded(
                    ["docker", "rm", "-f", name], seconds=10, max_bytes=8192
                )
                self.cleanup_failed = bool(cleanup.code or cleanup.reason)
            except OSError:
                self.cleanup_failed = True
            if self.cleanup_failed:
                reason = "Validation cleanup could not be confirmed"
        common = dict(
            command_id=command.id,
            execution_revision_id=revision,
            base_commit=base_commit,
            patch_sha256=patch_sha256,
        )
        if result is None:
            return ValidationCheck(**common, status="unavailable", reason=reason), None
        raw = result.output
        if getattr(self, "redactor", None):
            raw = self.redactor(raw.decode("utf-8", errors="replace")).encode()
            limit = min(self.resources.max_output_bytes, max_bytes) if max_bytes is not None else self.resources.max_output_bytes
            raw = raw[:limit]
        path = Path(out) / f"validation-{command.id}.log"
        path.write_bytes(raw)
        artifact = Artifact(
            id="validation-" + command.id,
            kind="validation_log",
            path=path.name,
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
        )
        if reason:
            return ValidationCheck(
                **common,
                status="unavailable",
                reason=reason,
                log_artifact_id=artifact.id,
            ), artifact
        return ValidationCheck(
            **common,
            status="passed" if result.code == 0 else "failed",
            exit_code=result.code,
            duration_ms=result.duration_ms,
            log_artifact_id=artifact.id,
        ), artifact
