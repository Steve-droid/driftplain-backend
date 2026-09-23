"""B3 contracts: reviewed source meaning, fail-closed adapters, atomic promotion."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DatabaseError

from app.catalog.imports.adapters import parse
from app.catalog.imports.registry import source_ids, get_source
from app.catalog.imports.pipeline import import_bytes
from app.catalog.imports.retention import retention_candidates
from app.models import (
    CatalogObservation,
    CatalogSourceSnapshot,
    CatalogModelAlias,
    CatalogProviderDeployment,
    CatalogImportState,
)

DATA = Path(__file__).resolve().parents[1] / "data/catalog/b3"
NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def payload(source="mmlu-pro"):
    return (DATA / f"{source}.json").read_bytes()


def mutate(change, source="mmlu-pro"):
    document = json.loads(payload(source))
    change(document)
    return json.dumps(document).encode()


def test_registry_is_exact_b1_subset():
    assert len(source_ids()) == 10
    assert get_source("swe-bench-verified")["maxPayloadBytes"] == 8000000
    assert get_source("mrcr-v2")["expectedCoverage"]["verifiedAtAudit"] is False
    with pytest.raises(ValueError):
        get_source("deepswe-1-1")


@pytest.mark.parametrize(
    "source",
    [
        "gpqa-diamond",
        "hle",
        "mmlu-pro",
        "aime-2025",
        "frontiermath-tier4-v2",
        "arc-agi-2",
        "terminal-bench-4",
        "mrcr-v2",
        "osworld-verified",
    ],
)
def test_reviewed_family_contract(source):
    raw = payload(source)
    batch = parse(source, raw)
    assert batch.content_hash == hashlib.sha256(raw).hexdigest()
    assert batch.source_id == source
    assert len({r.model_label for r in batch.rows}) >= (
        0 if source == "mrcr-v2" else 2 if source == "aime-2025" else 3
    )
    for row in batch.rows:
        assert row.citation and row.protocol and row.evaluator


def test_mmlu_report_meaning():
    batch = parse("mmlu-pro", payload())
    sonnet = next(r for r in batch.rows if r.model_label == "Claude-3.5-Sonnet")
    assert sonnet.metrics[0].value == Decimal("76.12")
    assert sonnet.protocol["shots"] == 5
    assert sonnet.protocol["prompt"]
    assert sonnet.reviewed_mapping is None


def test_aime_effort_does_not_inflate_model_coverage():
    batch = parse("aime-2025", payload("aime-2025"))
    assert {r.model_label for r in batch.rows} == {"gpt-oss-120b", "gpt-oss-20b"}
    high = next(
        r
        for r in batch.rows
        if r.model_label == "gpt-oss-120b"
        and r.protocol["effort"] == "high"
        and r.protocol["tools"] is False
    )
    assert high.metrics[0].value == Decimal("92.5")
    assert "two" in batch.coverage_note.lower()


def test_mrcr_launch_has_definition_without_invented_scores():
    batch = parse("mrcr-v2", payload("mrcr-v2"))
    assert not batch.rows
    assert batch.coverage_note


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["rows"][0]["protocol"].pop("shots"),
        lambda d: d["rows"][0]["metrics"][0].update(value=101),
        lambda d: d["rows"][0]["metrics"][0].update(value=True),
        lambda d: d["rows"].append(d["rows"][0]),
        lambda d: d["review"].pop("reviewer"),
        lambda d: d.update(schema_version=2),
        lambda d: d.update(source_id="hle"),
        lambda d: d.update(rows=[]),
    ],
)
def test_invalid_batch_is_rejected(change):
    with pytest.raises(ValueError):
        parse("mmlu-pro", mutate(change))


def test_missing_score_is_null_with_reason():
    raw = mutate(
        lambda d: d["rows"][0]["metrics"][0].update(
            value=None, missing_reason="not reported"
        )
    )
    assert parse("mmlu-pro", raw).rows[0].metrics[0].value is None
    with pytest.raises(ValueError):
        parse(
            "mmlu-pro", mutate(lambda d: d["rows"][0]["metrics"][0].update(value=None))
        )


def test_size_and_duplicate_json_keys_fail_closed():
    with pytest.raises(ValueError):
        parse("mmlu-pro", b" " * 250001)
    with pytest.raises(ValueError):
        parse("mmlu-pro", b'{"schema_version":1,"schema_version":1}')


def test_promotion_idempotency_history_api_and_runtime_independence(
    migrated_engine, db_session, client
):
    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    assert first.status == "promoted"
    same = import_bytes(
        migrated_engine, "mmlu-pro", payload(), checked_at=NOW + timedelta(hours=6)
    )
    assert same.status == "unchanged" and same.snapshot_id == first.snapshot_id
    changed = mutate(lambda d: d["rows"][0]["metrics"][0].update(value=76.13))
    second = import_bytes(
        migrated_engine, "mmlu-pro", changed, checked_at=NOW + timedelta(days=1)
    )
    assert second.snapshot_id != first.snapshot_id
    assert (
        db_session.scalar(select(func.count()).select_from(CatalogSourceSnapshot)) == 2
    )
    assert (
        db_session.scalar(select(func.count()).select_from(CatalogProviderDeployment))
        == 0
    )
    assert set(db_session.scalars(select(CatalogModelAlias.resolution_status))) == {
        "unresolved"
    }
    state = db_session.scalar(select(CatalogImportState))
    assert state.active_snapshot_id == second.snapshot_id
    rows = client.get(
        "/catalog/v1/observations", params={"snapshotId": first.snapshot_id}
    ).json()["items"]
    assert (
        rows and rows[0]["sourceContentHash"] == hashlib.sha256(payload()).hexdigest()
    )
    assert client.get("/catalog/v1/observations", params={"q": "Claude-3.5"}).json()[
        "items"
    ]
    with pytest.raises(DatabaseError):
        db_session.execute(
            text("DELETE FROM catalog_source_snapshot WHERE id = :id"),
            {"id": first.snapshot_id},
        )
    db_session.rollback()


def test_invalid_changed_batch_keeps_last_good(migrated_engine, db_session):
    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    bad = mutate(lambda d: d["rows"][-1]["protocol"].pop("prompt"))
    failed = import_bytes(
        migrated_engine, "mmlu-pro", bad, checked_at=NOW + timedelta(hours=6)
    )
    assert failed.status == "failed"
    state = db_session.scalar(select(CatalogImportState))
    assert state.active_snapshot_id == first.snapshot_id
    assert state.failure_count == 1 and state.last_successful_check_at == NOW
    assert (
        db_session.scalar(select(func.count()).select_from(CatalogSourceSnapshot)) == 1
    )


def test_retention_only_flags_superseded_b3_snapshots_after_365_days(
    migrated_engine, db_session
):
    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    change = mutate(lambda d: d["rows"][0]["metrics"][0].update(value=76.13))
    second = import_bytes(
        migrated_engine, "mmlu-pro", change, checked_at=NOW + timedelta(days=10)
    )
    assert retention_candidates(db_session, now=NOW + timedelta(days=374)) == []
    assert retention_candidates(db_session, now=NOW + timedelta(days=375)) == [
        first.snapshot_id
    ]
    assert db_session.get(CatalogSourceSnapshot, second.snapshot_id) is not None
    assert db_session.get(CatalogSourceSnapshot, first.snapshot_id) is not None


def test_pinned_swe_fixture_semantics_and_duplicate_folders():
    raw = (
        Path(__file__).parent / "fixtures/catalog/swe-bench-verified.json"
    ).read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest()
        == get_source("swe-bench-verified")["artifactSha256"]
    )
    batch = parse("swe-bench-verified", raw)
    assert len(batch.rows) == 180
    sonar = next(
        r
        for r in batch.rows
        if r.locator == "20251205_sonar-foundation-agent_claude-opus-4-5"
    )
    assert sonar.model_label == "Claude 4.5 Opus"
    assert sonar.metrics[0].value == Decimal("79.2")
    assert sonar.protocol["attempts"] == 1
    assert sonar.protocol["verification"] == "unchecked"
    assert sonar.metrics[0].denominator == 500
    doc = json.loads(raw)
    verified = next(b for b in doc["leaderboards"] if b["name"] == "Verified")
    verified["results"].append(verified["results"][0])
    with pytest.raises(ValueError, match="folder"):
        parse("swe-bench-verified", json.dumps(doc).encode())


def test_hle_metrics_and_protocol_separation():
    batch = parse("hle", payload("hle"))
    gemini = next(r for r in batch.rows if r.model_label == "Gemini 3 Pro")
    assert [(m.key, m.value, m.direction) for m in gemini.metrics] == [
        ("accuracy", Decimal("38.3"), "higher"),
        ("calibration_error", Decimal("57.2"), "lower"),
    ]
    assert gemini.protocol["judge"] == "o3-mini"
    text_only = next(r for r in batch.rows if r.model_label == "DeepSeek-R1*")
    assert text_only.protocol["modality"] == "text-only subset"


def test_terminal_preserves_attempts_and_reported_interval():
    batch = parse("terminal-bench-4", payload("terminal-bench-4"))
    sol = next(r for r in batch.rows if r.model_label == "GPT-5.6 Sol")
    assert sol.protocol["model"] == "openai/gpt-5.6-sol"
    assert sol.protocol["attempts"] == 5
    assert sol.metrics[0].value == Decimal("37.27")
    assert sol.metrics[0].confidence_low == Decimal("33.49")
    assert sol.metrics[0].confidence_high == Decimal("41.05")
    assert sol.metrics[0].confidence_level == Decimal(".95")
    assert sol.metrics[0].denominator == 330


def test_gpqa_diamond_paper_values_and_arc_zero():
    gpqa = parse("gpqa-diamond", payload("gpqa-diamond"))
    assert {r.model_label: r.metrics[0].value for r in gpqa.rows} == {
        "Llama-2-70B-chat": Decimal("28.1"),
        "GPT-3.5-turbo-16k": Decimal("29.6"),
        "GPT-4": Decimal("38.8"),
    }
    arc = parse("arc-agi-2", payload("arc-agi-2"))
    assert (
        arc.rows[0].metrics[0].value == 0
        and arc.rows[0].metrics[0].missing_reason is None
    )
    assert arc.rows[0].protocol["set"] == "semi-private"


def test_all_ten_imports_are_catalog_only(migrated_engine, db_session):
    from app.models import (
        AgentRuntimeConfig,
        CatalogBenchmarkFamily,
        CatalogModel,
        CatalogSourcePayload,
    )

    before = db_session.execute(
        select(AgentRuntimeConfig.id, AgentRuntimeConfig.enabled)
    ).all()
    db_session.rollback()
    for source in source_ids():
        raw = (
            (
                Path(__file__).parent / "fixtures/catalog/swe-bench-verified.json"
            ).read_bytes()
            if source == "swe-bench-verified"
            else payload(source)
        )
        result = import_bytes(migrated_engine, source, raw, checked_at=NOW)
        assert result.status == "promoted", (source, result)
        stored = db_session.get(CatalogSourcePayload, result.snapshot_id)
        if source == "swe-bench-verified":
            assert stored is None  # exact pinned immutable upstream artifact
        else:
            assert stored.raw_bytes == raw
    assert (
        db_session.scalar(select(func.count()).select_from(CatalogBenchmarkFamily))
        == 10
    )
    assert (
        db_session.scalar(select(func.count()).select_from(CatalogProviderDeployment))
        == 0
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(CatalogModel)
            .where(CatalogModel.legacy_model_id.is_(None))
        )
        == 0
    )
    assert (
        db_session.execute(
            select(AgentRuntimeConfig.id, AgentRuntimeConfig.enabled)
        ).all()
        == before
    )


def test_late_database_failure_rolls_back_every_candidate_write(
    migrated_engine, db_session
):
    from app.models import CatalogBenchmarkFamily, CatalogProtocol, CatalogSourcePayload

    db_session.execute(
        text("""CREATE FUNCTION b3_test_failure() RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'test persistence failure'; END; $$ LANGUAGE plpgsql""")
    )
    db_session.execute(
        text(
            "CREATE TRIGGER b3_test_failure BEFORE INSERT ON catalog_observation_metric FOR EACH ROW EXECUTE FUNCTION b3_test_failure()"
        )
    )
    db_session.commit()
    failed = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    assert failed.status == "failed"
    for model in (
        CatalogBenchmarkFamily,
        CatalogProtocol,
        CatalogSourceSnapshot,
        CatalogObservation,
        CatalogModelAlias,
        CatalogSourcePayload,
    ):
        assert db_session.scalar(select(func.count()).select_from(model)) == 0
    assert db_session.scalar(select(CatalogImportState)).active_snapshot_id is None


def test_conflicting_dimension_rejects_whole_batch(migrated_engine, db_session):
    from app.models import CatalogMetricDefinition

    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    db_session.scalar(select(CatalogMetricDefinition)).direction = "lower"
    db_session.commit()
    changed = mutate(lambda d: d["rows"][-1]["metrics"][0].update(value=70))
    assert (
        import_bytes(
            migrated_engine, "mmlu-pro", changed, checked_at=NOW + timedelta(days=1)
        ).status
        == "failed"
    )
    assert (
        db_session.scalar(select(func.count()).select_from(CatalogSourceSnapshot)) == 1
    )
    assert (
        db_session.scalar(select(CatalogImportState)).active_snapshot_id
        == first.snapshot_id
    )


def test_concurrent_same_source_promotes_once(migrated_engine, db_session):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: import_bytes(
                    migrated_engine, "mmlu-pro", payload(), checked_at=NOW
                ),
                range(2),
            )
        )
    assert sorted(r.status for r in results) == ["promoted", "unchanged"]
    assert len({r.snapshot_id for r in results}) == 1
    assert db_session.scalar(select(func.count()).select_from(CatalogObservation)) == 3


def test_reviewed_alias_mapping_and_conflict_preserve_history(
    migrated_engine, db_session
):
    from app.models import CatalogModel

    db_session.add_all(
        [
            CatalogModel(slug="reviewed-sonnet", name="Reviewed Sonnet"),
            CatalogModel(slug="other", name="Other"),
        ]
    )
    db_session.commit()

    def mapping(doc):
        doc["rows"][0]["reviewed_mapping"] = {
            "model_slug": "reviewed-sonnet",
            "review": doc["review"],
            "citation": doc["rows"][0]["citation"],
        }

    raw = mutate(mapping)
    first = import_bytes(migrated_engine, "mmlu-pro", raw, checked_at=NOW)
    assert first.status == "promoted"
    alias = db_session.scalar(
        select(CatalogModelAlias).where(
            CatalogModelAlias.source_label == "Claude-3.5-Sonnet"
        )
    )
    assert alias.resolution_status == "resolved"
    doc = json.loads(raw)
    doc["rows"][0]["reviewed_mapping"]["model_slug"] = "other"
    assert (
        import_bytes(
            migrated_engine,
            "mmlu-pro",
            json.dumps(doc).encode(),
            checked_at=NOW + timedelta(days=1),
        ).status
        == "failed"
    )
    assert (
        db_session.scalar(select(func.count()).select_from(CatalogSourceSnapshot)) == 1
    )


def test_unchanged_check_preserves_dates_and_failed_check_recovers(
    migrated_engine, db_session
):
    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    import_bytes(
        migrated_engine, "mmlu-pro", b"{}", checked_at=NOW + timedelta(hours=6)
    )
    assert (
        import_bytes(
            migrated_engine, "mmlu-pro", payload(), checked_at=NOW + timedelta(hours=12)
        ).status
        == "unchanged"
    )
    snapshot = db_session.get(CatalogSourceSnapshot, first.snapshot_id)
    assert snapshot.fetched_at == NOW and snapshot.publication_date is None
    state = db_session.scalar(select(CatalogImportState))
    assert state.last_promoted_at == NOW and state.failure_count == 0
    assert state.last_successful_check_at == NOW + timedelta(hours=12)


def test_retention_holds_and_legacy_are_protected(migrated_engine, db_session):
    from app.models import CatalogSnapshotLifecycle

    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    import_bytes(
        migrated_engine,
        "mmlu-pro",
        mutate(lambda d: d["rows"][0]["metrics"][0].update(value=77)),
        checked_at=NOW + timedelta(days=1),
    )
    db_session.get(
        CatalogSnapshotLifecycle, first.snapshot_id
    ).hold_reason = "selection evidence: future B6 reference"
    db_session.commit()
    assert retention_candidates(db_session, now=NOW + timedelta(days=1000)) == []


def test_source_label_search_cursor_is_bound_and_import_internals_are_private(
    migrated_engine, client
):
    import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    import_bytes(
        migrated_engine,
        "mmlu-pro",
        mutate(lambda d: d["rows"][0]["metrics"][0].update(value=77)),
        checked_at=NOW + timedelta(days=1),
    )
    response = client.get(
        "/catalog/v1/observations", params={"q": "Claude", "limit": 1}
    )
    page = response.json()
    assert response.status_code == 200 and len(page["items"]) == 1
    cursor = page["pageInfo"]["nextCursor"]
    assert (
        client.get(
            "/catalog/v1/observations", params={"q": "Gemini", "cursor": cursor}
        ).status_code
        == 422
    )
    assert client.get(
        "/catalog/v1/observations", params={"q": "Claude", "cursor": cursor}
    ).json()["items"]
    assert (
        not {"rawBytes", "failureCode", "etag", "holdReason"} & page["items"][0].keys()
    )
    assert client.get("/catalog/v1/import-state").status_code == 404


@pytest.mark.parametrize(
    "source,key,value",
    [
        ("mmlu-pro", "shots", -1),
        ("mmlu-pro", "shots", True),
        ("aime-2025", "tools", "yes"),
        ("aime-2025", "samples", 0),
        ("osworld-verified", "max_steps", -1),
        ("osworld-verified", "modality", "guessed"),
        ("terminal-bench-4", "attempts", 0),
        ("arc-agi-2", "system_category", "guessed"),
        ("frontiermath-tier4-v2", "tier", "4"),
        ("hle", "modality", "guessed"),
    ],
)
def test_protocol_types_and_ranges_are_not_guessed(source, key, value):
    with pytest.raises(ValueError):
        parse(
            source,
            mutate(lambda d: d["rows"][0]["protocol"].update({key: value}), source),
        )


def test_swe_lower_bound_attempts_and_unknown_verification_are_honest():
    raw = (
        Path(__file__).parent / "fixtures/catalog/swe-bench-verified.json"
    ).read_bytes()
    batch = parse("swe-bench-verified", raw)
    lower_bound = next(
        r for r in batch.rows if r.locator == "20250928_trae_doubao_seed_code"
    )
    assert (
        lower_bound.protocol["attempts"] == "2+"
        and lower_bound.metrics[0].attempts is None
    )
    unknown = next(
        r for r in batch.rows if r.locator == "20260217_mini-v2.0.0_gpt-5-mini"
    )
    assert unknown.protocol["verification"]["unknown_reason"]
    unchecked = next(
        r
        for r in batch.rows
        if r.locator == "20251120_livesweagent_gemini-3-pro-preview"
    )
    assert unchecked.protocol["verification"] == "unchecked"


def test_fetch_promote_304_and_report_mode(migrated_engine, db_session):
    from app.catalog.imports.pipeline import import_source
    from app.catalog.imports.fetch import FetchResponse

    raw = (
        Path(__file__).parent / "fixtures/catalog/swe-bench-verified.json"
    ).read_bytes()
    calls = []

    def fetcher(url, **policy):
        calls.append(policy)
        return (
            FetchResponse(200, raw, etag='"b1"')
            if len(calls) == 1
            else FetchResponse(304, etag='"b1"')
        )

    first = import_source(
        migrated_engine, "swe-bench-verified", fetcher=fetcher, checked_at=NOW
    )
    assert first.status == "promoted"
    assert (
        import_source(
            migrated_engine,
            "swe-bench-verified",
            fetcher=fetcher,
            checked_at=NOW + timedelta(hours=6),
        ).status
        == "unchanged"
    )
    assert calls[1]["etag"] == '"b1"'
    assert (
        db_session.scalar(select(func.count()).select_from(CatalogSourceSnapshot)) == 1
    )
    with pytest.raises(ValueError, match="reviewed"):
        import_source(migrated_engine, "mmlu-pro", fetcher=fetcher)


def test_distinct_reports_and_unreported_settings_do_not_merge(
    migrated_engine, db_session
):
    from app.models import CatalogProtocol

    first = import_bytes(
        migrated_engine, "aime-2025", payload("aime-2025"), checked_at=NOW
    )
    # Unknown sample counts mean even similar settings cannot establish comparability.
    protocols = list(db_session.scalars(select(CatalogProtocol)))
    assert len(protocols) == 12
    assert all(p.configuration.get("unreported_settings_scope") for p in protocols)
    changed = mutate(
        lambda d: d["rows"][0].update(evaluator="Second cited evaluator"), "aime-2025"
    )
    assert (
        import_bytes(
            migrated_engine, "aime-2025", changed, checked_at=NOW + timedelta(days=1)
        ).status
        == "promoted"
    )
    assert db_session.scalar(select(func.count()).select_from(CatalogObservation)) == 24
    assert db_session.get(CatalogSourceSnapshot, first.snapshot_id)


def test_validation_command_has_no_database_side_effects(capsys):
    from app.catalog.imports.__main__ import main

    assert (
        main(["--source", "mmlu-pro", "--manifest", str(DATA / "mmlu-pro.json")]) == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "validated" and output["observations"] == 3


def test_payload_insert_must_match_snapshot_digest_and_size(
    migrated_engine, db_session
):
    from app.models import CatalogSourcePayload

    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    source_id = db_session.get(CatalogSourceSnapshot, first.snapshot_id).source_id
    bad = CatalogSourceSnapshot(
        source_id=source_id, content_hash="0" * 64, byte_count=3
    )
    db_session.add(bad)
    db_session.flush()
    db_session.add(CatalogSourcePayload(snapshot_id=bad.id, raw_bytes=b"{}"))
    with pytest.raises(DatabaseError):
        db_session.flush()
    db_session.rollback()


def test_active_pointer_cannot_reference_another_source(migrated_engine, db_session):
    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    second = import_bytes(migrated_engine, "hle", payload("hle"), checked_at=NOW)
    state = db_session.scalar(
        select(CatalogImportState).where(
            CatalogImportState.active_snapshot_id == first.snapshot_id
        )
    )
    state.active_snapshot_id = second.snapshot_id
    with pytest.raises(DatabaseError):
        db_session.flush()
    db_session.rollback()


@pytest.mark.parametrize("bad_date", [None, 20260923, {}, "2026-02-30"])
def test_malformed_swe_date_fails_as_validation(bad_date):
    raw = (
        Path(__file__).parent / "fixtures/catalog/swe-bench-verified.json"
    ).read_bytes()
    document = json.loads(raw)
    next(b for b in document["leaderboards"] if b["name"] == "Verified")["results"][0][
        "date"
    ] = bad_date
    with pytest.raises(ValueError):
        parse("swe-bench-verified", json.dumps(document).encode())


def test_source_metadata_conflict_records_failure_without_losing_last_good(
    migrated_engine, db_session
):
    from app.models import CatalogSource

    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    source = db_session.scalar(select(CatalogSource))
    source.name = "Previously reviewed source metadata"
    db_session.commit()
    result = import_bytes(
        migrated_engine, "mmlu-pro", payload(), checked_at=NOW + timedelta(days=1)
    )
    assert result.status == "failed" and result.snapshot_id == first.snapshot_id
    db_session.expire_all()
    state = db_session.scalar(select(CatalogImportState))
    assert state.failure_count == 1 and state.last_successful_check_at == NOW
    assert source.name == "Previously reviewed source metadata"


def test_default_check_time_is_taken_after_acquiring_source_lock(
    migrated_engine, monkeypatch
):
    from sqlalchemy import event
    from app.catalog.imports import pipeline

    lock_acquired = []

    def record_lock(connection, cursor, statement, parameters, context, executemany):
        if "SELECT pg_advisory_lock(" in statement:
            lock_acquired.append(True)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert lock_acquired, "A queued import must timestamp after taking the lock"
            return NOW

    event.listen(migrated_engine, "after_cursor_execute", record_lock)
    monkeypatch.setattr(pipeline, "datetime", Clock)
    try:
        assert import_bytes(migrated_engine, "mmlu-pro", payload()).status == "promoted"
    finally:
        event.remove(migrated_engine, "after_cursor_execute", record_lock)


def test_command_sanitizes_database_connection_failure(monkeypatch, capsys):
    from sqlalchemy.exc import OperationalError
    import sqlalchemy
    from app.catalog.imports.__main__ import main

    def unavailable_engine(*args, **kwargs):
        raise OperationalError("private-connection-details", None, Exception("offline"))

    monkeypatch.setattr(sqlalchemy, "create_engine", unavailable_engine)
    assert (
        main(
            [
                "--source",
                "mmlu-pro",
                "--manifest",
                str(DATA / "mmlu-pro.json"),
                "--promote",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "private-connection-details" not in output and "offline" not in output
    assert json.loads(output)["failureCode"] == "database_unavailable"


def test_historical_replay_is_a_noop_and_does_not_extend_retention(
    migrated_engine, db_session
):
    from app.models import CatalogSnapshotLifecycle

    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    changed = mutate(lambda d: d["rows"][0]["metrics"][0].update(value=76.13))
    second = import_bytes(
        migrated_engine, "mmlu-pro", changed, checked_at=NOW + timedelta(days=1)
    )
    replay = import_bytes(
        migrated_engine, "mmlu-pro", payload(), checked_at=NOW + timedelta(days=2)
    )
    assert replay.status == "unchanged"
    state = db_session.scalar(select(CatalogImportState))
    assert state.active_snapshot_id == second.snapshot_id
    assert state.last_promoted_at == NOW + timedelta(days=1)
    assert db_session.get(
        CatalogSnapshotLifecycle, first.snapshot_id
    ).superseded_at == NOW + timedelta(days=1)
    assert (
        db_session.scalar(select(func.count()).select_from(CatalogSourceSnapshot)) == 2
    )


@pytest.mark.parametrize("source_id", source_ids())
def test_adapter_core_uses_supplied_metadata_without_file_access(
    source_id, monkeypatch
):
    from app.catalog.imports.adapters import parse_candidates
    from app.catalog.imports.registry import REGISTRY_HASH

    spec = get_source(source_id)
    raw = (
        (
            Path(__file__).parent / "fixtures/catalog/swe-bench-verified.json"
        ).read_bytes()
        if source_id == "swe-bench-verified"
        else payload(source_id)
    )

    def no_files(*args, **kwargs):
        pytest.fail("Adapter attempted file access")

    monkeypatch.setattr(Path, "read_bytes", no_files)
    batch = parse_candidates(source_id, raw, spec, source_registry_hash=REGISTRY_HASH)
    assert (
        batch.source_id == source_id
        and batch.content_hash == hashlib.sha256(raw).hexdigest()
    )


def test_local_structured_import_cannot_misattribute_changed_bytes_to_pinned_artifact(
    migrated_engine, db_session
):
    raw = (
        Path(__file__).parent / "fixtures/catalog/swe-bench-verified.json"
    ).read_bytes()
    first = import_bytes(migrated_engine, "swe-bench-verified", raw, checked_at=NOW)
    assert first.status == "promoted"
    # Valid JSON, but not the exact bytes at the immutable source URI.
    result = import_bytes(
        migrated_engine,
        "swe-bench-verified",
        raw + b"\n",
        checked_at=NOW + timedelta(days=1),
    )
    assert result.status == "failed" and result.snapshot_id == first.snapshot_id
    assert (
        db_session.scalar(select(func.count()).select_from(CatalogSourceSnapshot)) == 1
    )
