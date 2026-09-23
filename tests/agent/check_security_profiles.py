"""Network-disabled pinned-image check: config/schema/models/tools only; no generation.

Run inside the pinned security container with current app/agent code mounted read-only.
This is fixture readiness, never a live model verification or activation path.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from agent.security_profile import isolated_environment
from app.security_contracts import SECURITY_PROFILES
from app.task_contracts import TaskConfiguration

for p in SECURITY_PROFILES.values():
    os.environ[p.credential_env] = "offline-fixture-key"
    with tempfile.TemporaryDirectory() as home:
        checkout = Path(home) / "checkout"
        checkout.mkdir()
        (checkout / "opencode.json").write_text(
            '{"permission":"allow","model":"wrong/wrong"}'
        )
        (checkout / ".opencode").mkdir()
        (checkout / ".opencode/opencode.json").write_text("invalid repository config")
        cfg = TaskConfiguration(inputs={"diff": False})
        env = isolated_environment(p, cfg, home, 1024, 4)

        def run(*args):
            completed = subprocess.run(
                ["/usr/local/bin/opencode", *args],
                env=env,
                cwd=checkout,
                capture_output=True,
                timeout=40,
            )
            assert completed.returncode == 0, completed.stderr[:1000]
            return completed.stdout.decode()

        resolved = json.loads(run("debug", "config"))
        assert resolved["model"] == p.route
        assert resolved["permission"]["*"] == "deny"
        assert resolved["agent"]["audit"]["steps"] == 4
        assert resolved["provider"][p.provider]["models"][p.model]["id"] == p.model
        models = run("models", p.provider)
        assert models.strip() == p.route, models
        print(f"PASS {p.route}: pinned schema/config/model registration", flush=True)
