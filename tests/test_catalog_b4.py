"""B4 semantic boundaries, complete artifacts and transactional public evidence."""

import base64
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.catalog.imports.adapters import parse
from app.catalog.imports.contracts import Metric
from app.catalog.imports.fetch import FetchResponse
from app.catalog.imports.pipeline import import_bytes, import_source
from app.catalog.imports.registry import get_source, source_ids
from app.models import (
    CatalogMetricDefinition,
    CatalogModelAlias,
    CatalogObservation,
    CatalogSourcePayload,
    CatalogSourceSnapshot,
)

DATA = Path(__file__).resolve().parents[1] / "data/catalog/b4"
NOW = datetime(2026, 9, 24, tzinfo=UTC)
IDS = tuple(json.loads((DATA / "contracts.json").read_bytes()))


def raw(s):
    return (DATA / (s + (".csv" if s == "testgeneval" else ".json"))).read_bytes()


def changed(s, mutate):
    doc = json.loads(raw(s))
    mutate(doc)
    return json.dumps(doc).encode()


def metric(row, key):
    return next(m for m in row.metrics if m.key == key)


def test_complete_registry_keeps_normative_audit():
    assert len(source_ids()) == 19
    assert get_source("logdx-ci")["importMode"] == "automatic_structured"
    assert get_source("logdx-ci")["importContract"]["mode"] == "reviewed_manifest"


@pytest.mark.parametrize("s", IDS)
def test_all_nine_launch_inputs(s):
    batch = parse(s, raw(s))
    assert batch.content_hash == hashlib.sha256(raw(s)).hexdigest()
    assert len(batch.rows) == 0 if s == "realvuln-3-1-0" else len(batch.rows) > 0
    assert all(r.reviewed_mapping is None for r in batch.rows)


def test_extra_is_not_full_and_other_columns_survive():
    batch = parse("testgeneval", raw("testgeneval"))
    assert len(batch.rows) == 10
    gpt = next(r for r in batch.rows if r.model_label == "GPT-4o")
    assert metric(gpt, "e_at_1").value == Decimal("30.4")
    assert gpt.source_data["f@1"] == "31.9"
    assert gpt.protocol["configuration"] == "Extra"


@pytest.mark.parametrize(
    "old,new", [(b"e@1", b"extra"), (b"30.4", b"130.4"), (b"30.4", b"NaN")]
)
def test_extra_schema_and_scale_fail_closed(old, new):
    with pytest.raises(ValueError):
        parse("testgeneval", raw("testgeneval").replace(old, new))


def test_csv_duplicate_model_rejected():
    data = raw("testgeneval")
    data += data.splitlines(keepends=True)[1]
    with pytest.raises(ValueError):
        parse("testgeneval", data)


def test_deepswe_complete_scope_effort_cost_uncertainty():
    batch = parse("deepswe-1-1", raw("deepswe-1-1"))
    assert len({r.model_label for r in batch.rows}) == 3
    r = next(r for r in batch.rows if r.model_label == "gpt-6-astra")
    assert r.protocol["effort"] == "xhigh"
    assert metric(r, "mean_cost_usd").unit == "USD"
    assert metric(r, "mean_output_tokens").value > 100
    assert metric(r, "verified_task_success").confidence_level == Decimal(".95")
    assert r.source_data["cost_basis"]
    assert "scope" in batch.coverage_note.lower()


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(n_tasks_in_set=114),
        lambda d: d["rows"][0].update(n_runs=3),
        lambda d: d["rows"][0].update(n_attempted=451),
        lambda d: d["rows"][0].update(n_tasks_attempted=111),
        lambda d: d["rows"][0].update(completed_by_attempt=[113, 113, 113]),
        lambda d: d["rows"].pop(0),
        lambda d: d["rows"][0].update(ci_method="unknown uncertainty"),
    ],
)
def test_deepswe_incomplete_scope_or_method_rejected(change):
    with pytest.raises(ValueError):
        parse("deepswe-1-1", changed("deepswe-1-1", change))


def test_review_routes_incomplete_rows_and_uncertainty():
    batch = parse("codereviewbench", raw("codereviewbench"))
    assert len(batch.rows) == 13
    full = next(r for r in batch.rows if r.model_label == "deepseek-v4-pro")
    partial = next(r for r in batch.rows if r.model_label == "glm-5.2@fireworks")
    assert full.protocol["recommendation_eligible"] is True
    assert partial.protocol["recommendation_eligible"] is False
    assert partial.protocol["pull_requests"] == 29 and partial.protocol["bugs"] == 91
    assert partial.protocol["provider_route"] == "glm-5.2@fireworks"
    assert (
        len(
            {
                r.protocol["comparison_group"]
                for r in batch.rows
                if r.protocol["recommendation_eligible"]
            }
        )
        == 1
    )
    assert partial.protocol["comparison_group"] != full.protocol["comparison_group"]
    assert metric(full, "f1").value == Decimal("43.89")
    assert full.source_data["runToRunVariance"]["f1"]["stdev"] == 0.28
    # Published recall interval must never be attached to F1.
    assert metric(full, "f1").confidence_low is None


def test_realvuln_manifest_authority_no_legacy_scores():
    b = parse("realvuln-3-1-0", raw("realvuln-3-1-0"))
    assert not b.rows
    assert "3.1.0" in b.coverage_note and "140" in b.coverage_note
    with pytest.raises(ValueError):
        parse(
            "realvuln-3-1-0",
            raw("realvuln-3-1-0").replace(
                b'"benchmark_version": "3.1.0"', b'"benchmark_version": "3.0.0"'
            ),
        )


def test_livebench_scopes_and_complete_bundle():
    b = parse("livebench-2026-06-25", raw("livebench-2026-06-25"))
    assert len({r.model_label for r in b.rows}) >= 3
    assert {r.protocol["scope"] for r in b.rows} == {"overall", "category", "task"}
    assert all(r.protocol["release"] == "2026-06-25" for r in b.rows)
    with pytest.raises(ValueError):
        parse(
            "livebench-2026-06-25",
            changed("livebench-2026-06-25", lambda d: d["files"].pop()),
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(revision="main"),
        lambda d: d["files"][0].update(content_base64="e30="),
        lambda d: d["files"].append(d["files"][0]),
        lambda d: d["files"][0].update(url="https://example.com/other.csv"),
    ],
)
def test_bundle_revision_integrity(change):
    with pytest.raises(ValueError):
        parse("livebench-2026-06-25", changed("livebench-2026-06-25", change))


def test_supplementary_method_identity_and_units():
    b = parse("logdx-ci", raw("logdx-ci"))
    assert len(b.rows) == 3 and {r.model_label for r in b.rows} == {"Sonnet 4.6"}
    assert len({r.protocol["method"] for r in b.rows}) == 3
    assert metric(b.rows[0], "diagnosis_score_v1_1").value == Decimal(".749")
    assert all(
        r.protocol["case_count"] == 35 and r.protocol["exclusions"] for r in b.rows
    )
    s = parse("swt-bench", raw("swt-bench"))
    assert {r.protocol["mode"] for r in s.rows} == {"unittest", "reproduction"}
    repair = parse("ci-repair-bench", raw("ci-repair-bench"))
    assert metric(repair.rows[0], "pass_at_1").value == Decimal("18.9")
    assert repair.rows[0].source_data["appliedPatches"] == 455
    assert all(
        r.protocol["recommendation_eligible"] is False
        for r in (*s.rows, *b.rows, *repair.rows)
    )


def test_livecodebench_exact_window_contamination():
    b = parse("livecodebench-v5", raw("livecodebench-v5"))
    assert len({r.model_label for r in b.rows}) >= 3
    assert all(
        r.protocol["window_start"] == "2024-07-01"
        and r.protocol["window_end"] == "2025-02-01"
        for r in b.rows
    )
    assert {r.protocol["contaminated"] for r in b.rows} == {True, False}
    with pytest.raises(ValueError):
        parse(
            "livecodebench-v5",
            changed(
                "livecodebench-v5",
                lambda d: d["rows"][0]["protocol"].update(window_end="2025-03-01"),
            ),
        )


@pytest.mark.parametrize(
    "unit,value",
    [
        ("percent", 101),
        ("ratio", 1.1),
        ("USD", -1),
        ("tokens", float("inf")),
        ("steps", -1),
        ("bogus", 2),
    ],
)
def test_metric_domains(unit, value):
    with pytest.raises(ValueError):
        Metric(key="x", value=value, unit=unit)


@pytest.mark.parametrize("s", IDS)
def test_atomic_b4_promotion_idempotency_and_aliases(migrated_engine, s):
    result = import_bytes(migrated_engine, s, raw(s), checked_at=NOW)
    assert result.status == "promoted"
    assert (
        import_bytes(migrated_engine, s, raw(s), checked_at=NOW).status == "unchanged"
    )
    from sqlalchemy.orm import Session

    with Session(migrated_engine) as db:
        assert db.scalar(select(func.count()).select_from(CatalogSourceSnapshot)) >= 1
        assert all(
            a.resolution_status == "unresolved"
            for a in db.scalars(select(CatalogModelAlias))
        )
        if s == "testgeneval":
            snap = db.get(CatalogSourceSnapshot, result.snapshot_id)
            assert snap.content_type == "text/csv"
            assert db.get(CatalogSourcePayload, snap.id).raw_bytes == raw(s)
        if s == "logdx-ci":
            definitions = list(
                db.scalars(
                    select(CatalogMetricDefinition).where(
                        CatalogMetricDefinition.key == "diagnosis_score_v1_1"
                    )
                )
            )
            assert definitions[0].maximum == 1


def test_failed_source_does_not_block_other_source_and_retains_history(migrated_engine):
    good = import_bytes(
        migrated_engine, "testgeneval", raw("testgeneval"), checked_at=NOW
    )
    bad = import_bytes(migrated_engine, "testgeneval", b"bad csv", checked_at=NOW)
    assert bad.status == "failed" and bad.snapshot_id == good.snapshot_id
    assert (
        import_bytes(
            migrated_engine, "logdx-ci", raw("logdx-ci"), checked_at=NOW
        ).status
        == "promoted"
    )
    # A valid removal snapshot retains all historical evidence.
    smaller = b"\n".join(raw("testgeneval").splitlines()[:-1]) + b"\n"
    newer = import_bytes(migrated_engine, "testgeneval", smaller, checked_at=NOW)
    assert newer.status == "promoted" and newer.snapshot_id != good.snapshot_id
    from sqlalchemy.orm import Session

    with Session(migrated_engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(CatalogObservation)
                .where(CatalogObservation.source_snapshot_id == good.snapshot_id)
            )
            == 10
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(CatalogObservation)
                .where(CatalogObservation.source_snapshot_id == newer.snapshot_id)
            )
            == 9
        )


def test_bundle_fetch_failure_rolls_back_source(migrated_engine):
    good = import_bytes(
        migrated_engine,
        "livebench-2026-06-25",
        raw("livebench-2026-06-25"),
        checked_at=NOW,
    )
    calls = []

    def fetcher(url, **kwargs):
        calls.append(url)
        if len(calls) == 2:
            raise ValueError("missing second file")
        doc = json.loads(raw("livebench-2026-06-25"))
        f = next(f for f in doc["files"] if f["url"] == url)
        return FetchResponse(200, base64.b64decode(f["content_base64"]))

    result = import_source(
        migrated_engine, "livebench-2026-06-25", fetcher=fetcher, checked_at=NOW
    )
    assert (
        result.status == "failed"
        and result.snapshot_id == good.snapshot_id
        and len(calls) == 2
    )


@pytest.mark.parametrize("s", IDS)
@pytest.mark.parametrize("body", [b"[]", b"null", b'{"rows":null}', b"{}"])
def test_malformed_shapes_fail_with_sanitizable_validation(s, body):
    with pytest.raises(ValueError):
        parse(s, body)


def test_deepswe_scope_cannot_be_reassigned_to_other_model():
    with pytest.raises(ValueError):
        parse(
            "deepswe-1-1",
            changed(
                "deepswe-1-1", lambda d: d["rows"][0].update(model="different-model")
            ),
        )


def test_supplementary_refresh_preserves_policy_runtime_and_groups(
    migrated_engine, db_session, client
):
    from sqlalchemy import text

    def unrelated():
        return {
            name: db_session.execute(
                text("SELECT * FROM " + name + " ORDER BY 1")
            ).all()
            for name in (
                "catalog_task_benchmark",
                "agent_runtime_config",
                "benchmark_result",
                "catalog_provider_deployment",
            )
        }

    before = unrelated()
    db_session.rollback()
    first = import_bytes(migrated_engine, "logdx-ci", raw("logdx-ci"), checked_at=NOW)
    updated = changed(
        "logdx-ci",
        lambda d: d["rows"][0]["protocol"].update(
            method="explicit-reviewed-new-method"
        ),
    )
    second = import_bytes(migrated_engine, "logdx-ci", updated, checked_at=NOW)
    assert first.status == second.status == "promoted"
    assert unrelated() == before
    response = client.get(
        "/catalog/v1/observations", params={"q": "Sonnet 4.6", "limit": 100}
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 6
    assert all(r["modelId"] is None for r in items)
    assert all(
        next(m for m in r["metrics"] if m["key"] == "diagnosis_score_v1_1")["unit"]
        == "ratio"
        for r in items
    )
    assert len({r["protocolId"] for r in items}) == 4


def test_measured_units_roundtrip_in_public_api(migrated_engine, client):
    assert (
        import_bytes(
            migrated_engine, "deepswe-1-1", raw("deepswe-1-1"), checked_at=NOW
        ).status
        == "promoted"
    )
    response = client.get("/catalog/v1/observations", params={"q": "gpt-6-astra"})
    assert response.status_code == 200
    row = response.json()["items"][0]
    by_key = {m["key"]: m for m in row["metrics"]}
    assert Decimal(str(by_key["mean_output_tokens"]["value"])) > 100
    assert by_key["mean_cost_usd"]["unit"] == "USD"
    assert by_key["verified_task_success"]["uncertaintyType"] == "confidence_interval"


def test_bundle_fetch_reproduces_offline_bytes_without_conditional_partial_state():
    from app.catalog.imports.acquisition import acquire_source

    doc = json.loads(raw("livebench-2026-06-25"))
    calls = []

    def fetcher(url, **kwargs):
        assert kwargs.get("etag") is None
        f = next(f for f in doc["files"] if f["url"] == url)
        calls.append(url)
        return FetchResponse(200, base64.b64decode(f["content_base64"]))

    assert acquire_source(
        get_source("livebench-2026-06-25"), fetcher=fetcher
    ).body == raw("livebench-2026-06-25")
    assert len(calls) == 2


def test_null_cost_is_not_zero():
    b = parse(
        "deepswe-1-1",
        changed("deepswe-1-1", lambda d: d["rows"][0].update(mean_cost_usd=None)),
    )
    assert metric(b.rows[0], "mean_cost_usd").value is None
    assert metric(b.rows[0], "mean_cost_usd").missing_reason
    b = parse(
        "deepswe-1-1",
        changed("deepswe-1-1", lambda d: d["rows"][0].update(mean_cost_usd=0)),
    )
    assert metric(b.rows[0], "mean_cost_usd").value == 0


def test_structured_future_schema_rejected_after_reviewed_digest_change():
    from app.catalog.imports.adapters import parse_candidates
    from app.catalog.imports.registry import REGISTRY_HASH

    data = changed(
        "codereviewbench", lambda d: d["entries"][0].update(judge="new-judge")
    )
    spec = get_source("codereviewbench")
    spec["importContract"]["sha256"] = hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError):
        parse_candidates(
            "codereviewbench", data, spec, source_registry_hash=REGISTRY_HASH
        )
