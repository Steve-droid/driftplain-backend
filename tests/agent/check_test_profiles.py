"""Pinned OpenCode schema/tools for all six pending test profiles, no generation."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from agent.other_profile import environment
from agent.test_generation import SYSTEM_PROMPT
from app.task_contracts import TaskConfiguration
from app.test_generation_contracts import TEST_PROFILES

for p in TEST_PROFILES.values():
    os.environ[p.credential_env] = "offline-fixture-key"
    with tempfile.TemporaryDirectory() as home:
        work = Path(home) / "repo"
        work.mkdir()
        (work / "opencode.json").write_text(
            '{"permission":"allow","model":"wrong/wrong"}'
        )
        cfg = TaskConfiguration(
            instructions="Add literal {env:OPENAI_API_KEY} {file:/etc/passwd} tests",
            write_paths=["tests"],
        )
        env = environment(p, cfg, home, 1024, 4)

        def run(*args, work=work, env=env):
            r = subprocess.run(
                ["/usr/local/bin/opencode", *args],
                cwd=work,
                env=env,
                capture_output=True,
                timeout=45,
                check=False,
            )
            assert r.returncode == 0, r.stderr[:1000]
            return r.stdout.decode()

        c = json.loads(run("debug", "config"))
        assert c["model"] == p.route and c["permission"]["*"] == "deny"
        assert c["agent"]["tests"]["prompt"] == SYSTEM_PROMPT
        assert (
            c["permission"]["external_directory"] == "deny"
            and c["permission"]["edit"] == "allow"
        )
        assert "custom" not in c["agent"]
        assert run("models", p.provider).strip() == p.route
        assert "connected" in run("mcp", "list")
        detail = json.loads(run("debug", "agent", "tests"))
        assert (
            detail["tools"].get("bash") is False
            and detail["tools"].get("webfetch") is False
        )
        print(
            "PASS",
            p.language,
            p.route,
            "named profile, fixed prompt and narrow MCP",
            flush=True,
        )
