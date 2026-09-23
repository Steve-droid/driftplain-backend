"""2026-09-23 policy: 365 days after supersession; active/held evidence never expires.

This is eligibility reporting, not deletion. B2 immutable evidence triggers remain
intact. B6 must set a hold before referencing catalog evidence from selections/runs.
Physical purge needs a separately reviewed migration/maintenance path; no scheduler
or destructive maintenance is included in B3.
"""

from datetime import timedelta
from sqlalchemy import select
from app.models import CatalogImportState, CatalogSnapshotLifecycle

RETENTION_DAYS = 365


def retention_candidates(db, *, now):
    return list(
        db.scalars(
            select(CatalogSnapshotLifecycle.snapshot_id)
            .where(
                CatalogSnapshotLifecycle.superseded_at
                <= now - timedelta(days=RETENTION_DAYS),
                CatalogSnapshotLifecycle.hold_reason.is_(None),
                ~CatalogSnapshotLifecycle.snapshot_id.in_(
                    select(CatalogImportState.active_snapshot_id).where(
                        CatalogImportState.active_snapshot_id.is_not(None)
                    )
                ),
            )
            .order_by(CatalogSnapshotLifecycle.snapshot_id)
        )
    )
