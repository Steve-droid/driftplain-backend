"""Reviewed runner reports, bound externally to the exact base and generated paths."""

import hashlib
import json
import time
from pathlib import Path

from agent.validation_executor import DockerValidationExecutor
from app.task_contracts import Artifact, TestEvidence, ValidationCheck, relative_path

PREFIX = b"DRIFTPLAIN_TEST_REPORT_V1="


def parse_report(raw):
    lines = [
        line[len(PREFIX) :] for line in raw.splitlines() if line.startswith(PREFIX)
    ]
    if len(lines) != 1:
        raise ValueError("Missing or ambiguous runner report")
    body = json.loads(lines[0])
    if (
        not isinstance(body, dict)
        or set(body) != {"version", "tests", "errors"}
        or type(body["version"]) is not int
        or body["version"] != 1
    ):
        raise ValueError("Malformed runner report")
    return body


def report_tests(body):
    if (
        body.get("errors") != []
        or not isinstance(body.get("tests"), list)
        or not 0 < len(body["tests"]) <= 10000
    ):
        raise ValueError("Collection/environment failure or zero discovered tests")
    tests = {}
    for row in body["tests"]:
        if not isinstance(row, dict) or set(row) != {"id", "path", "status"}:
            raise ValueError("Malformed test identity")
        identity, path = row["id"], row["path"]
        if (
            not isinstance(identity, str)
            or not 0 < len(identity) <= 2048
            or identity in tests
            or not identity.startswith(path + ":")
        ):
            raise ValueError("Duplicate or mismatched test identity")
        relative_path(path)
        if row["status"] != "passed":
            raise ValueError("Failed, skipped or unexecuted test")
        tests[identity] = path
    return tests


def identity_digest(tests):
    return hashlib.sha256(
        json.dumps(sorted(tests.items()), separators=(",", ":")).encode()
    ).hexdigest()


def evaluate_reports(baseline, candidate, generated):
    base, final = report_tests(baseline), report_tests(candidate)
    new = {i: p for i, p in final.items() if p in generated}
    old = {i: p for i, p in final.items() if p not in generated}
    if base != old or not new or set(new.values()) != set(generated):
        raise ValueError(
            "Existing suite changed or generated tests were not discovered/executed"
        )
    return {
        "generatedTestsDiscovered": len(new),
        "generatedTestsExecuted": len(new),
        "existingTestsDiscovered": len(old),
        "existingTestsExecuted": len(old),
        "baselineIdentitySha256": identity_digest(base),
        "existingIdentitySha256": identity_digest(old),
    }


class TestValidationExecutor(DockerValidationExecutor):
    __test__ = False

    def __init__(self, cfg, language):
        super().__init__(cfg.resources)
        self.cfg, self.language = cfg, language
        self.generated = []

    def argv(self, name, workspace, command):
        argv = super().argv(name, workspace, command)
        index = argv.index("--workdir")
        argv[index:index] = [
            "--mount",
            f"type=bind,src={self.reporters},dst=/driftplain-reporters,readonly",
        ]
        # Absolute trusted entry point; no repository shadowing of reporter or pytest imports.
        if self.language == "python":
            replacement = [
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
                "python",
                "-I",
                "-B",
                "/driftplain-reporters/pytest_runner.py",
                *command.argv[3:],
            ]
        else:
            files = []
            for path in command.argv[2:]:
                selected = Path(workspace) / path
                if selected.is_dir():
                    files.extend(
                        p.relative_to(workspace).as_posix()
                        for p in selected.rglob("*")
                        if p.is_file()
                        and p.name.endswith((".test.js", ".test.cjs", ".test.mjs"))
                    )
                elif selected.is_file():
                    files.append(path)
            if not files or len(files) > 1000:
                raise ValueError(
                    "Node suite has no test files or exceeds the file ceiling"
                )
            replacement = [
                "node",
                "--test",
                "--test-reporter=/driftplain-reporters/node_reporter.cjs",
                *sorted(set(files)),
            ]
        argv[-len(command.argv) :] = [
            "DRIFTPLAIN_DEPENDENCY_SHA256="
            + self.cfg.test_environment.dependency_sha256,
            *replacement,
        ]
        return argv

    def run(self, workspace, command, **kw):
        common = {
            "command_id": command.id,
            "execution_revision_id": kw["revision"],
            "base_commit": kw["base_commit"],
            "patch_sha256": kw["patch_sha256"],
        }
        env = self.cfg.test_environment
        start = time.monotonic()
        budget = min(kw["seconds"], command.max_seconds)
        max_bytes = min(
            kw["max_bytes"]
            if kw.get("max_bytes") is not None
            else self.resources.max_output_bytes,
            self.resources.max_output_bytes,
        )
        reports, logs = [], []
        duration = 0
        exit_code = 0
        reason = None
        unavailable = False
        for root in (self.baseline, workspace):
            try:
                raw_lock = (root / env.dependency_lock).read_bytes()
                if hashlib.sha256(raw_lock).hexdigest() != env.dependency_sha256:
                    raise ValueError()
            except (OSError, ValueError):
                reason, unavailable = (
                    "Dependency lock does not match the reviewed environment",
                    True,
                )
                break
            remaining = budget - (time.monotonic() - start)
            if remaining <= 0:
                reason, unavailable = (
                    "Test validation wall-clock budget exhausted",
                    True,
                )
                break
            check, artifact = super().run(
                root,
                command,
                **{
                    **kw,
                    "seconds": remaining,
                    "max_bytes": max_bytes - sum(map(len, logs)),
                },
            )
            if artifact:
                logs.append((Path(kw["out"]) / artifact.path).read_bytes())
            if check.status == "unavailable":
                reason, unavailable = check.reason, True
                break
            duration += check.duration_ms
            exit_code = check.exit_code
            try:
                report = parse_report(logs[-1])
                reports.append(report)
                if report["errors"] or exit_code not in (0, 1):
                    reason, unavailable = (
                        "Test collection, dependency or environment failure",
                        True,
                    )
                    break
                if exit_code:
                    reason = "Configured tests failed"
                    break
                report_tests(report)
            except (ValueError, TypeError, KeyError):
                reason, unavailable = (
                    "Missing, malformed, skipped or incomplete test evidence",
                    True,
                )
                break
        evidence = None
        if not reason:
            try:
                counts = evaluate_reports(*reports, self.generated)
                evidence = TestEvidence(
                    profile=env.profile,
                    dependency_sha256=env.dependency_sha256,
                    generated_paths=self.generated,
                    **{
                        k: v for k, v in counts.items() if not k.startswith("generated")
                    },
                )
            except (ValueError, TypeError):
                reason, unavailable = (
                    "Existing suite changed or generated tests not executed",
                    True,
                )
        raw = b"\n--- baseline / candidate ---\n".join(logs)
        # The combined log keeps original reports and diagnostic output, bounded across both runs.
        artifact = None
        if logs:
            path = Path(kw["out"]) / f"validation-{command.id}.log"
            if len(raw) > max_bytes:
                raw = b""  # Never emit an over-budget or deceptively truncated evidence artifact.
                reason, unavailable = (
                    "Combined validation output ceiling exceeded",
                    True,
                )
            path.write_bytes(raw)
            artifact = Artifact(
                id="validation-" + command.id,
                kind="validation_log",
                path=path.name,
                sha256=hashlib.sha256(raw).hexdigest(),
                size_bytes=len(raw),
            )
        if unavailable:
            return ValidationCheck(
                **common,
                status="unavailable",
                reason=reason,
                log_artifact_id=artifact.id if artifact else None,
            ), artifact
        return ValidationCheck(
            **common,
            status="failed" if reason else "passed",
            exit_code=exit_code if reason else 0,
            duration_ms=duration,
            log_artifact_id=artifact.id,
            reason=reason,
            test_evidence=evidence,
            generated_tests_discovered=counts["generatedTestsDiscovered"]
            if evidence
            else None,
            generated_tests_executed=counts["generatedTestsExecuted"]
            if evidence
            else None,
        ), artifact
