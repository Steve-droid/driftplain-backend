"""Actual read-only analysis namespace, synthetic events, no model traffic."""

import json
import os
import subprocess
from pathlib import Path
import pytest
from tests.agent.test_diagnosis import (
    diagnosis_repo,
    repository,
    configuration,
    local_config,
    claimed,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("B12_CONTAINER_TESTS") != "1", reason="Local diagnosis Docker opt-in"
)


def test_readonly_editor_actual_mount_and_selected_inputs(
    diagnosis_repo, tmp_path, monkeypatch
):
    from agent.diagnosis import run_diagnosis
    from agent.other_opencode import DockerEditor
    from agent.security_stream import run_stream
    from tests.agent.test_security_execution import events

    image = json.loads(
        subprocess.check_output(
            [
                "docker",
                "image",
                "inspect",
                "ghcr.io/steve-droid/driftplain-agent-security:1.6.0",
            ]
        )
    )[0]["RepoDigests"][0]

    def fixture_stream(argv, cwd, **kwargs):
        script = Path(cwd) / "control/fixture.py"
        report = json.dumps(
            {
                "summary": "Likely source issue",
                "cause": "repository",
                "uncertainty": "Limited evidence",
                "nextSteps": ["Check source"],
                "evidenceArtifactIds": ["failure-log"],
                "noPatchReason": "Read-only analysis",
            }
        )
        script.write_text(
            """import os
from pathlib import Path
assert not os.environ.get('MODELMATCH_CI_TOKEN')
assert not Path('/var/run/docker.sock').exists()
assert not Path('/workspace/tests/test_app.py').exists()
assert Path('/workspace/src/app.py').exists()
for target in ['/workspace/src/app.py','/workspace/extra','/bridge/request.json']:
    try: Path(target).write_text('forbidden')
    except OSError: pass
    else: raise AssertionError('writable analysis boundary')
assert os.getuid()!=0
s=Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\\t1' in s
"""
            + "\n".join(
                "print(" + repr(json.dumps(e)) + ",flush=True)" for e in events(report)
            )
        )
        script.chmod(0o444)
        at = argv.index("--entrypoint")
        argv[at:at] = ["--network=none"]
        argv[-2:] = ["/control/fixture.py"]
        return run_stream(argv, cwd, **kwargs)

    monkeypatch.setattr("agent.other_opencode.run_stream", fixture_stream)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setenv("MODELMATCH_CI_TOKEN", "launcher-token")
    r, code = run_diagnosis(
        configuration(inputs={"diff": False, "files": ["src/app.py"]}),
        local_config(tmp_path, diagnosis_repo),
        claim=claimed,
        runner=DockerEditor(image),
    )
    assert code == 1 and r["taskResult"]["executionStatus"] == "completed", r
    assert (diagnosis_repo[0] / "src/app.py").read_text() == "value = 1\n"
    assert (tmp_path / "out/failure.log").exists()
