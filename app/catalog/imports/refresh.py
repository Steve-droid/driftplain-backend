"""Bounded refresh orchestration; reports require a separate human attestation."""

import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import urldefrag

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.catalog.imports.fetch import fetch
from app.catalog.imports.pipeline import (
    ImportResult,
    _run,
    import_source,
    import_state,
    source_lock_key,
)
from app.catalog.imports.registry import get_source, source_ids
from app.models import CatalogImportState as State, CatalogSource as Source
from app.models import (
    CatalogSourceSnapshot as Snapshot,
    CatalogSnapshotLifecycle as Lifecycle,
)

MISSED_CHECK_SECONDS = 7 * 3600  # six-hour cadence plus one hour of operational grace


def is_report(spec):
    return (
        spec.get("importContract", {}).get("mode", spec["importMode"])
        == "reviewed_manifest"
    )


def report_url(spec):
    # Fixed registry authority; the fragment is a citation locator, never part of HTTP GET.
    return urldefrag(spec["resultArtifact"])[0]


def refresh_source(engine, source_id, *, fetcher=None, checked_at=None):
    spec = get_source(source_id)
    if not is_report(spec):
        return import_source(
            engine,
            source_id,
            fetcher=fetcher,
            checked_at=checked_at,
            nonblocking=True,
            refresh=True,
        )
    transport = fetcher or fetch

    def acquire(state):
        url = report_url(spec)
        return transport(
            url,
            allowed_urls=(url,),
            max_bytes=spec["maxPayloadBytes"],
            media_types=("text/html", "text/plain", "text/csv", "application/json"),
            etag=state.report_etag,
            last_modified=state.report_last_modified,
        )

    return _run(
        engine,
        source_id,
        acquire,
        checked_at=checked_at,
        report=True,
        nonblocking=True,
        refresh=True,
    )


def record_report(db, spec, source, state, response, now):
    if response.status == 200:
        if not 0 < len(response.body) <= min(spec["maxPayloadBytes"], 8000000):
            raise ValueError("report outside byte bound")
        digest = hashlib.sha256(response.body).hexdigest()
        db.execute(
            text("""INSERT INTO catalog_report_artifact
          (source_id, content_hash, artifact_url, raw_bytes, fetched_at)
          VALUES (:source, :hash, :url, :raw, :now) ON CONFLICT DO NOTHING"""),
            dict(
                source=source.id,
                hash=digest,
                url=report_url(spec),
                raw=response.body,
                now=now,
            ),
        )
        state.report_content_hash = digest
    elif response.status != 304 or not state.report_content_hash:
        raise ValueError("report fetch failed or 304 without previous acquisition")
    state.report_etag = response.etag
    state.report_last_modified = response.last_modified
    pending = state.report_content_hash != state.reviewed_report_hash
    return ImportResult(
        "pending_review" if pending else "unchanged", state.active_snapshot_id
    )


@contextmanager
def operator_session(engine, source_id):
    spec = get_source(source_id)
    key = source_lock_key(source_id)
    with engine.connect() as connection:
        locked = connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        )
        connection.commit()
        if not locked:
            raise ValueError("source check already running")
        try:
            with Session(connection) as db, db.begin():
                source, state = import_state(db, spec)
                yield db, source, state
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            connection.commit()


def _audit(db, source, state, snapshot_id, action, reason, now, report_hash=None):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
        raise ValueError("bounded operator identity and reason required")
    if now.tzinfo is None or (state.last_checked_at and now < state.last_checked_at):
        raise ValueError("invalid operator timestamp")
    snapshot = db.get(Snapshot, snapshot_id)
    lifecycle = db.get(Lifecycle, snapshot_id)
    if not snapshot or snapshot.source_id != source.id or not lifecycle:
        raise ValueError("snapshot is not accepted evidence for this source")
    db.execute(
        text("""INSERT INTO catalog_operator_action
      (source_id, snapshot_id, previous_snapshot_id, report_hash, action, reason, performed_at)
      VALUES (:source, :snapshot, :previous, :hash, :action, :reason, :now)"""),
        dict(
            source=source.id,
            snapshot=snapshot_id,
            previous=state.active_snapshot_id,
            hash=report_hash,
            action=action,
            reason=reason,
            now=now,
        ),
    )
    return lifecycle


def review_report(
    engine, source_id, report_hash, snapshot_id, reason, *, checked_at=None
):
    """Human attests an accepted snapshot accurately represents these exact report bytes.

    Does not parse, promote, change observations or imply that a fetch performed review.
    Operator must first import any changed extraction through the existing manifest CLI.
    """
    if not is_report(get_source(source_id)):
        raise ValueError("source does not use reviewed reports")
    with operator_session(engine, source_id) as (db, source, state):
        if (
            not report_hash
            or report_hash != state.report_content_hash
            or snapshot_id != state.active_snapshot_id
        ):
            raise ValueError(
                "review must bind latest fetched hash and current accepted snapshot"
            )
        _audit(
            db,
            source,
            state,
            snapshot_id,
            "review_report",
            reason,
            checked_at or datetime.now(UTC),
            report_hash,
        )
        state.reviewed_report_hash = report_hash


def select_snapshot(engine, source_id, snapshot_id, reason, *, checked_at=None):
    now = checked_at or datetime.now(UTC)
    with operator_session(engine, source_id) as (db, source, state):
        lifecycle = _audit(
            db, source, state, snapshot_id, "select_snapshot", reason, now
        )
        if state.active_snapshot_id != snapshot_id:
            old = (
                db.get(Lifecycle, state.active_snapshot_id)
                if state.active_snapshot_id
                else None
            )
            if old:
                old.superseded_at = now
            lifecycle.superseded_at = None
            # activated_at is the original activation; the audit records each selection.
            state.active_snapshot_id = snapshot_id
            state.last_promoted_at = now
            # Prior report attestation was for a different accepted extraction.
            state.reviewed_report_hash = None


def source_health(db, source_id, *, now=None):
    get_source(source_id)
    now = now or datetime.now(UTC)
    state = db.scalar(select(State).join(Source).where(Source.slug == source_id))
    snapshot = (
        db.get(Snapshot, state.active_snapshot_id)
        if state and state.active_snapshot_id
        else None
    )
    return _health(source_id, state, snapshot, now)


def all_source_health(db, *, now=None):
    now = now or datetime.now(UTC)
    rows = db.execute(
        select(Source.slug, State, Snapshot)
        .outerjoin(State, State.source_id == Source.id)
        .outerjoin(Snapshot, Snapshot.id == State.active_snapshot_id)
        .where(Source.slug.in_(source_ids()))
    ).all()
    by_source = {slug: (state, snapshot) for slug, state, snapshot in rows}
    return [
        _health(source, *by_source.get(source, (None, None)), now)
        for source in source_ids()
    ]


def _health(source_id, state, snapshot, now):
    successful = state.refresh_last_successful_check_at if state else None
    publication = (
        datetime.combine(snapshot.publication_date, datetime.min.time(), UTC)
        if snapshot and snapshot.publication_date
        else None
    )
    return dict(
        source=source_id,
        last_checked_at=state.refresh_last_checked_at if state else None,
        last_successful_check_at=successful,
        missed_check=successful is None
        or (now - successful).total_seconds() > MISSED_CHECK_SECONDS,
        failure_count=state.refresh_failure_count if state else 0,
        pending_review=bool(
            state
            and state.report_content_hash
            and state.report_content_hash != state.reviewed_report_hash
        ),
        report_content_hash=state.report_content_hash if state else None,
        active_snapshot_id=snapshot.id if snapshot else None,
        active_snapshot_age_seconds=(now - snapshot.fetched_at).total_seconds()
        if snapshot
        else None,
        upstream_content_age_seconds=(now - publication).total_seconds()
        if publication
        else None,
    )
