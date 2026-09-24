"""Local offline Docker checks. Opt in explicitly; never run model generation."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent.validation_executor import DockerValidationExecutor
from app.task_contracts import ResourceLimits, ValidationCommand

pytestmark = pytest.mark.skipif(
    os.environ.get("B10_CONTAINER_TESTS") != "1",
    reason="Local isolated Docker verification opt-in",
)


@pytest.fixture
def image():
    return json.loads(
        subprocess.check_output(
            ["docker", "image", "inspect", "ghcr.io/steve-droid/driftplain-agent:1.4.0"]
        )
    )[0]["RepoDigests"][0]


def run(tmp_path, image, script, seconds=10, max_bytes=8192):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.chmod(0o755)
    (workspace / "input").write_text("scoped")
    (workspace / "input").chmod(0o444)
    (workspace / "check.py").write_text(script)
    (workspace / "check.py").chmod(0o444)
    ex = DockerValidationExecutor(
        ResourceLimits(
            max_output_bytes=max_bytes,
            memory_mib=128,
            cpu_millis=1000,
            max_processes=16,
        )
    )
    cmd = ValidationCommand(
        id="check",
        argv=["python", "/workspace/check.py"],
        environment_image=image,
        max_seconds=seconds,
    )
    return ex.run(
        workspace,
        cmd,
        revision=9,
        base_commit="a" * 40,
        patch_sha256=None,
        out=tmp_path,
        seconds=seconds,
    )


def test_actual_namespace_environment_network_and_readonly(
    tmp_path, image, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-validation")
    monkeypatch.setenv("MODELMATCH_CI_TOKEN", "must-not-reach-validation")
    sentinel = tmp_path / "host-only"
    sentinel.write_text("host authority")
    script = """import os,socket,pathlib
assert not any('API_KEY' in k or 'CI_TOKEN' in k or k.startswith('AWS_') for k in os.environ)
assert not pathlib.Path('/var/run/docker.sock').exists()
try:assert not pathlib.Path('/root/.aws').exists()
except PermissionError:pass
assert not pathlib.Path(HOST_PATH).exists()
assert pathlib.Path('/workspace/input').read_text()=='scoped'
assert os.getuid()==10001
status=pathlib.Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\t1' in status
assert int(pathlib.Path('/sys/fs/cgroup/pids.max').read_text())==16
assert int(pathlib.Path('/sys/fs/cgroup/memory.max').read_text())==128*1024**2
try:pathlib.Path('/workspace/attack').write_text('x')
except OSError:pass
else:raise AssertionError('writable mount')
s=socket.socket();s.settimeout(.2)
try:s.connect(('1.1.1.1',443))
except OSError:pass
else:raise AssertionError('network reachable')
print('isolated')
""".replace("HOST_PATH", repr(str(sentinel)))
    c, a = run(tmp_path, image, script)
    assert c.status == "passed", (c, (tmp_path / a.path).read_text() if a else None)
    assert (tmp_path / a.path).read_text().strip().endswith("isolated")


@pytest.mark.parametrize(
    "script,seconds,max_bytes",
    [
        ("import time;time.sleep(20)", 1, 8192),
        ('import os\nwhile True:os.write(1,b"x"*16384)', 10, 1024),
        ("import os,time\nif os.fork()==0:time.sleep(20)\nelse:os._exit(0)", 1, 8192),
    ],
)
def test_limits_remove_entire_container(tmp_path, image, script, seconds, max_bytes):
    c, a = run(tmp_path, image, script, seconds, max_bytes)
    if "os.fork" in script:
        assert (
            c.status == "passed"
        )  # PID 1 exit tears down descendants at the namespace boundary.
    else:
        assert c.status == "unavailable" and c.exit_code is None
    names = subprocess.check_output(
        ["docker", "ps", "-a", "--format", "{{.Names}}"]
    ).decode()
    assert "driftplain-validation-" not in names


def test_real_editor_mailbox_pause_validation_and_final_patch(
    tmp_path, image, monkeypatch
):
    from agent.config import AgentConfig
    from agent.other_opencode import DockerEditor, run_opencode
    from agent.security_stream import run_stream as real_stream
    from tests.agent.test_other_execution import configuration
    from tests.agent.test_other_workspace import git
    from tests.agent.test_security_execution import events

    root = tmp_path / "original"
    root.mkdir()
    git(root, "init", "-q")
    (root / "src").mkdir()
    (root / "src" / "value").write_text("base")
    (root / "check.py").write_text(
        "from pathlib import Path\nassert Path('src/value').read_text() in ('first','final')\nprint('checked')\n"
    )
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=f@example.test",
        "commit",
        "-qm",
        "base",
    )
    c = configuration(
        mode="opencode",
        inputs={"diff": False},
        write_paths=["src"],
        validation_commands=[
            {"id": "check", "argv": ["python", "check.py"], "environmentImage": image}
        ],
    )
    editor_image = json.loads(
        subprocess.check_output(
            [
                "docker",
                "image",
                "inspect",
                "ghcr.io/steve-droid/driftplain-agent-security:1.4.0",
            ]
        )
    )[0]["RepoDigests"][0]
    observed = []

    def fixture_stream(argv, cwd, **kwargs):
        # Test-only synthetic editor. Production has no script/endpoint/activation override.
        script = Path(cwd) / "control" / "fixture.py"
        emitted = events('{"summary":"Updated after validation"}')
        script.write_text(
            "import os,json\nfrom pathlib import Path\nfrom agent.validation_bridge import request_validation\n"
            "assert not os.environ.get('MODELMATCH_CI_TOKEN')\n"
            "p=Path('/workspace/src/value');p.write_text('first')\n"
            "r=request_validation('/bridge');assert r['status']=='passed',r\n"
            "Path('/workspace/src/intermediate-digest').write_text(r['patchSha256'])\n"
            + "p.write_text('final')\n"
            + "\n".join(
                "print(" + repr(json.dumps(e)) + ",flush=True)" for e in emitted
            )
        )
        script.chmod(0o444)
        at = argv.index("--entrypoint")
        argv[at:at] = [
            "--network=none",
            "--mount",
            f"type=bind,src={Path(__file__).resolve().parents[2] / 'agent'},dst=/app/agent,readonly",
            "--mount",
            f"type=bind,src={Path(__file__).resolve().parents[2] / 'app'},dst=/app/app,readonly",
        ]
        argv[-2:] = ["/control/fixture.py"]
        original_tick = kwargs["tick"]

        def tick():
            original_tick()
            reply = Path(cwd) / "bridge" / "response.json"
            if reply.exists():
                observed.append(json.loads(reply.read_text())["patchSha256"])

        kwargs["tick"] = tick
        return real_stream(argv, cwd, **kwargs)

    monkeypatch.setattr("agent.other_opencode.run_stream", fixture_stream)
    monkeypatch.setenv("OPENAI_API_KEY", "editor-only-fixture-key")
    monkeypatch.setenv("MODELMATCH_CI_TOKEN", "launcher-only-fixture-token")
    result, code = run_opencode(
        c,
        AgentConfig(
            workspace=str(root),
            base_commit=git(root, "rev-parse", "HEAD"),
            output_dir=str(tmp_path / "out"),
        ),
        runner=DockerEditor(editor_image),
    )
    assert code == 0, json.dumps(result)
    assert (
        result["taskResult"]["patch"]["sha256"]
        not in (tmp_path / "out" / "changes.patch").read_text()
    )
    assert "intermediate-digest" in (tmp_path / "out" / "changes.patch").read_text()
    assert result["taskResult"]["validationStatus"] == "passed"
    assert "final" in (tmp_path / "out" / "changes.patch").read_text()
    assert (root / "src" / "value").read_text() == "base"
    names = subprocess.check_output(
        ["docker", "ps", "-a", "--format", "{{.Names}}"]
    ).decode()
    assert "driftplain-editor-" not in names and "driftplain-validation-" not in names
