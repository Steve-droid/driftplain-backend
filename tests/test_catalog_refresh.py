"""B16: real database orchestration with entirely fake upstream transport."""

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.catalog.imports.fetch import FetchResponse
from app.catalog.imports.pipeline import import_bytes, source_lock_key
from app.catalog.imports.refresh import (
    refresh_source,
    review_report,
    select_snapshot,
    source_health,
)
from app.models import CatalogImportState, CatalogSourceSnapshot
from tests.test_catalog_imports import NOW, payload


def test_structured_changed_unchanged_failure_and_recovery(migrated_engine):
    engine = migrated_engine
    raw = payload("testgeneval")
    first = refresh_source(
        engine,
        "testgeneval",
        fetcher=lambda *a, **k: FetchResponse(200, raw),
        checked_at=NOW,
    )
    assert first.status == "promoted"
    assert (
        refresh_source(
            engine,
            "testgeneval",
            fetcher=lambda *a, **k: FetchResponse(304),
            checked_at=NOW + timedelta(hours=6),
        ).status
        == "unchanged"
    )
    assert (
        refresh_source(
            engine,
            "testgeneval",
            fetcher=lambda *a, **k: FetchResponse(200, b"invalid"),
            checked_at=NOW + timedelta(hours=12),
        ).status
        == "failed"
    )
    with Session(engine) as db:
        h = source_health(db, "testgeneval", now=NOW + timedelta(hours=20))
        assert h["missed_check"] and h["failure_count"] == 1
        assert h["active_snapshot_id"] == first.snapshot_id
    assert (
        refresh_source(
            engine,
            "testgeneval",
            fetcher=lambda *a, **k: FetchResponse(200, raw),
            checked_at=NOW + timedelta(hours=21),
        ).status
        == "unchanged"
    )
    with Session(engine) as db:
        h = source_health(db, "testgeneval", now=NOW + timedelta(hours=21))
        assert not h["missed_check"] and h["failure_count"] == 0
        assert h["active_snapshot_age_seconds"] == 21 * 3600


def test_report_pending_is_not_promotion_and_requires_exact_review(migrated_engine):
    engine = migrated_engine
    old = import_bytes(engine, "mmlu-pro", payload(), checked_at=NOW)
    raw = b"<html>changed report</html>"
    digest = hashlib.sha256(raw).hexdigest()
    fetcher = lambda *a, **k: FetchResponse(200, raw, "report-v2")
    result = refresh_source(
        engine, "mmlu-pro", fetcher=fetcher, checked_at=NOW + timedelta(hours=6)
    )
    assert result.status == "pending_review" and result.snapshot_id == old.snapshot_id
    assert (
        refresh_source(
            engine,
            "mmlu-pro",
            fetcher=lambda *a, **k: FetchResponse(304),
            checked_at=NOW + timedelta(hours=12),
        ).status
        == "pending_review"
    )
    with pytest.raises(ValueError):
        review_report(
            engine, "mmlu-pro", "0" * 64, old.snapshot_id, "operator: inspected"
        )
    review_report(
        engine,
        "mmlu-pro",
        digest,
        old.snapshot_id,
        "operator: inspected; no score changes",
        checked_at=NOW + timedelta(hours=13),
    )
    assert (
        refresh_source(
            engine, "mmlu-pro", fetcher=fetcher, checked_at=NOW + timedelta(hours=18)
        ).status
        == "unchanged"
    )
    with Session(engine) as db:
        h = source_health(db, "mmlu-pro", now=NOW + timedelta(hours=18))
        assert not h["pending_review"] and h["active_snapshot_id"] == old.snapshot_id
        assert len(list(db.scalars(select(CatalogSourceSnapshot)))) == 1
        artifact = db.execute(
            text("SELECT content_hash, raw_bytes FROM catalog_report_artifact")
        ).one()
        assert artifact == (digest, raw)


def test_overlap_skips_without_fetch_or_freshness(migrated_engine):
    engine = migrated_engine
    with engine.connect() as connection:
        connection.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": source_lock_key("testgeneval")},
        )
        connection.commit()
        try:

            def forbidden(*a, **k):
                pytest.fail("overlap fetched source")

            assert (
                refresh_source(engine, "testgeneval", fetcher=forbidden).status
                == "overlap"
            )
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": source_lock_key("testgeneval")},
            )
            connection.commit()
    with Session(engine) as db:
        assert not db.scalar(select(CatalogImportState))


def test_snapshot_reversal_preserves_history_and_rejects_cross_source(migrated_engine):
    from tests.test_catalog_imports import mutate

    engine = migrated_engine
    first = import_bytes(engine, "mmlu-pro", payload(), checked_at=NOW)
    second = import_bytes(
        engine,
        "mmlu-pro",
        mutate(lambda doc: doc.update(coverage_note="Updated coverage")),
        checked_at=NOW + timedelta(hours=1),
    )
    select_snapshot(
        engine,
        "mmlu-pro",
        first.snapshot_id,
        "operator: reverse coverage change",
        checked_at=NOW + timedelta(hours=2),
    )
    with Session(engine) as db:
        state = db.scalar(select(CatalogImportState))
        assert state.active_snapshot_id == first.snapshot_id
        assert state.last_successful_check_at == NOW + timedelta(hours=1)
        assert len(list(db.scalars(select(CatalogSourceSnapshot)))) == 2
        assert db.scalar(text("SELECT count(*) FROM catalog_operator_action")) == 1
    with pytest.raises(ValueError):
        select_snapshot(
            engine, "testgeneval", second.snapshot_id, "operator: invalid source"
        )
    select_snapshot(
        engine, "mmlu-pro", second.snapshot_id, "operator: restore selection"
    )


def test_never_checked_health_is_unknown_and_missed(migrated_engine):
    with Session(migrated_engine) as db:
        h = source_health(db, "gpqa-diamond", now=NOW)
        assert h["missed_check"]
        assert h["last_successful_check_at"] is None
        assert h["upstream_content_age_seconds"] is None
        assert h["active_snapshot_age_seconds"] is None


def test_valid_changed_structured_feed_promotes_new_snapshot(migrated_engine):
    raw = payload("testgeneval")
    first = refresh_source(
        migrated_engine,
        "testgeneval",
        fetcher=lambda *a, **k: FetchResponse(200, raw),
        checked_at=NOW,
    )
    changed = raw.replace(b",5.2,22.9,", b",5.3,22.9,")
    assert changed != raw
    second = refresh_source(
        migrated_engine,
        "testgeneval",
        fetcher=lambda *a, **k: FetchResponse(200, changed),
        checked_at=NOW + timedelta(hours=6),
    )
    assert second.status == "promoted" and second.snapshot_id != first.snapshot_id
    with Session(migrated_engine) as db:
        assert (
            db.scalar(select(CatalogImportState)).active_snapshot_id
            == second.snapshot_id
        )
        assert len(list(db.scalars(select(CatalogSourceSnapshot)))) == 2


def test_failed_report_retains_pending_bytes_and_does_not_block_other_source(
    migrated_engine,
):
    def acquire(*args, **kwargs):
        assert "#" not in args[0]
        assert kwargs["allowed_urls"] == (args[0],)
        assert kwargs.get("expected_hash") is None
        return FetchResponse(200, b"new report", "etag-report")

    assert (
        refresh_source(
            migrated_engine, "mmlu-pro", fetcher=acquire, checked_at=NOW
        ).status
        == "pending_review"
    )

    def fail(*a, **kw):
        assert kw["etag"] == "etag-report"
        raise OSError("arbitrary upstream text must not be emitted")

    assert (
        refresh_source(
            migrated_engine,
            "mmlu-pro",
            fetcher=fail,
            checked_at=NOW + timedelta(hours=6),
        ).status
        == "failed"
    )
    with Session(migrated_engine) as db:
        h = source_health(db, "mmlu-pro", now=NOW + timedelta(hours=6))
        assert h["pending_review"] and h["failure_count"] == 1
        assert h["last_successful_check_at"] == NOW
        assert h["active_snapshot_id"] is None
    assert (
        refresh_source(
            migrated_engine,
            "testgeneval",
            fetcher=lambda *a, **k: FetchResponse(200, payload("testgeneval")),
            checked_at=NOW,
        ).status
        == "promoted"
    )


def test_report_first_304_fails_closed(migrated_engine):
    assert (
        refresh_source(
            migrated_engine,
            "mmlu-pro",
            fetcher=lambda *a, **k: FetchResponse(304),
            checked_at=NOW,
        ).status
        == "failed"
    )


def test_new_manifest_invalidates_report_attestation(migrated_engine):
    from tests.test_catalog_imports import mutate

    raw = b"checked report"
    digest = hashlib.sha256(raw).hexdigest()
    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    refresh_source(
        migrated_engine,
        "mmlu-pro",
        fetcher=lambda *a, **k: FetchResponse(200, raw),
        checked_at=NOW,
    )
    review_report(
        migrated_engine,
        "mmlu-pro",
        digest,
        first.snapshot_id,
        "Steve: inspected exact report",
        checked_at=NOW,
    )
    import_bytes(
        migrated_engine,
        "mmlu-pro",
        mutate(lambda doc: doc.update(coverage_note="new review")),
        checked_at=NOW,
    )
    with Session(migrated_engine) as db:
        assert source_health(db, "mmlu-pro", now=NOW)["pending_review"]


def test_refresh_audit_bytes_and_populated_downgrade_are_protected(migrated_engine):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import DatabaseError

    raw = b"checked report"
    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    refresh_source(
        migrated_engine,
        "mmlu-pro",
        fetcher=lambda *a, **k: FetchResponse(200, raw),
        checked_at=NOW,
    )
    review_report(
        migrated_engine,
        "mmlu-pro",
        hashlib.sha256(raw).hexdigest(),
        first.snapshot_id,
        "Steve: exact review",
        checked_at=NOW,
    )
    with Session(migrated_engine) as db:
        for statement in [
            "DELETE FROM catalog_report_artifact",
            "UPDATE catalog_operator_action SET reason = 'rewritten'",
        ]:
            with pytest.raises(DatabaseError):
                db.execute(text(statement))
            db.rollback()
    with pytest.raises(DatabaseError, match="Cannot discard refresh history"):
        command.downgrade(Config("alembic.ini"), "a0b1c2d3e4f5")
    with Session(migrated_engine) as db:
        assert (
            db.scalar(text("SELECT version_num FROM alembic_version")) == "b16c0a7a0001"
        )
        assert db.scalar(text("SELECT count(*) FROM catalog_operator_action")) == 1


def test_upgrade_preserves_populated_catalog_and_empty_downgrade(migrated_engine):
    from alembic import command
    from alembic.config import Config

    first = import_bytes(migrated_engine, "mmlu-pro", payload(), checked_at=NOW)
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "a0b1c2d3e4f5")
    command.upgrade(cfg, "head")
    with Session(migrated_engine) as db:
        assert (
            db.scalar(select(CatalogImportState)).active_snapshot_id
            == first.snapshot_id
        )
        assert (
            db.get(CatalogSourceSnapshot, first.snapshot_id).content_hash
            == hashlib.sha256(payload()).hexdigest()
        )


def test_metrics_are_durable_opt_in_and_do_not_invent_unknown_ages(
    client, migrated_engine, monkeypatch
):
    from app.config import get_settings

    assert "modelmatch_catalog_" not in client.get("/metrics").text
    monkeypatch.setenv("CATALOG_REFRESH_METRICS_ENABLED", "true")
    get_settings.cache_clear()
    try:
        text_before = client.get("/metrics").text
        assert "modelmatch_catalog_health_available 1.0" in text_before
        assert 'modelmatch_catalog_missed_check{source="mmlu-pro"} 1.0' in text_before
        assert (
            'modelmatch_catalog_upstream_content_age_seconds{source="mmlu-pro"}'
            not in text_before
        )
        refresh_source(
            migrated_engine,
            "mmlu-pro",
            fetcher=lambda *a, **k: FetchResponse(200, b"report"),
        )
        result = client.get("/metrics").text
        assert 'modelmatch_catalog_pending_review{source="mmlu-pro"} 1.0' in result
        assert 'modelmatch_catalog_missed_check{source="mmlu-pro"} 0.0' in result
        assert "report_content_hash" not in result
    finally:
        get_settings.cache_clear()


def test_metric_database_failure_is_not_healthy():
    from app.catalog.imports.metrics import render_catalog_metrics
    from sqlalchemy.exc import OperationalError

    class Broken:
        def execute(self, *args):
            raise OperationalError("do not disclose", {}, Exception("private detail"))

        def rollback(self):
            pass

    result = render_catalog_metrics(Broken()).decode()
    assert "modelmatch_catalog_health_available 0.0" in result
    assert "private detail" not in result
    assert "modelmatch_catalog_missed_check" not in result


def test_scheduler_cli_is_read_only_by_default_and_sanitizes_failure(
    monkeypatch, capsys
):
    from app.catalog.imports import scheduled

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert scheduled.main(["--source", "mmlu-pro"]) == 1
    assert "catalog_operation_failed" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        scheduled.main(["--source", "mmlu-pro", "--select-snapshot", "1"])


def test_local_manifest_cannot_mask_missed_or_failed_upstream_checks(migrated_engine):
    engine = migrated_engine
    import_bytes(engine, "mmlu-pro", payload(), checked_at=NOW)
    with Session(engine) as db:
        assert source_health(db, "mmlu-pro", now=NOW)["missed_check"]
    refresh_source(
        engine,
        "mmlu-pro",
        fetcher=lambda *a, **k: FetchResponse(200, b"report"),
        checked_at=NOW,
    )
    refresh_source(
        engine,
        "mmlu-pro",
        fetcher=lambda *a, **k: FetchResponse(500),
        checked_at=NOW + timedelta(hours=6),
    )
    import_bytes(engine, "mmlu-pro", payload(), checked_at=NOW + timedelta(hours=20))
    with Session(engine) as db:
        h = source_health(db, "mmlu-pro", now=NOW + timedelta(hours=20))
        assert h["missed_check"] and h["last_successful_check_at"] == NOW
        assert h["failure_count"] == 1
