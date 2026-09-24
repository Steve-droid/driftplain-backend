"""Dedicated writable profile for the pinned OpenCode binary; no repository authority."""

import json
import os
from pathlib import Path

from agent.security_profile import isolated_environment, opencode_configuration

ALLOWED_TOOLS = frozenset(
    {
        "read",
        "glob",
        "grep",
        "list",
        "edit",
        "write",
        "apply_patch",
        "validation_validate",
    }
)


def configuration(profile, cfg, output, steps):
    named = hasattr(profile, "language")
    if named:
        from agent.test_generation import SYSTEM_PROMPT

        cfg = cfg.model_copy(update={"system_prompt": SYSTEM_PROMPT})
    result = opencode_configuration(profile, cfg, output, steps)
    permissions = {
        "*": "deny",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "edit": "allow",
        "validation_validate": "allow",
        "external_directory": "deny",
    }
    result["permission"] = permissions
    result["agent"].pop("audit")
    result["agent"]["tests" if named else "custom"] = {
        "mode": "primary",
        "model": profile.route,
        "steps": steps,
        "prompt": cfg.system_prompt,
        "permission": permissions,
    }
    result["mcp"] = {
        "validation": {
            "type": "local",
            "command": ["/venv/bin/python", "-m", "agent.validation_bridge"],
            "enabled": True,
            "timeout": cfg.resources.max_seconds * 1000,
        }
    }
    return result


def environment(profile, cfg, home, output, steps):
    env = isolated_environment(profile, cfg, home, output, steps)
    # Pinned OpenCode substitutes config macros BEFORE JSON parsing. Escape markers
    # in serialized JSON so user prompts stay literal and cannot read environment/files.
    env["OPENCODE_CONFIG_CONTENT"] = (
        json.dumps(configuration(profile, cfg, output, steps))
        .replace("{env:", r"\u007benv:")
        .replace("{file:", r"\u007bfile:")
    )
    # The MCP helper imports only trusted code. Never include checkout paths.
    env.update(PYTHONPATH="/app", PYTHONSAFEPATH="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def worker():
    """Internal image entry point. No CI token or Docker socket exists in this namespace."""
    import hashlib
    import tempfile

    from agent.errors import AgentConfigError
    from app.security_contracts import OPENCODE_BINARY_SHA256
    from app.task_contracts import TaskConfiguration

    config = json.loads(Path("/control/job.json").read_text())
    from agent.other_execution import resolve_other_profile

    negotiated = {
        k: v for k, v in config.items() if k not in {"prompt", "outputTokens", "steps"}
    }
    if negotiated["taskType"] == "test_generation":
        from agent.test_generation import resolve_test_profile

        p = resolve_test_profile(negotiated)
    else:
        p = resolve_other_profile(negotiated)
    cfg = TaskConfiguration.model_validate(config["taskConfiguration"])
    with open("/usr/local/bin/opencode", "rb") as f:
        if hashlib.file_digest(f, "sha256").hexdigest() != OPENCODE_BINARY_SHA256:
            raise AgentConfigError("Pinned OpenCode binary mismatch")
    with tempfile.TemporaryDirectory(dir="/tmp") as home:
        env = environment(p, cfg, home, config["outputTokens"], config["steps"])
        os.chdir("/workspace")
        os.execve(
            "/usr/local/bin/opencode",
            [
                "opencode",
                "run",
                "--format",
                "json",
                "--agent",
                "tests" if hasattr(p, "language") else "custom",
                "--title",
                "Driftplain test generation"
                if hasattr(p, "language")
                else "Driftplain custom task",
                "-m",
                p.route,
                config["prompt"],
            ],
            env,
        )


if __name__ == "__main__":
    worker()
