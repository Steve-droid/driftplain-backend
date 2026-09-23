"""B5 public projection: historical identity, safe status and anonymous active views."""

import json
from datetime import UTC, datetime

from app.catalog.imports.pipeline import import_bytes
from app.models import CatalogImportState, CatalogSourceSnapshot
from tests.test_catalog_imports import payload
from tests.test_catalog_v1 import _all_keys, _seed_normalized_catalog


def test_active_view_is_opt_in_and_cursor_bound(client, db_session):
    graph = _seed_normalized_catalog(db_session)
    snapshot = db_session.get(CatalogSourceSnapshot, graph["known"].source_snapshot_id)
    db_session.add(
        CatalogImportState(source_id=snapshot.source_id, active_snapshot_id=snapshot.id)
    )
    db_session.commit()
    page = client.get("/catalog/v1/observations?view=active&limit=1").json()
    assert len(page["items"]) == 1
    assert page["items"][0]["snapshotStatus"] == "active"
    cursor = page["pageInfo"]["nextCursor"]
    assert (
        client.get("/catalog/v1/observations", params={"cursor": cursor}).status_code
        == 422
    )
    assert (
        client.get(
            "/catalog/v1/observations", params={"cursor": cursor, "view": "active"}
        ).status_code
        == 200
    )
    state = db_session.get(CatalogImportState, snapshot.source_id)
    state.active_snapshot_id = None
    db_session.commit()
    assert client.get("/catalog/v1/observations?view=active").json()["items"] == []
    assert len(client.get("/catalog/v1/observations").json()["items"]) == 2


def test_public_projection_keeps_citation_separate_from_artifact(client, db_session):
    import_bytes(
        db_session.get_bind(),
        "mmlu-pro",
        payload(),
        checked_at=datetime(2026, 9, 24, tzinfo=UTC),
    )
    row = client.get("/catalog/v1/observations?view=active").json()["items"][0]
    assert row["citationUrl"].startswith("https://")
    assert row["sourceUrl"].startswith("urn:sha256:")
    assert row["coverageNote"]
    assert row["protocolConfiguration"]
    assert row["runner"]
    family = client.get("/catalog/v1/benchmarks").json()["items"][0]
    assert (
        family["tooltip"]
        == "Challenging multiple-choice knowledge and reasoning questions across subject areas."
    )
    assert family["collection"] == "reasoning_knowledge"
    assert family["sources"][0]["snapshotId"] == row["sourceSnapshotId"]
    state = db_session.get(CatalogImportState, family["sources"][0]["id"])
    state.failure_count = 1
    state.failure_code = "fetch_error"
    state.etag = "private-header"
    db_session.commit()
    source = client.get("/catalog/v1/sources").json()["items"][0]
    assert source["refreshStatus"] == "failed"
    assert source["snapshotId"] == row["sourceSnapshotId"]
    assert source["publicationDate"] is None
    keys = set(_all_keys(source))
    assert not keys & {
        "etag",
        "lastModified",
        "failureCode",
        "rawBytes",
        "checkedContentHash",
    }
    assert "private-header" not in json.dumps(source)


def test_definition_only_and_missing_source_are_explicit(client, db_session):
    import_bytes(
        db_session.get_bind(),
        "mrcr-v2",
        payload("mrcr-v2"),
        checked_at=datetime(2026, 9, 24, tzinfo=UTC),
    )
    family = client.get("/catalog/v1/benchmarks").json()["items"][0]
    assert family["sources"][0]["snapshotId"] is not None
    assert client.get("/catalog/v1/observations?view=active").json()["items"] == []
    assert family["tooltip"].startswith("Retrieving the requested information")
    graph = _seed_normalized_catalog(db_session)
    row = client.get(f"/catalog/v1/observations/{graph['known'].id}").json()
    assert row["snapshotStatus"] == "unknown"
    assert row["coverageNote"] is None


def test_capability_filter_and_summary_are_server_side(client, db_session):
    for source in ("mmlu-pro", "mrcr-v2"):
        result = import_bytes(
            db_session.get_bind(),
            source,
            payload(source),
            checked_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        assert result.status == "promoted"
    result = client.get("/catalog/v1/benchmarks?collection=reasoning_knowledge").json()
    assert [b["slug"] for b in result["items"]] == ["mmlu-pro"]
    assert result["items"][0]["versionLabels"]
    assert result["items"][0]["metricUnits"] == ["percent"]
    assert client.get("/catalog/v1/benchmarks?collection=absent").json()["items"] == []
    assert client.get("/catalog/v1/sources?unreviewed=yes").status_code == 422


def test_failed_refresh_does_not_hide_last_good_active_evidence(client, db_session):
    result = import_bytes(
        db_session.get_bind(),
        "mmlu-pro",
        payload(),
        checked_at=datetime(2026, 9, 24, tzinfo=UTC),
    )
    snapshot = db_session.get(CatalogSourceSnapshot, result.snapshot_id)
    state = db_session.get(CatalogImportState, snapshot.source_id)
    state.failure_count = 2
    state.failure_code = "parse_error"
    db_session.commit()
    rows = client.get("/catalog/v1/observations?view=active").json()["items"]
    assert len(rows) == 3
    assert all(row["sourceSnapshotId"] == result.snapshot_id for row in rows)
