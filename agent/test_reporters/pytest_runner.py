"""Trusted pytest entry point. Mounted read-only outside repository import paths."""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

PREFIX = "DRIFTPLAIN_TEST_REPORT_V1="


class Reporter:
    def __init__(self):
        self.tests = {}
        self.errors = []

    def pytest_collection_finish(self, session):
        for item in session.items:
            if item.nodeid in self.tests:
                self.errors.append("duplicate identity")
            self.tests[item.nodeid] = {
                "id": item.nodeid,
                "path": item.nodeid.split("::")[0],
                "status": "not_run",
            }

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.errors.append("collection failed or skipped")

    def pytest_runtest_logreport(self, report):
        row = self.tests.get(report.nodeid)
        if row is None:
            self.errors.append("uncollected test")
            return
        if report.skipped or hasattr(report, "wasxfail"):
            row["status"] = "skipped"
        elif report.failed:
            row["status"] = "failed" if report.when == "call" else "error"
        elif report.when == "call" and row["status"] == "not_run":
            row["status"] = "passed"


if __name__ == "__main__":
    if (
        hashlib.sha256(
            Path("/opt/driftplain-tests/requirements.lock").read_bytes()
        ).hexdigest()
        != os.environ["DRIFTPLAIN_DEPENDENCY_SHA256"]
        or pytest.__version__ != "9.0.3"
    ):
        raise SystemExit("Reviewed pytest environment/lock mismatch")
    sys.path.insert(0, "/workspace")
    reporter = Reporter()
    # Fixed policy flags cannot be overridden by repository addopts or plugin autoload.
    code = pytest.main(
        ["-o", "addopts=", "-p", "no:cacheprovider", "--strict-markers", *sys.argv[1:]],
        plugins=[reporter],
    )
    print(
        "\n"
        + PREFIX
        + json.dumps(
            {
                "version": 1,
                "tests": list(reporter.tests.values()),
                "errors": reporter.errors,
            }
        ),
        flush=True,
    )
    raise SystemExit(code)
