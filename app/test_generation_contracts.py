"""Exact named-test candidates and pure, external test-only policy."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from app.security_contracts import OPENCODE_VERSION


@dataclass(frozen=True)
class TestProfile:
    language: str
    provider: str
    model: str
    credential_env: str
    verification_status: str = "pending"

    @property
    def route(self):
        return f"{self.provider}/{self.model}"

    @property
    def version(self):
        return f"tests-{self.language}-oc{OPENCODE_VERSION}-v1:{self.model}:low"


TEST_PROFILES = {
    (language, model): TestProfile(language, provider, model, credential)
    for language in ("python", "node")
    for provider, model, credential in (
        ("openai", "gpt-5.6-sol", "OPENAI_API_KEY"),
        ("anthropic", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
        ("google", "gemini-3.7-flash", "GEMINI_API_KEY"),
    )
}


def validate_test_paths(language, files):
    paths = [f.path for f in files]
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("New test paths must be nonempty and unique")
    for f in files:
        p = PurePosixPath(f.path)
        name = p.name
        test_name = (
            (name.startswith("test_") or name.endswith("_test.py"))
            and name.endswith(".py")
            if language == "python"
            else name.endswith((".test.js", ".test.cjs", ".test.mjs"))
        )
        if (
            f.operation != "added"
            or not test_name
            or not any(x in ("test", "tests", "__tests__") for x in p.parts[:-1])
            or any(x.startswith(".") or x == "node_modules" for x in p.parts)
        ):
            raise ValueError(
                "Named test generation permits only new test files in test directories"
            )


def validate_test_environment(cfg, language):
    env = cfg.test_environment
    expected = "pytest-v1" if language == "python" else "node-test-v1"
    if env is None or env.profile != expected:
        raise ValueError("Named tests require the matching reviewed test environment")
    if len(cfg.validation_commands) != 1 or not cfg.validation_commands[0].required:
        raise ValueError(
            "Named v1 profiles require one required complete-suite command"
        )
    argv = cfg.validation_commands[0].argv
    prefix = ["python", "-m", "pytest"] if language == "python" else ["node", "--test"]
    if argv[: len(prefix)] != prefix or len(argv) == len(prefix):
        raise ValueError(
            "Test command must use the reviewed runner and explicit suite paths"
        )
    from app.task_contracts import relative_path

    for path in argv[len(prefix) :]:
        relative_path(path)
        if path.startswith("-"):
            raise ValueError("Only literal suite paths may follow the runner")
    if language == "node" and not env.dependency_lock.endswith("package-lock.json"):
        raise ValueError("Node profile requires an npm package-lock.json")
