"""Execute inside the pinned image, network disabled. No generation or activation."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from agent.other_profile import environment
from app.other_contracts import OTHER_PROFILES
from app.task_contracts import TaskConfiguration

for (mode, _), p in OTHER_PROFILES.items():
    if mode != "opencode":
        continue
    os.environ[p.credential_env] = "offline-fixture-key"
    with tempfile.TemporaryDirectory() as home:
        work = Path(home) / "repo"
        work.mkdir()
        (work / "opencode.json").write_text(
            '{"permission":"allow","model":"wrong/wrong"}'
        )
        cfg = TaskConfiguration(
            label="Fixture",
            system_prompt="Custom system {env:OPENAI_API_KEY} {file:/etc/passwd}",
            instructions="Custom task",
            write_paths=["src"],
        )
        env = environment(p, cfg, home, 1024, 4)

        def run(*args):
            r = subprocess.run(
                ["/usr/local/bin/opencode", *args],
                cwd=work,
                env=env,
                capture_output=True,
                timeout=45,
            )
            assert r.returncode == 0, r.stderr[:1000]
            return r.stdout.decode()

        c = json.loads(run("debug", "config"))
        assert c["model"] == p.route and c["permission"]["*"] == "deny"
        assert (
            c["permission"]["edit"] == "allow"
            and c["permission"]["external_directory"] == "deny"
        )
        assert c["agent"]["custom"]["prompt"] == cfg.system_prompt
        assert c["mcp"]["validation"]["command"] == [
            "/venv/bin/python",
            "-m",
            "agent.validation_bridge",
        ]
        assert run("models", p.provider).strip() == p.route
        mcp = run("mcp", "list")
        assert "connected" in mcp, mcp
        detail = json.loads(run("debug", "agent", "custom"))
        assert detail["tools"].get("bash") is False
        assert detail["tools"].get("webfetch") is False
        target = work / "editable.txt"
        target.write_text("old\n")

        def tool(name, params):
            return subprocess.run(
                [
                    "/usr/local/bin/opencode",
                    "debug",
                    "agent",
                    "custom",
                    "--tool",
                    name,
                    "--params",
                    json.dumps(params),
                ],
                cwd=work,
                env=env,
                capture_output=True,
                timeout=45,
            )

        denied = tool("read", {"filePath": "/proc/self/environ"})
        assert denied.returncode != 0, denied.stdout[:300]
        denied = tool(
            "bash", {"command": "touch /tmp/forbidden", "description": "probe"}
        )
        assert denied.returncode != 0
        if detail["tools"].get("apply_patch"):
            changed = tool(
                "apply_patch",
                {
                    "patchText": "*** Begin Patch\n*** Delete File: editable.txt\n*** Add File: added.txt\n+new\n*** End Patch"
                },
            )
            assert changed.returncode == 0, changed.stderr[:1000]
            assert not target.exists() and (work / "added.txt").read_text() == "new\n"
        else:
            changed = tool(
                "write", {"filePath": str(work / "added.txt"), "content": "new\n"}
            )
            assert changed.returncode == 0, changed.stderr[:1000]
            assert (work / "added.txt").read_text() == "new\n"
        print("PASS", p.route, "custom profile and narrow MCP connected", flush=True)
