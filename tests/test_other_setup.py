"""B14 wrappers preserve exits and artifact capture; never interpolate author text."""

import os
import subprocess

import pytest


@pytest.mark.parametrize("mode", ["single_call", "opencode"])
def test_other_stage_captures_json_and_preserves_failure(tmp_path, mode):
    from app.selections.other_setup import build_other_stage

    stage = build_other_stage("printf '{\"executionStatus\":\"failed\"}'\nexit 7\n", mode)
    assert "stage('Custom task')" in stage
    assert "post {" in stage and "always {" in stage
    assert "archiveArtifacts" in stage
    assert ("result/**" in stage) == (mode == "opencode")
    script = stage.split("sh '''", 1)[1].split("'''", 1)[0]
    result = subprocess.run(
        ["sh"], input=script, text=True,
        env={**os.environ, "DRIFTPLAIN_SCRATCH": str(tmp_path)},
        capture_output=True,
    )
    assert result.returncode == 7
    assert (tmp_path / "result.json").read_text() == '{"executionStatus":"failed"}'


def test_single_call_command_enforces_saved_container_limits():
    from app.selections.other_setup import build_other_command
    from tests.agent.test_other_execution import configuration

    c = configuration()
    c["taskConfiguration"]["resources"].update(
        cpuMillis=500, memoryMib=256, maxProcesses=12,
    )
    command = build_other_command(c, "launcher@sha256:" + "a" * 64, "")
    assert "--cpus 0.5 --memory 256m --pids-limit 12" in command
    assert "docker.sock" not in command
    subprocess.run(["sh", "-n"], input=command, text=True, check=True)
