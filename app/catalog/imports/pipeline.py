"""Serialize each source, validate all candidates, atomically promote or retain last good."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.catalog.imports.adapters import parse_candidates
from app.catalog.imports.contracts import canonical, fingerprint
from app.catalog.imports.registry import REGISTRY_HASH, get_source
from app.models import (
    CatalogBenchmarkFamily as Family,
    CatalogBenchmarkVersion as Version,
    CatalogEvaluator as Evaluator,
    CatalogProtocol as Protocol,
    CatalogSource as Source,
    CatalogSourceSnapshot as Snapshot,
    CatalogImportState as State,
    CatalogSourcePayload as Payload,
    CatalogSnapshotLifecycle as Lifecycle,
    CatalogModel as Model,
    CatalogModelAlias as Alias,
    CatalogObservation as Observation,
    CatalogMetricDefinition as MetricDefinition,
    CatalogObservationMetric as ObservationMetric,
)


@dataclass(frozen=True)
class ImportResult:
    status: str
    snapshot_id: int | None = None
    failure_code: str | None = None


def ensure(db, model, identity, values):
    # Upsert dimensions safely even when distinct source imports share a dimension.
    db.execute(insert(model).values(**identity, **values).on_conflict_do_nothing())
    row = db.scalar(select(model).filter_by(**identity))
    if row is None or any(getattr(row, key) != value for key, value in values.items()):
        raise ValueError("conflicting catalog dimension")
    return row


def import_state(db, spec, *, validate_source=True):
    # Failure bookkeeping must survive a conflict with existing source metadata.
    source = (
        None
        if validate_source
        else db.scalar(select(Source).filter_by(slug=spec["id"]))
    )
    source = source or ensure(
        db,
        Source,
        {"slug": spec["id"]},
        {
            "name": spec["name"],
            "definition_url": spec["definitionUrl"],
            "result_url": spec["resultArtifact"],
            "license_text": spec["license"],
            "attribution": spec["attribution"],
            "access_notes": spec["access"],
        },
    )
    db.execute(insert(State).values(source_id=source.id).on_conflict_do_nothing())
    return source, db.get(State, source.id)


def _promote(db, spec, source, state, raw, now):
    batch = parse_candidates(spec["id"], raw, spec, source_registry_hash=REGISTRY_HASH)
    if batch.review and batch.review.reviewed_at > now.date():
        raise ValueError("future review date")
    if batch.publication_date and batch.publication_date > now.date():
        raise ValueError("future publication date")
    if any(r.publication_date and r.publication_date > now.date() for r in batch.rows):
        raise ValueError("future observation date")
    snapshot = db.scalar(
        select(Snapshot).filter_by(source_id=source.id, content_hash=batch.content_hash)
    )
    if snapshot:
        # Known historical content never silently rolls the active pointer backward.
        return ImportResult("unchanged", snapshot.id)
    family = ensure(
        db,
        Family,
        {"slug": spec["id"]},
        {
            "name": spec["name"],
            "methodology_url": spec["definitionUrl"],
            "limitations": spec["failureBehavior"],
        },
    )
    version = ensure(
        db,
        Version,
        {"benchmark_family_id": family.id, "version": spec["version"]},
        {"methodology": spec["runner"], "methodology_url": spec["definitionUrl"]},
    )
    contract = spec.get("importContract")
    immutable_upstream = (
        batch.content_hash == contract.get("sha256")
        if contract
        else spec["importMode"] == "automatic_structured"
        and batch.content_hash == spec.get("artifactSha256")
    )
    # A reviewed manifest is the accepted artifact; do not attribute its hash to its cited HTML.
    snapshot = Snapshot(
        source_id=source.id,
        content_hash=batch.content_hash,
        artifact_uri=(contract["url"] if contract else spec["resultArtifact"])
        if immutable_upstream
        else "urn:sha256:" + batch.content_hash,
        fetched_at=now,
        publication_date=batch.publication_date,
        content_type="text/csv" if spec["id"] == "testgeneval" else "application/json",
        byte_count=len(raw),
    )
    db.add(snapshot)
    db.flush()
    if not immutable_upstream:
        db.add(Payload(snapshot_id=snapshot.id, raw_bytes=raw))
    for row in batch.rows:
        mapping = row.reviewed_mapping
        alias = db.scalar(
            select(Alias).filter_by(source_id=source.id, source_label=row.model_label)
        )
        target = None
        if mapping:
            if mapping.review.reviewed_at > now.date():
                raise ValueError("future alias review")
            target = db.scalar(select(Model).filter_by(slug=mapping.model_slug))
            if target is None:
                raise ValueError("reviewed alias target does not exist")
            if (
                alias
                and alias.resolution_status == "resolved"
                and alias.catalog_model_id != target.id
            ):
                raise ValueError("conflicting reviewed alias target")
        if alias is None:
            alias = Alias(
                source_id=source.id,
                source_label=row.model_label,
                normalized_label=row.model_label.casefold().strip(),
                resolution_status="unresolved",
                reviewed_at=now,
                review_note="Importer retained exact source label; identity is unresolved.",
            )
            db.add(alias)
        if target:
            alias.resolution_status = "resolved"
            alias.catalog_model_id = target.id
            alias.reviewed_at = datetime.combine(
                mapping.review.reviewed_at, datetime.min.time(), tzinfo=UTC
            )
            alias.review_note = canonical(mapping)
        config = dict(row.protocol)
        if any(isinstance(v, dict) and "unknown_reason" in v for v in config.values()):
            config["unreported_settings_scope"] = row.citation.url + "#" + row.locator
        configuration_hash = fingerprint(config)
        protocol = ensure(
            db,
            Protocol,
            {
                "benchmark_version_id": version.id,
                "configuration_fingerprint": configuration_hash,
            },
            {
                "name": spec["name"] + " / " + configuration_hash[:12],
                "runner": spec["runner"],
                "configuration": config,
            },
        )
        evaluator = ensure(
            db, Evaluator, {"name": row.evaluator, "organization": None}, {}
        )
        observation = Observation(
            benchmark_family_id=family.id,
            benchmark_version_id=version.id,
            protocol_id=protocol.id,
            evaluator_id=evaluator.id,
            source_snapshot_id=snapshot.id,
            source_record_locator=row.locator,
            configuration_fingerprint=configuration_hash,
            record_fingerprint=fingerprint(row),
            source_model_label=row.model_label,
            catalog_model_id=alias.catalog_model_id
            if alias.resolution_status == "resolved"
            else None,
            origin="source",
            provenance_status="complete",
            source_url=row.citation.url,
            observed_at=row.publication_date,
            notes=canonical(
                {
                    "citation": row.citation.model_dump(mode="json"),
                    "review": batch.review.model_dump(mode="json")
                    if batch.review
                    else None,
                    "coverage": batch.coverage_note,
                    "source_data": row.source_data,
                }
            ),
        )
        db.add(observation)
        db.flush()
        for metric in row.metrics:
            definition = ensure(
                db,
                MetricDefinition,
                {
                    "benchmark_family_id": family.id,
                    "benchmark_version_id": version.id,
                    "key": metric.key,
                },
                {
                    "name": metric.key,
                    "unit": metric.unit,
                    "direction": metric.direction,
                    "minimum": 0,
                    "maximum": 100
                    if metric.unit == "percent"
                    else 1
                    if metric.unit == "ratio"
                    else None,
                },
            )
            db.add(
                ObservationMetric(
                    observation_id=observation.id,
                    metric_definition_id=definition.id,
                    value=metric.value,
                    reported_value=metric.reported_value,
                    missing_reason=metric.missing_reason,
                    confidence_low=metric.confidence_low,
                    confidence_high=metric.confidence_high,
                    confidence_level=metric.confidence_level,
                    uncertainty_type="confidence_interval"
                    if metric.confidence_level
                    else None,
                    denominator=metric.denominator,
                    attempts=metric.attempts,
                )
            )
    if state.active_snapshot_id:
        old_lifecycle = db.get(Lifecycle, state.active_snapshot_id)
        if old_lifecycle is None:
            raise ValueError("active snapshot lifecycle is missing")
        old_lifecycle.superseded_at = now
    db.add(Lifecycle(snapshot_id=snapshot.id, activated_at=now))
    state.active_snapshot_id = snapshot.id
    state.last_promoted_at = now
    state.reviewed_report_hash = None
    db.flush()
    return ImportResult("promoted", snapshot.id)


def source_lock_key(source_id):
    return int.from_bytes(
        hashlib.sha256(("catalog-import:" + source_id).encode()).digest()[:8],
        "big",
        signed=True,
    )


def _run(
    engine,
    source_id,
    acquire,
    *,
    checked_at=None,
    report=False,
    nonblocking=False,
    refresh=False,
):
    spec = get_source(source_id)
    if checked_at is not None and checked_at.tzinfo is None:
        raise ValueError("check time must be timezone-aware")
    lock = source_lock_key(source_id)
    with engine.connect() as connection:
        if nonblocking:
            acquired = connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": lock}
            )
        else:
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock})
            acquired = True
        connection.commit()
        if not acquired:
            return ImportResult("overlap")
        try:
            # A queued invocation's check starts only once it owns the source lock.
            now = checked_at or datetime.now(UTC)
            with Session(connection) as db:
                try:
                    source, state = import_state(db, spec)
                    if state.last_checked_at and now < state.last_checked_at:
                        raise ValueError("check time precedes last check")
                    response = acquire(state)
                    if report:
                        from app.catalog.imports.refresh import record_report

                        result = record_report(db, spec, source, state, response, now)
                    elif response.status == 304:
                        if (
                            not state.active_snapshot_id
                            or state.checked_content_hash is None
                        ):
                            raise ValueError("304 without accepted state")
                        result = ImportResult("unchanged", state.active_snapshot_id)
                    elif response.status == 200:
                        result = _promote(db, spec, source, state, response.body, now)
                        state.checked_content_hash = hashlib.sha256(
                            response.body
                        ).hexdigest()
                    else:
                        raise ValueError("fetch failed")
                    state.last_checked_at = now
                    state.last_successful_check_at = now
                    state.failure_count = 0
                    state.failure_code = None
                    if refresh:
                        state.refresh_last_checked_at = now
                        state.refresh_last_successful_check_at = now
                        state.refresh_failure_count = 0
                    if not report:
                        state.etag = response.etag
                        state.last_modified = response.last_modified
                    db.commit()
                    return result
                except (ValueError, SQLAlchemyError, OSError) as exc:
                    db.rollback()
                    # Failure bookkeeping is separate from the rolled-back candidate transaction.
                    source, state = import_state(db, spec, validate_source=False)
                    state.last_checked_at = max(now, state.last_checked_at or now)
                    state.failure_count += 1
                    if refresh:
                        state.refresh_last_checked_at = max(
                            now, state.refresh_last_checked_at or now
                        )
                        state.refresh_failure_count += 1
                    state.failure_code = (
                        "persistence_failure"
                        if isinstance(exc, SQLAlchemyError)
                        else "validation_or_fetch_failure"
                    )
                    result = ImportResult(
                        "failed", state.active_snapshot_id, state.failure_code
                    )
                    db.commit()
                    return result
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock})
            connection.commit()


def import_bytes(engine, source_id, raw, *, checked_at=None):
    from app.catalog.imports.fetch import FetchResponse

    return _run(
        engine, source_id, lambda _: FetchResponse(200, raw), checked_at=checked_at
    )


def import_source(
    engine,
    source_id,
    *,
    fetcher=None,
    checked_at=None,
    nonblocking=False,
    refresh=False,
):
    """Structured feeds may promote; reviewed reports require an explicit manifest import."""
    from app.catalog.imports.fetch import fetch

    spec = get_source(source_id)
    if spec.get("importContract"):
        from app.catalog.imports.acquisition import acquire_source

        return _run(
            engine,
            source_id,
            lambda state: acquire_source(spec, fetcher=fetcher, state=state),
            checked_at=checked_at,
            nonblocking=nonblocking,
            refresh=refresh,
        )
    if spec["importMode"] != "automatic_structured":
        raise ValueError("reviewed report: import an explicitly reviewed manifest")
    fetcher = fetcher or fetch

    def acquire(state):
        return fetcher(
            spec["resultArtifact"],
            allowed_urls=(spec["resultArtifact"],),
            max_bytes=spec["maxPayloadBytes"],
            expected_hash=spec.get("artifactSha256"),
            etag=state.etag,
            last_modified=state.last_modified,
        )

    return _run(
        engine,
        source_id,
        acquire,
        checked_at=checked_at,
        nonblocking=nonblocking,
        refresh=refresh,
    )
