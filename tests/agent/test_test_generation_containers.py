"""Real networkless validation with synthetic edits; no model calls."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from agent.config import AgentConfig
from agent.other_opencode import run_opencode
from agent.security_stream import SecurityStream
from agent.test_validation import TestValidationExecutor
from app.task_contracts import (
    TaskConfiguration,
    TaskResult,
    validate_result_configuration,
)
from tests.agent.test_other_workspace import git
from tests.agent.test_test_generation import configuration

pytestmark = pytest.mark.skipif(
    os.environ.get("B11_CONTAINER_TESTS") != "1", reason="Local B11 containers opt-in"
)
FIXTURES = Path(__file__).parent / "fixtures" / "test_generation"


class LocalExecutor(TestValidationExecutor):
    def argv(self, name, workspace, command):
        args = super().argv(name, workspace, command)
        # Local unpublished fixture image: same production argv, only its image ID substituted.
        tag = (
            "driftplain-b11-pytest"
            if self.language == "python"
            else "driftplain-b11-node"
        )
        image = json.loads(
            subprocess.check_output(["docker", "image", "inspect", tag])
        )[0]["Id"]
        args[args.index(command.environment_image)] = image
        return args


def make_repo(tmp_path, language):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tests").mkdir()
    lock = "requirements.lock" if language == "python" else "package-lock.json"
    raw = (FIXTURES / lock).read_bytes()
    (root / lock).write_bytes(raw)
    if language == "python":
        (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (root / "tests/test_old.py").write_text(
            "from calc import add\ndef test_old():\n    assert add(1, 2) == 3\n"
        )
    else:
        (root / "calc.cjs").write_text("exports.add = (a,b) => a+b;\n")
        (root / "tests/old.test.cjs").write_text(
            "const test=require('node:test');const assert=require('node:assert/strict');const {add}=require('../calc.cjs');test('old',()=>assert.equal(add(1,2),3));\n"
        )
    git(root, "init", "-q")
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
    c = configuration(language)
    c["taskConfiguration"]["testEnvironment"]["dependencySha256"] = hashlib.sha256(
        raw
    ).hexdigest()
    return root, c


@pytest.mark.parametrize("language", ["python", "node"])
@pytest.mark.parametrize(
    "case",
    [
        "pass",
        "failure",
        "malformed",
        "zero",
        "skip",
        "missing",
        "production",
        "weaken",
        "config",
        "timeout",
        "final_edit",
    ],
)
def test_named_runtime_real_validation(tmp_path, language, case):
    root, c = make_repo(tmp_path, language)
    original = {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    cfg = TaskConfiguration.model_validate(c["taskConfiguration"])
    if case == "timeout":
        c["taskConfiguration"]["validationCommands"][0]["maxSeconds"] = 2

    class Runner:
        def run(self, work, c, prompt, local, validate, remaining):
            assert "Add regression tests" in prompt
            name = "tests/test_new.py" if language == "python" else "tests/new.test.cjs"
            text = (
                "from calc import add\ndef test_new():\n    assert add(-1, 1) == 0\n"
                if language == "python"
                else "const test=require('node:test');const assert=require('node:assert/strict');const {add}=require('../calc.cjs');test('new',()=>assert.equal(add(-1,1),0));\n"
            )
            if case == "failure":
                text = text.replace("== 0", "== 42").replace("1),0)", "1),42)")
            if case == "malformed":
                text = "invalid syntax { ]"
            if case == "zero":
                text = "x = 1\n"
            if case == "skip":
                text = (
                    "import pytest\n@pytest.mark.skip\ndef test_new():\n    assert False\n"
                    if language == "python"
                    else "require('node:test').skip('skip',()=>{});\n"
                )
            if case == "missing":
                text = (
                    "import missing_b11_dependency\ndef test_new(): pass\n"
                    if language == "python"
                    else "require('missing_b11_dependency');\n"
                )
            if case == "timeout":
                text = (
                    "import time\ndef test_new():\n    time.sleep(30)\n"
                    if language == "python"
                    else "require('node:test')('timeout',async()=>new Promise(r=>setTimeout(r,30000)));\n"
                )
            (work.path / name).write_text(text)
            if case == "production":
                (
                    work.path / ("calc.py" if language == "python" else "calc.cjs")
                ).write_text("changed")
            if case == "weaken":
                (
                    work.path
                    / (
                        "tests/test_old.py"
                        if language == "python"
                        else "tests/old.test.cjs"
                    )
                ).write_text("")
            if case == "config":
                (work.path / "tests/conftest.py").write_text("")
            if case == "final_edit":
                assert validate(remaining())["status"] == "passed"
                (work.path / name).write_text(
                    text.replace("== 0", "== 42").replace("1),0)", "1),42)")
                )
            return SecurityStream(
                text='{"summary":"Added regression tests"}',
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

    output = tmp_path / "out"
    result, code = run_opencode(
        c,
        AgentConfig(
            workspace=str(root),
            base_commit=git(root, "rev-parse", "HEAD"),
            output_dir=str(output),
        ),
        runner=Runner(),
        executor=LocalExecutor(cfg, language),
    )
    assert (code == 0) == (case == "pass"), (
        json.dumps(result)
        + "\n"
        + "\n".join(p.read_text() for p in output.glob("*.log"))
    )
    r = TaskResult.model_validate(result["taskResult"])
    validate_result_configuration(r, c, 9, result["gate"])
    assert r.task == "test_generation" and r.kind == "patch"
    if case == "pass":
        check = r.validations[0]
        assert check.generated_tests_discovered == check.generated_tests_executed == 1
        assert check.test_evidence.existing_tests_executed == 1
        assert (output / "changes.patch").exists()
    if case in ("failure", "final_edit"):
        assert r.validation_status == "failed"
    assert original == {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    names = subprocess.check_output(
        ["docker", "ps", "-a", "--format", "{{.Names}}"]
    ).decode()
    assert "driftplain-validation-" not in names


def test_named_real_editor_bridge_and_final_revalidation(tmp_path, monkeypatch):
    from agent.other_opencode import DockerEditor
    from agent.security_stream import run_stream as real_stream
    from tests.agent.test_security_execution import events

    root, c = make_repo(tmp_path, "python")
    cfg = TaskConfiguration.model_validate(c["taskConfiguration"])
    editor_image = json.loads(
        subprocess.check_output(
            [
                "docker",
                "image",
                "inspect",
                "ghcr.io/steve-droid/driftplain-agent-security:1.5.0",
            ]
        )
    )[0]["RepoDigests"][0]

    def stream(argv, cwd, **kw):
        script = Path(cwd) / "control" / "fixture.py"
        text = "from calc import add\ndef test_new():\n    assert add(-1, 1) == 0\n"
        script.write_text(
            "import os,json\nfrom pathlib import Path\nfrom agent.validation_bridge import request_validation\n"
            "assert not os.environ.get('MODELMATCH_CI_TOKEN')\n"
            "assert not Path('/var/run/docker.sock').exists()\n"
            "c=json.loads(Path('/control/job.json').read_text());assert c['taskType']=='test_generation'\n"
            f"p=Path('/workspace/tests/test_new.py');p.write_text({text!r})\n"
            "r=request_validation('/bridge');assert r['status']=='passed',r\n"
            f"p.write_text({text.replace('== 0', '== 99')!r})\n"
            + "\n".join(
                "print(" + repr(json.dumps(e)) + ",flush=True)"
                for e in events('{"summary":"Final tests require review"}')
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
        return real_stream(argv, cwd, **kw)

    monkeypatch.setattr("agent.other_opencode.run_stream", stream)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-editor-key")
    monkeypatch.setenv("MODELMATCH_CI_TOKEN", "synthetic-launcher-token")
    r, code = run_opencode(
        c,
        AgentConfig(
            workspace=str(root),
            base_commit=git(root, "rev-parse", "HEAD"),
            output_dir=str(tmp_path / "out"),
        ),
        runner=DockerEditor(editor_image),
        executor=LocalExecutor(cfg, "python"),
    )
    assert code == 1 and r["taskResult"]["validationStatus"] == "failed", r
    assert (
        r["taskResult"]["validations"][0]["patchSha256"]
        == r["taskResult"]["patch"]["sha256"]
    )
    assert not (root / "tests/test_new.py").exists()
    names = subprocess.check_output(
        ["docker", "ps", "-a", "--format", "{{.Names}}"]
    ).decode()
    assert "driftplain-editor-" not in names and "driftplain-validation-" not in names
