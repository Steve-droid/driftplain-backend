"""Reviewed configuration for OpenCode 1.18.20; never loads repository configuration."""

import hashlib
import json
import os
import stat
from pathlib import Path

from agent.errors import AgentConfigError, CeilingExceeded
from agent.security import load_system_prompt
from app.security_contracts import OPENCODE_BINARY_SHA256

ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
}
OPTIONS = {
    "openai": {"reasoningEffort": "low", "store": False},
    "anthropic": {"thinking": {"type": "adaptive"}, "effort": "low"},
    "google": {"thinkingConfig": {"thinkingLevel": "low", "includeThoughts": True}},
}


def opencode_configuration(profile, cfg, output_tokens, steps):
    permissions = {
        "*": "deny",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "external_directory": "deny",
    }
    return {
        "model": profile.route,
        "small_model": profile.route,
        "enabled_providers": [profile.provider],
        "share": "disabled",
        "autoupdate": False,
        "snapshot": False,
        "plugin": [],
        "mcp": {},
        "lsp": False,
        "formatter": False,
        "instructions": [],
        "compaction": {"auto": False, "prune": False},
        "permission": permissions,
        "agent": {
            "audit": {
                "mode": "primary",
                "model": profile.route,
                "steps": steps,
                "prompt": load_system_prompt(None),
                "permission": permissions,
            },
            **{
                n: {"disable": True}
                for n in (
                    "build",
                    "plan",
                    "explore",
                    "general",
                    "title",
                    "summary",
                    "compaction",
                )
            },
        },
        "provider": {
            profile.provider: {
                "npm": f"@ai-sdk/{profile.provider}",
                "api": ENDPOINTS[profile.provider],
                "env": [profile.credential_env],
                "whitelist": [profile.model],
                "options": {
                    "baseURL": ENDPOINTS[profile.provider],
                    "timeout": cfg.resources.max_seconds * 1000,
                },
                "models": {
                    profile.model: {
                        "id": profile.model,
                        "name": profile.model,
                        "reasoning": True,
                        "temperature": False,
                        "tool_call": True,
                        "modalities": {"input": ["text"], "output": ["text"]},
                        "limit": {
                            "context": cfg.inputs.max_context_tokens,
                            "output": output_tokens,
                        },
                        "options": OPTIONS[profile.provider],
                    }
                },
            }
        },
    }


def isolated_environment(profile, cfg, home, output_tokens, steps):
    key = os.environ.get(profile.credential_env)
    if not key:
        raise AgentConfigError(f"Missing {profile.credential_env} credential")
    root = Path(home)
    catalog = root / "models.json"
    catalog.write_text("{}")
    # An allowlist, never inherited OPENCODE_*, proxy, SDK endpoints, CI tokens or PATH.
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": home,
        "NO_COLOR": "1",
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_STATE_HOME": str(root / "state"),
        "OPENCODE_MODELS_PATH": str(catalog),
        "OPENCODE_CONFIG_CONTENT": json.dumps(
            opencode_configuration(profile, cfg, output_tokens, steps)
        ),
        profile.credential_env: key,
    }
    if profile.provider == "google":
        env["GOOGLE_GENERATIVE_AI_API_KEY"] = key
    for name in (
        "DISABLE_PROJECT_CONFIG",
        "DISABLE_MODELS_FETCH",
        "DISABLE_AUTOUPDATE",
        "DISABLE_AUTOCOMPACT",
        "DISABLE_PRUNE",
        "DISABLE_DEFAULT_PLUGINS",
        "DISABLE_EXTERNAL_SKILLS",
        "DISABLE_CLAUDE_CODE",
        "DISABLE_LSP_DOWNLOAD",
        "PURE",
    ):
        env[f"OPENCODE_{name}"] = "1"
    env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"] = str(output_tokens)
    return env


def check_launch_boundary(workspace, resources):
    """Fail closed outside the reviewed Linux cgroup-v2 container envelope."""
    try:
        if os.getuid() == 0 or not os.statvfs(workspace).f_flag & os.ST_RDONLY:
            raise ValueError()
        status = dict(
            line.split(":", 1)
            for line in Path("/proc/self/status").read_text().splitlines()
            if ":" in line
        )
        if int(status["CapEff"].strip(), 16) or int(status["NoNewPrivs"].strip()) != 1:
            raise ValueError()
        cgroup = Path("/sys/fs/cgroup")
        if int((cgroup / "memory.max").read_text()) > resources.memory_mib * 1024**2:
            raise ValueError()
        if int((cgroup / "pids.max").read_text()) > resources.max_processes:
            raise ValueError()
        quota, period = (cgroup / "cpu.max").read_text().split()
        if int(quota) * 1000 > resources.cpu_millis * int(period):
            raise ValueError()
        # Pin the binary too; an arbitrary env path never selects this executable.
        binary = Path("/usr/local/bin/opencode")
        with binary.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != OPENCODE_BINARY_SHA256:
            raise ValueError()
    except (OSError, KeyError, ValueError, AttributeError):
        raise AgentConfigError(
            "Security requires the pinned image, read-only workspace and bounded cgroup-v2 container"
        ) from None


def inspect_workspace(workspace, inputs):
    """Bound the whole checkout. No special files/symlinks, no hidden unbounded inputs."""
    root = Path(workspace)
    if root.is_symlink() or not root.is_dir():
        raise AgentConfigError("Security checkout must be a real directory")

    def unreadable(_):
        raise AgentConfigError("Security checkout contains an unreadable directory")

    total = 0
    files = set()
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=unreadable):
        for name in dirs + names:
            path = Path(directory) / name
            info = path.lstat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise AgentConfigError(
                    "Security checkout contains a link or special file"
                )
        for name in names:
            path = Path(directory) / name
            size = path.stat().st_size
            if size > inputs.max_file_bytes:
                raise CeilingExceeded("input", "security file byte ceiling exceeded")
            total += size
            if total > inputs.max_bytes:
                raise CeilingExceeded(
                    "input", "security checkout byte ceiling exceeded"
                )
            files.add(path.relative_to(root).as_posix())
            if len(files) > 1000:
                raise CeilingExceeded(
                    "input", "security checkout file ceiling exceeded"
                )
    if not files:
        raise AgentConfigError("Security checkout has no files to audit")
    return files
