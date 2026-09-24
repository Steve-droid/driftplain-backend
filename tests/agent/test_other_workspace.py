import subprocess

import pytest

from agent.errors import AgentConfigError, CeilingExceeded
from agent.other_workspace import DisposableWorkspace
from agent.validation_executor import DockerValidationExecutor
from app.task_contracts import TaskConfiguration, ValidationCommand


def git(path, *args):
    return (
        subprocess.check_output(
            ["git", "-C", str(path), *args], stderr=subprocess.DEVNULL
        )
        .decode()
        .strip()
    )


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    git(root, "init", "-q")
    (root / "src").mkdir()
    (root / "src" / "old.txt").write_text("old\n")
    (root / "outside.txt").write_text("protected\n")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-qm",
        "base",
    )
    return root, git(root, "rev-parse", "HEAD")


def test_exact_base_patch_add_delete_preserves_original_and_artifact(
    repository, tmp_path
):
    root, commit = repository
    (root / "outside.txt").write_text("dirty original\n")
    cfg = TaskConfiguration(write_paths=["src"])
    out = tmp_path / "out"
    out.mkdir()
    with DisposableWorkspace(root, commit, cfg) as work:
        assert (work.path / "outside.txt").read_text() == "protected\n"
        (work.path / "src" / "old.txt").unlink()
        (work.path / "src" / "new.txt").write_text("new\n")
        patch, artifact = work.capture(out)
        assert {f.operation for f in patch.files} == {"added", "deleted"}
        raw = (out / artifact.path).read_text()
        assert "a/src/old.txt" in raw and "b/src/new.txt" in raw
        saved = work.path
    assert not saved.exists()
    assert (out / artifact.path).exists()
    assert (root / "src" / "old.txt").read_text() == "old\n"
    assert (root / "outside.txt").read_text() == "dirty original\n"


def test_forbidden_edits_symlinks_and_output_bound(repository, tmp_path):
    root, commit = repository
    with DisposableWorkspace(
        root, commit, TaskConfiguration(write_paths=["src"])
    ) as work:
        (work.path / "outside.txt").write_text("changed")
        with pytest.raises(AgentConfigError, match="permitted"):
            work.capture(tmp_path)
        (work.path / "outside.txt").write_text("protected\n")
        (work.path / "src" / "link").symlink_to("/etc/passwd")
        with pytest.raises(AgentConfigError):
            work.capture(tmp_path)
        (work.path / "src" / "link").unlink()
        (work.path / "src" / "big").write_bytes(b"x" * 1048577)
        with pytest.raises(CeilingExceeded):
            work.capture(tmp_path)


def test_exact_base_rejects_refs(repository):
    root, commit = repository
    with pytest.raises(AgentConfigError):
        with DisposableWorkspace(root, "HEAD", TaskConfiguration(write_paths=["src"])):
            pass


def test_validation_launcher_does_not_inherit_authority(repository, tmp_path):
    cmd = ValidationCommand(
        id="tests",
        argv=["python", "-c", "print(1)"],
        environment_image="python@sha256:" + "a" * 64,
    )
    ex = DockerValidationExecutor(TaskConfiguration().resources)
    argv = ex.argv("fixture", tmp_path, cmd)
    assert "--network=none" in argv and "--read-only" in argv
    assert "--cap-drop=ALL" in argv and "--security-opt=no-new-privileges" in argv
    assert "-i" in argv and "HOME=/tmp" in argv
    assert "--pull=never" in argv
    assert not any(
        "docker.sock" in v or "API_KEY" in v or "CI_TOKEN" in v for v in argv
    )


def test_final_patch_is_revalidated_and_artifacts_survive(repository, tmp_path):
    import hashlib

    from agent.config import AgentConfig
    from agent.other_opencode import run_opencode
    from agent.security_stream import SecurityStream
    from app.task_contracts import (
        Artifact,
        TaskResult,
        ValidationCheck,
        validate_result_configuration,
    )
    from tests.agent.test_other_execution import configuration

    root, commit = repository
    c = configuration(
        mode="opencode",
        inputs={"diff": False},
        write_paths=["src"],
        validation_commands=[
            {
                "id": "tests",
                "argv": ["python", "test.py"],
                "environmentImage": "python@sha256:" + "a" * 64,
            }
        ],
    )
    seen = []

    class Executor:
        def run(self, workspace, cmd, **kw):
            seen.append((workspace / "src" / "old.txt").read_text())
            raw = b"passed"
            (kw["out"] / "log.txt").write_bytes(raw)
            artifact = Artifact(
                id="log",
                kind="validation_log",
                path="log.txt",
                sha256=hashlib.sha256(raw).hexdigest(),
                size_bytes=len(raw),
            )
            return ValidationCheck(
                command_id=cmd.id,
                status="passed",
                execution_revision_id=kw["revision"],
                base_commit=commit,
                patch_sha256=kw["patch_sha256"],
                exit_code=0,
                duration_ms=1,
                log_artifact_id="log",
            ), artifact

    class Runner:
        def run(self, work, c, prompt, local, validate, remaining):
            (work.path / "src" / "old.txt").write_text("first")
            assert validate(remaining())["status"] == "passed"
            (work.path / "src" / "old.txt").write_text("final")
            return SecurityStream(
                text='{"summary":"Updated"}',
                steps=1,
                counts={
                    "input": 10,
                    "output": 10,
                    "reasoning": 0,
                    "cache_read": 0,
                    "cache_write": 0,
                    "total": 20,
                },
            )

    result, code = run_opencode(
        c,
        AgentConfig(
            workspace=str(root), base_commit=commit, output_dir=str(tmp_path / "result")
        ),
        runner=Runner(),
        executor=Executor(),
    )
    assert code == 0
    assert seen == ["first", "final"]
    assert result["taskResult"]["kind"] == "patch"
    assert (
        result["taskResult"]["validations"][0]["patchSha256"]
        == result["taskResult"]["patch"]["sha256"]
    )
    validate_result_configuration(
        TaskResult.model_validate(result["taskResult"]), c, 9, "pass"
    )
    assert (tmp_path / "result" / "changes.patch").exists()
    assert (root / "src" / "old.txt").read_text() == "old\n"


@pytest.mark.parametrize("status", ["failed", "unavailable"])
def test_failed_missing_validation_never_passes(repository, tmp_path, status):
    from agent.config import AgentConfig
    from agent.other_opencode import run_opencode
    from agent.security_stream import SecurityStream
    from app.task_contracts import Artifact, ValidationCheck
    from tests.agent.test_other_execution import configuration

    root, commit = repository
    c = configuration(
        mode="opencode",
        inputs={"diff": False},
        write_paths=["src"],
        validation_commands=[
            {
                "id": "tests",
                "argv": ["missing"],
                "environmentImage": "python@sha256:" + "a" * 64,
            }
        ],
    )

    class Executor:
        def run(self, workspace, cmd, **kw):
            common = dict(
                command_id=cmd.id,
                execution_revision_id=kw["revision"],
                base_commit=commit,
                patch_sha256=kw["patch_sha256"],
            )
            if status == "unavailable":
                return ValidationCheck(
                    **common, status=status, reason="Missing dependencies"
                ), None
            (kw["out"] / "log").write_bytes(b"")
            artifact = Artifact(
                id="log",
                kind="validation_log",
                path="log",
                sha256="a" * 64,
                size_bytes=0,
            )
            return ValidationCheck(
                **common,
                status=status,
                exit_code=1,
                duration_ms=1,
                log_artifact_id="log",
            ), artifact

    class Runner:
        def run(self, *args):
            return SecurityStream(
                text='{"summary":"Done"}',
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

    result, code = run_opencode(
        c,
        AgentConfig(
            workspace=str(root), base_commit=commit, output_dir=str(tmp_path / "out")
        ),
        runner=Runner(),
        executor=Executor(),
    )
    assert code == 1 and result["gate"] == "fail"
    assert result["taskResult"]["validationStatus"] == status


@pytest.mark.parametrize(
    "case", ["malformed", "refused", "timeout", "incomplete", "forbidden"]
)
def test_custom_opencode_failure_cases_preserve_original(repository, tmp_path, case):
    from agent.config import AgentConfig
    from agent.other_opencode import run_opencode
    from agent.security_stream import SecurityStream
    from tests.agent.test_other_execution import configuration

    root, commit = repository
    c = configuration(mode="opencode", inputs={"diff": False}, write_paths=["src"])

    class Runner:
        def run(self, work, *args):
            counts = {
                "input": 1,
                "output": 1,
                "reasoning": 0,
                "cache_read": 0,
                "cache_write": 0,
                "total": 2,
            }
            if case == "forbidden":
                (work.path / "outside.txt").write_text("bad")
            if case == "incomplete":
                counts["total"] = None
            return SecurityStream(
                text="not json"
                if case == "malformed"
                else "I cannot assist with this request."
                if case == "refused"
                else '{"summary":"Done"}',
                steps=1,
                counts=counts,
                code=124 if case == "timeout" else 0,
                reason="deadline" if case == "timeout" else None,
                timed_out=case == "timeout",
            )

    result, code = run_opencode(
        c,
        AgentConfig(
            workspace=str(root), base_commit=commit, output_dir=str(tmp_path / "out")
        ),
        runner=Runner(),
    )
    assert code != 0 and result["gate"] == "fail"
    assert result["taskResult"]["executionStatus"] != "completed"
    assert (root / "outside.txt").read_text() == "protected\n"


def test_repository_attributes_cannot_normalize_away_edits(repository, tmp_path):
    root, commit = repository
    (root / ".gitattributes").write_text("*.txt text eol=lf\n")
    git(root, "add", ".gitattributes")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=f@example.test",
        "commit",
        "-qm",
        "attributes",
    )
    with DisposableWorkspace(
        root, git(root, "rev-parse", "HEAD"), TaskConfiguration(write_paths=["src"])
    ) as work:
        (work.path / "src" / "old.txt").write_bytes(b"old\r\n")
        patch, artifact = work.capture(tmp_path)
        assert patch is not None and artifact.size_bytes > 0
        assert b"+old\r\n" in (tmp_path / artifact.path).read_bytes()
