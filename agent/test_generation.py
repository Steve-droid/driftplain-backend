"""Named test-generation policy on the shared disposable execution lifecycle."""

import ast
import shutil
from pathlib import Path

from agent.errors import AgentConfigError
from agent.other_execution import build_prompt
from app.task_contracts import validate_execution_config
from app.test_generation_contracts import (
    TEST_PROFILES,
    validate_test_environment,
    validate_test_paths,
)

SYSTEM_PROMPT = """Add useful regression tests for the supplied change and expected behavior.
Only add new test files in permitted test directories. Do not edit existing files, production
code, configuration, dependencies, locks, runners or assertions. Never skip, xfail, todo,
select only tests, alter collection, mock the reporter, or bypass a check. Use the configured
pytest or Node node:test runner and installed dependencies. Test failures may be mistakes in
your tests: inspect evidence and correct new tests only. Use validation_validate with no
arguments; no shell or installation. Passing tests are evidence of execution, not proof of
correctness or useful bug coverage. Return the requested JSON summary honestly."""


def profile(configuration):
    return TEST_PROFILES[
        (configuration["policy"]["language"], configuration["model"]["providerModelId"])
    ]


def resolve_test_profile(configuration):
    try:
        validate_execution_config(configuration, configuration["projectId"])
        p = profile(configuration)
        m = configuration["model"]
        if (
            configuration["taskType"],
            configuration["executionMode"],
            configuration["capability"],
            configuration["runtimeVersion"],
            m["provider"],
            m["authMode"],
            m["credentialEnvVar"],
        ) != (
            "test_generation",
            "opencode",
            "test_generation_" + p.language,
            p.version,
            p.provider,
            "api_key",
            p.credential_env,
        ):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise AgentConfigError("Unsupported named test-generation profile") from None
    if p.verification_status != "verified":
        raise AgentConfigError(
            "Test generation integration pending exact live verification"
        )
    return p


def prompt(cfg, inputs):
    return build_prompt(
        cfg.model_copy(
            update={
                "instructions": cfg.instructions
                or "Add regression tests for the supplied inputs."
            }
        ),
        inputs,
    )


def prepare(work, executor, language):
    validate_test_environment(work.cfg, language)
    baseline = work.root / "baseline"
    shutil.copytree(work.path, baseline)
    executor.baseline = baseline
    # This path is in the launcher's same-path job scratch, never inside the editor mount.
    reporters = work.root / "reporters"
    shutil.copytree(Path(__file__).parent / "test_reporters", reporters)
    executor.reporters = reporters


def check_patch(work, patch, language):
    if patch is None:
        raise AgentConfigError("No generated tests were added")
    try:
        validate_test_paths(language, patch.files)
    except ValueError as e:
        raise AgentConfigError(str(e)) from None
    for f in patch.files:
        p = work.path / f.path
        if p.stat().st_mode & 0o111:
            raise AgentConfigError("Generated tests cannot be executable files")
        try:
            text = p.read_text(encoding="utf-8")
            if language == "python":
                tree = ast.parse(text)
                # Prevent ordinary collection/skip bypasses before any test execution.
                forbidden = {
                    "skip",
                    "skipif",
                    "xfail",
                    "importorskip",
                    "exit",
                    "_exit",
                    "pytest_plugins",
                    "pytestmark",
                    "__test__",
                }
                for node in ast.walk(tree):
                    name = (
                        node.id
                        if isinstance(node, ast.Name)
                        else node.attr
                        if isinstance(node, ast.Attribute)
                        else node.name
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        else ""
                    )
                    if name in forbidden or name.startswith("pytest_"):
                        raise ValueError(
                            "Generated tests cannot alter collection or skip execution"
                        )
        except (UnicodeError, SyntaxError, ValueError) as e:
            raise AgentConfigError(
                "Malformed or forbidden generated test: " + type(e).__name__
            ) from None
