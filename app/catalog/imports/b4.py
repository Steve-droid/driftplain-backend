"""Independent/CI evidence adapters. Pure parsing; upstream code is never executed."""

import base64
import csv
import hashlib
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.catalog.imports.contracts import (
    Batch,
    Citation,
    Manifest,
    Metric,
    Row,
    decode,
    fingerprint,
)

METRICS = {
    "deepswe-1-1": {
        "verified_task_success": ("percent", "higher"),
        "mean_cost_usd": ("USD", "lower"),
        "mean_output_tokens": ("tokens", "lower"),
        "mean_agent_steps": ("steps", "lower"),
    },
    "codereviewbench": {
        "f1": ("percent", "higher"),
        "recall": ("percent", "higher"),
        "precision": ("percent", "higher"),
    },
    "testgeneval": {"e_at_1": ("percent", "higher")},
    "livebench-2026-06-25": {"average_score": ("percent", "higher")},
    "livecodebench-v5": {"pass_at_1": ("percent", "higher")},
    "swt-bench": {"success_rate": ("percent", "higher")},
    "logdx-ci": {
        "diagnosis_score_v1_1": ("ratio", "higher"),
        "confident_error_rate": ("ratio", "lower"),
    },
    "ci-repair-bench": {"pass_at_1": ("percent", "higher")},
    "realvuln-3-1-0": {},
}
DS_UNIT = "pass@1 is attempt pass rate over scored rollout attempts. pass@4 is tasks with at least one passing rollout divided by tasks attempted. Context-window failures and agent timeouts are scored failures; provider/verifier/network errors are excluded. Efficiency aggregates are over every scored attempt."
DS_CI = "95% run-to-run: SE across repeated whole-benchmark passes (1.96 * std(runs)/sqrt(R))"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def count(value, minimum=1):
    require(type(value) is int and value >= minimum, "invalid count")
    return value


def number(value):
    require(
        not isinstance(value, bool) and isinstance(value, (str, int, float, Decimal)),
        "invalid number",
    )
    result = Decimal(str(value))
    require(result.is_finite(), "non-finite number")
    return result


def csv_rows(raw):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    require(
        reader.fieldnames and len(set(reader.fieldnames)) == len(reader.fieldnames),
        "duplicate/missing CSV header",
    )
    rows = list(reader)
    require(rows and len(rows) <= 10000, "empty/oversize CSV")
    require(
        all(None not in r and all(v is not None for v in r.values()) for r in rows),
        "ragged CSV",
    )
    return reader.fieldnames, rows


def build_row(spec, digest, label, locator, protocol, metrics, data, *, url=None):
    return Row(
        locator=locator,
        model_label=label,
        version=spec["version"],
        protocol=protocol,
        evaluator=spec["evaluator"],
        publication_date=None,
        citation=Citation(
            url=url or spec["importContract"]["url"],
            locator=locator,
            content_hash=digest,
        ),
        metrics=tuple(metrics),
        source_data=data,
    )


def validate_rows(sid, rows, source):
    identities = set()
    for row in rows:
        require(row.version == source["version"], "benchmark version changed")
        require(row.evaluator == source["evaluator"], "evaluator changed")
        require(row.locator not in identities, "duplicate observation identity")
        identities.add(row.locator)
        require(
            len(row.metrics) == len(METRICS[sid])
            and {m.key for m in row.metrics} == set(METRICS[sid]),
            "metric schema changed",
        )
        for m in row.metrics:
            require(
                (m.unit, m.direction) == METRICS[sid][m.key],
                "metric unit/direction mismatch",
            )
        p = row.protocol
        if sid == "livecodebench-v5":
            require(
                p["scenario"] == "code-generation"
                and p["window_start"] == "2024-07-01"
                and p["window_end"] == "2025-02-01"
                and type(p["contaminated"]) is bool,
                "LiveCodeBench window/scenario mismatch",
            )
            count(p["task_total"])
        if sid == "swt-bench":
            require(
                p["split"] == "Verified"
                and p["mode"] in ("unittest", "reproduction")
                and isinstance(p["agent"], str)
                and p["agent"].strip(),
                "SWT protocol mismatch",
            )
        if sid == "logdx-ci":
            require(
                p["source_release"] == "v1.2"
                and p["runner"] == "real-agent-v1"
                and p["debugger"] == row.model_label == "Sonnet 4.6",
                "LogDx runner mismatch",
            )
            require(
                isinstance(p["method"], str)
                and p["method"].strip()
                and isinstance(p["exclusions"], str)
                and p["exclusions"].strip(),
                "missing LogDx method/exclusions",
            )
            require(
                count(p["case_count"]) == 35
                and all(m.denominator == 35 for m in row.metrics),
                "LogDx denominator mismatch",
            )
        if sid == "ci-repair-bench":
            require(
                p["paper"] == "2604.27148v2"
                and p["instances"] == 567
                and p["repositories"] == 103
                and p["attempts"] == 1
                and p["success"] == "full GitHub Actions CI re-execution passes"
                and p["runner"] == "reference repair pipeline",
                "CI repair protocol mismatch",
            )
            require(
                all(m.denominator == 567 and m.attempts == 1 for m in row.metrics),
                "repair denominator mismatch",
            )
        if sid in ("swt-bench", "logdx-ci", "ci-repair-bench"):
            require(
                p["recommendation_eligible"] is False,
                "supplementary evidence cannot enable recommendations",
            )
    minimum = 0 if sid == "realvuln-3-1-0" else 1 if sid == "logdx-ci" else 3
    require(len({r.model_label for r in rows}) >= minimum, "incomplete source coverage")


def testgeneval(raw, spec, digest):
    fields, data = csv_rows(raw)
    require(
        fields
        == ["Model", "cov", "fu@1", "f@1", "f@5", "l@1", "l@5", "e@1", "e@5", "link"],
        "TestGenEval Extra schema changed",
    )
    rows = []
    for d in data:
        for key in fields[1:-1]:
            require(0 <= number(d[key]) <= 100, "TestGenEval scale changed")
        rows.append(
            build_row(
                spec,
                digest,
                d["Model"],
                d["Model"],
                {
                    "configuration": "Extra",
                    "language": "Python",
                    "attempts": 1,
                    "runner": "TestGenEval fixed test-completion generation",
                },
                [
                    Metric(
                        key="e_at_1",
                        value=d["e@1"],
                        attempts=1,
                        reported_value=d["e@1"],
                    )
                ],
                d,
            )
        )
    return (
        rows,
        "Complete supplied CSV; Extra e@1 only. Older exact labels remain unresolved; other columns are retained as source data.",
    )


def deepswe(raw, spec, digest):
    d = decode(raw)
    require(
        d["n_tasks_in_set"] == 113 and d["unit"] == DS_UNIT,
        "DeepSWE version/denominator/scoring changed",
    )
    timestamp = datetime.fromisoformat(d["generated_at"])
    require(timestamp.tzinfo is not None, "DeepSWE missing generation timezone")
    scope = spec["importContract"]["scope"]
    rows = []
    for r in d["rows"]:
        if r["config"] not in scope:
            continue
        expected = spec["importContract"]["configurations"][r["config"]]
        require(
            r["model"] == expected["model"]
            and r["reasoning_effort"] == expected["effort"],
            "DeepSWE model/effort changed",
        )
        require(
            r["harness"] == "mini-swe-agent" and r["source"] == "deep-swe",
            "DeepSWE runner changed",
        )
        require(
            count(r["n_tasks_attempted"]) == 113
            and count(r["n_runs"]) == 4
            and count(r["n_attempted"]) == 452,
            "incomplete DeepSWE trial set",
        )
        require(
            r.get("completed_by_attempt", [113] * 4) == [113] * 4,
            "incomplete DeepSWE passes",
        )
        require(
            count(r["n_passed"], 0) <= 452
            and r["ci_passed"] == r["n_passed"]
            and r["ci_attempted"] == 452,
            "DeepSWE inconsistent counts",
        )
        require(
            abs(number(r["pass_at_1"]) - Decimal(r["n_passed"]) / 452)
            < Decimal("1e-12")
            and r["pass_rate"] == r["pass_at_1"],
            "DeepSWE success mismatch",
        )
        require(r["ci_method"] == DS_CI, "DeepSWE uncertainty changed")
        p = {
            "dataset": "DeepSWE v1.1",
            "task_total": 113,
            "runner": r["harness"],
            "effort": r["reasoning_effort"],
            "attempts": 4,
            "aggregation": "attempt pass rate across four complete benchmark passes",
            "uncertainty_method": r["ci_method"],
            "cost_basis": r.get(
                "cost_basis",
                "Upstream reported mean_cost_usd; pricing basis not separately supplied",
            ),
        }
        metrics = [
            Metric(
                key="verified_task_success",
                value=number(r["pass_at_1"]) * 100,
                confidence_low=number(r["ci_lo"]) * 100,
                confidence_high=number(r["ci_hi"]) * 100,
                confidence_level=Decimal(".95"),
                denominator=452,
                attempts=4,
                reported_value=str(r["pass_at_1"]),
            )
        ]
        for key, unit in [
            ("mean_cost_usd", "USD"),
            ("mean_output_tokens", "tokens"),
            ("mean_agent_steps", "steps"),
        ]:
            metrics.append(
                Metric(
                    key=key,
                    value=r[key],
                    unit=unit,
                    direction="lower",
                    missing_reason="not reported by source" if r[key] is None else None,
                    reported_value=str(r[key]) if r[key] is not None else None,
                )
            )
        rows.append(
            build_row(
                spec,
                digest,
                r["model"],
                r["config"],
                p,
                metrics,
                {
                    **r,
                    "artifact_generated_at": d["generated_at"],
                    "artifact_scope": d["scope"],
                },
            )
        )
    require(
        {r.locator for r in rows} == set(scope) and len(rows) == len(scope),
        "incomplete DeepSWE launch scope",
    )
    return (
        rows,
        "Explicit launch scope: three named model/effort configurations, each 113 tasks x four complete passes. Other upstream configurations are retained in raw evidence but not promoted; incomplete trial sets reject this scope. Cost is upstream reported, not a Driftplain token-price estimate.",
    )


def codereview(raw, spec, digest):
    d = decode(raw)
    require(
        isinstance(d["entries"], list) and isinstance(d["averages"], dict),
        "CodeReviewBench schema changed",
    )
    rows = []
    for r in d["entries"]:
        require(
            r["harness"] == "kodus"
            and r["executionMode"] == "replay"
            and r["judge"] == "claude-haiku-4-5",
            "CodeReviewBench methodology changed",
        )
        prs = count(r["cases"])
        bugs = count(r["goldensTotal"])
        require(prs <= 30 and bugs <= 95, "CodeReviewBench dataset changed")
        require(
            r["key"] == "kodus::" + r["modelId"], "CodeReviewBench row identity changed"
        )
        p = {
            "runner": "kodus",
            "runner_version": r["harnessVersion"],
            "execution_mode": r["executionMode"],
            "judge": r["judge"],
            "pull_requests": prs,
            "bugs": bugs,
            "provider": r["provider"],
            "provider_route": r["modelId"],
            "access_path": r["accessPath"],
            "reasoning_config": r["reasoningConfig"],
            "effort": r["reasoningEffort"]
            if r["reasoningEffort"] is not None
            else {"unknown_reason": "Upstream effort is null"},
            "recommendation_eligible": (prs == 30 and bugs == 95),
            "coverage": "complete" if prs == 30 and bugs == 95 else "incomplete",
        }
        # Explicit cross-model evidence group from the source's evaluation protocol.
        # Serving route remains recorded, but is not itself a different benchmark.
        p["comparison_group"] = fingerprint(
            {
                "benchmark": spec["version"],
                "runner": r["harness"],
                "runner_version": r["harnessVersion"],
                "mode": r["executionMode"],
                "judge": r["judge"],
                "pull_requests": prs,
                "bugs": bugs,
                "reasoning_config": r["reasoningConfig"],
                "effort": r["reasoningEffort"],
            }
        )
        metrics = [
            Metric(
                key=k,
                value=r[src],
                missing_reason="not reported by source" if r[src] is None else None,
            )
            for k, src in [
                ("f1", "f1"),
                ("precision", "precision"),
                ("recall", "score"),
            ]
        ]
        rows.append(build_row(spec, digest, r["modelId"], r["key"], p, metrics, r))
    require(
        sum(r.protocol["recommendation_eligible"] for r in rows) >= 3,
        "incomplete CodeReviewBench comparable group",
    )
    return (
        rows,
        "Pinned detailed official score table: Kodus replay / Haiku 4.5; complete 30-PR/95-bug evidence and incomplete groups separated. Provider routes and published uncertainty retained verbatim. Recall uncertainty is not F1 uncertainty; upstream costBasis may use list pricing, so costs remain attributed source data.",
    )


def realvuln(raw, spec, digest):
    d = decode(raw)
    require(
        d["schema_version"] == "3.0"
        and d["benchmark_version"] == "3.1.0"
        and d["ground_truth_version"] == "3.0.0",
        "RealVuln manifest version mismatch",
    )
    require(
        d["dataset"]["repo_count"] == 140 and len(d["repos"]) == 140,
        "RealVuln repository coverage mismatch",
    )
    require(
        d["release_date"] == "2026-09-10" and d["default_prompt_version"],
        "RealVuln release/prompt missing",
    )
    return (
        [],
        "Manifest-authoritative benchmark 3.1.0 / ground truth 3.0.0, 140 repositories. README version is stale. No verified matching complete strict-F3 general-purpose LLM score artifact; definition only and empty recommendation group. Legacy 2.1 results are not relabeled.",
    )


def unpack_bundle(raw, spec):
    d = decode(raw)
    contract = spec["importContract"]
    require(
        d["schema_version"] == 1
        and type(d["schema_version"]) is int
        and d["source_id"] == spec["id"]
        and d["revision"] == contract["revision"],
        "bundle identity mismatch",
    )
    require(len(d["files"]) == len(contract["files"]), "incomplete artifact bundle")
    contents = {}
    for actual, expected in zip(d["files"], contract["files"], strict=True):
        require(
            set(actual) == set(expected) | {"content_base64"}
            and all(actual[k] == v for k, v in expected.items()),
            "bundle artifact metadata mismatch",
        )
        b = base64.b64decode(actual["content_base64"], validate=True)
        require(
            len(b) == expected["byte_count"]
            and hashlib.sha256(b).hexdigest() == expected["sha256"],
            "bundle artifact integrity mismatch",
        )
        require(expected["path"] not in contents, "duplicate bundle artifact")
        contents[expected["path"]] = b
    return contents


def livebench(raw, spec, digest):
    files = unpack_bundle(raw, spec)
    fields, data = csv_rows(files["public/table_2026_06_25.csv"])
    categories = decode(files["public/categories_2026_06_25.json"])
    require(isinstance(categories, dict) and categories, "missing LiveBench categories")
    tasks = [t for ts in categories.values() for t in ts]
    require(
        len(tasks) == len(set(tasks)) and set(fields) == {"model", *tasks},
        "LiveBench missing/duplicate task metadata",
    )
    rows = []
    descriptor = spec["importContract"]["files"][0]
    for r in data:
        values = {t: number(r[t]) for t in tasks}
        require(
            all(0 <= v <= 100 for v in values.values()), "LiveBench score scale changed"
        )
        avgs = {c: sum(values[t] for t in ts) / len(ts) for c, ts in categories.items()}
        scores = [("overall", "all", "all", sum(avgs.values()) / len(avgs))]
        scores += [("category", c, "all", v) for c, v in avgs.items()]
        scores += [
            ("task", c, t, values[t]) for c, ts in categories.items() for t in ts
        ]
        for scope, category, task, value in scores:
            p = {
                "release": "2026-06-25",
                "scope": scope,
                "category": category,
                "task": task,
                "aggregation": "mean of category means"
                if scope == "overall"
                else "mean of category tasks"
                if scope == "category"
                else "published task score",
                "model_configuration": r["model"],
            }
            rows.append(
                build_row(
                    spec,
                    descriptor["sha256"],
                    r["model"],
                    r["model"] + "/" + scope + "/" + category + "/" + task,
                    p,
                    [Metric(key="average_score", value=value)],
                    {
                        "task_scores": r,
                        "category_tasks": categories,
                        "artifact_revision": spec["importContract"]["revision"],
                    },
                    url=descriptor["url"],
                )
            )
    return (
        rows,
        "Exact 2026-06-25 release from one pinned site revision; all table rows and category/task metadata. Overall is the mean of category means; category and task scopes remain separate. Source labels retain effort/configuration.",
    )


def parse_b4(sid, raw, spec, registry_hash):
    try:
        digest = hashlib.sha256(raw).hexdigest()
        contract = spec["importContract"]
        require(
            len(raw) <= contract["max_bytes"], "source exceeds operational byte budget"
        )
        if contract.get("sha256"):
            require(digest == contract["sha256"], "immutable artifact hash mismatch")
        if contract["mode"] == "reviewed_manifest":
            doc = decode(raw)
            require(
                isinstance(doc, dict) and type(doc.get("schema_version")) is int,
                "invalid manifest schema version",
            )
            m = Manifest.model_validate(doc)
            require(
                m.source_id == sid and m.registry_hash == registry_hash,
                "manifest source/registry mismatch",
            )
            rows = m.rows
            note = m.coverage_note
            review = m.review
            publication = m.publication_date
        else:
            rows, note = {
                "deepswe-1-1": deepswe,
                "testgeneval": testgeneval,
                "codereviewbench": codereview,
                "realvuln-3-1-0": realvuln,
                "livebench-2026-06-25": livebench,
            }[sid](raw, spec, digest)
            review = None
            publication = None
        validate_rows(sid, rows, spec)
        return Batch(
            source_id=sid,
            content_hash=digest,
            publication_date=publication,
            coverage_note=note,
            rows=tuple(rows),
            review=review,
        )
    except (
        KeyError,
        TypeError,
        IndexError,
        UnicodeError,
        InvalidOperation,
        ZeroDivisionError,
        csv.Error,
    ) as exc:
        raise ValueError("source schema changed; adapter review required") from exc
