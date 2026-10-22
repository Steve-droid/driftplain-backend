"""Reviewed label mappings for structured sources, carried by an overlay snapshot."""

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.catalog.imports.adapters import parse
from app.catalog.imports.overlay import wrap
from app.catalog.imports.pipeline import import_bytes
from app.catalog.imports.registry import REGISTRY_HASH, get_source
from app.models import (
    CatalogModel,
    CatalogModelAlias,
    CatalogObservation,
    CatalogSourcePayload,
    CatalogSourceSnapshot,
)

ROOT = Path(__file__).resolve().parents[1]
DEEPSWE = (ROOT / "data/catalog/b4/deepswe-1-1.json").read_bytes()
SWE = (ROOT / "tests/fixtures/catalog/swe-bench-verified.json").read_bytes()
NOW = datetime(2026, 10, 22, tzinfo=UTC)
REVIEW = {"reviewer": "operator", "reviewed_at": "2026-10-22", "note": "Label match."}


def mappings(source, artifact, pairs):
    digest = hashlib.sha256(artifact).hexdigest()
    url = (
        source["importContract"]["url"]
        if source.get("importContract")
        else source["resultArtifact"]
    )
    return {
        "review": REVIEW,
        "mappings": [
            {
                "source_label": label,
                "model_slug": slug,
                "review": REVIEW,
                "citation": {"url": url, "locator": label, "content_hash": digest},
            }
            for label, slug in pairs
        ],
    }


def overlay(source_id, artifact, pairs):
    spec = get_source(source_id)
    return wrap(spec, artifact, mappings(spec, artifact, pairs), REGISTRY_HASH)


def test_overlay_maps_only_reviewed_labels_and_keeps_rows():
    plain = parse("deepswe-1-1", DEEPSWE)
    raw = overlay("deepswe-1-1", DEEPSWE, [("gpt-5-5", "gpt-5-5-model")])
    batch = parse("deepswe-1-1", raw)
    assert batch.content_hash == hashlib.sha256(raw).hexdigest()
    assert batch.content_hash != plain.content_hash
    assert [r.locator for r in batch.rows] == [r.locator for r in plain.rows]
    mapped = [r for r in batch.rows if r.reviewed_mapping]
    assert mapped and {r.model_label for r in mapped} == {"gpt-5-5"}
    assert {r.reviewed_mapping.model_slug for r in mapped} == {"gpt-5-5-model"}
    assert batch.review.note == "Label match."
    assert "Reviewed label mappings: 1 (overlay)." in batch.coverage_note


def test_overlay_keeps_the_pinned_artifact_hash():
    raw = overlay("swe-bench-verified", SWE, [("Claude 4.5 Sonnet", "sonnet")])
    batch = parse("swe-bench-verified", raw)
    labelled = [r for r in batch.rows if r.model_label == "Claude 4.5 Sonnet"]
    assert labelled and all(r.reviewed_mapping for r in labelled)
    assert all(r.reviewed_mapping is None for r in batch.rows if r not in labelled)
    doc = json.loads(SWE)
    doc["leaderboards"][0]["results"][0]["resolved"] = 1
    tampered = json.dumps(doc).encode()
    with pytest.raises(ValueError, match="immutable artifact hash"):
        parse(
            "swe-bench-verified",
            overlay("swe-bench-verified", tampered, [("Claude 4.5 Sonnet", "s")]),
        )


def edit(raw, change):
    doc = json.loads(raw)
    change(doc)
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def reordered(raw):
    # Same content, keys out of canonical order (the prefix still marks an overlay).
    doc = json.loads(raw)
    keys = ["artifact_base64", *reversed([k for k in doc if k != "artifact_base64"])]
    return json.dumps({k: doc[k] for k in keys}, separators=(",", ":")).encode()


RAW = overlay("deepswe-1-1", DEEPSWE, [("gpt-5-5", "gpt-5-5-model")])


@pytest.mark.parametrize(
    "bad",
    [
        overlay("deepswe-1-1", DEEPSWE, [("absent-label", "x")]),
        overlay("deepswe-1-1", DEEPSWE, [("gpt-5-5", "x"), ("gpt-5-5", "y")]),
        edit(RAW, lambda d: d["mappings"][0]["citation"].update(content_hash="0" * 64)),
        edit(RAW, lambda d: d.update(artifact_sha256="0" * 64)),
        edit(RAW, lambda d: d.update(registry_hash="0" * 64)),
        edit(RAW, lambda d: d["mappings"][0].update(extra=True)),
        reordered(RAW),
        edit(
            RAW,
            lambda d: d.update(
                artifact_base64=base64.b64encode(RAW).decode(),
                artifact_sha256=hashlib.sha256(RAW).hexdigest(),
            ),
        ),
    ],
    ids=[
        "absent-label",
        "duplicate-label",
        "foreign-citation",
        "artifact-hash",
        "registry",
        "extra-field",
        "non-canonical",
        "nested",
    ],
)
def test_overlay_fails_closed(bad):
    with pytest.raises(ValueError):
        parse("deepswe-1-1", bad)


def test_overlay_is_for_another_source_or_a_reviewed_manifest():
    with pytest.raises(ValueError, match="source/registry"):
        parse("testgeneval", RAW)
    swt = get_source("swt-bench")
    with pytest.raises(ValueError, match="carry their own mappings"):
        wrap(swt, b"{}", {"review": REVIEW, "mappings": []}, REGISTRY_HASH)


def test_validation_command_binds_mappings_without_database(tmp_path, capsys):
    from app.catalog.imports.__main__ import main

    spec = get_source("deepswe-1-1")
    path = tmp_path / "mappings.json"
    path.write_text(json.dumps(mappings(spec, DEEPSWE, [("gpt-5-5", "m")])))
    artifact = ROOT / "data/catalog/b4/deepswe-1-1.json"
    argv = ["--source", "deepswe-1-1", "--manifest", str(artifact)]
    assert main(argv + ["--mappings", str(path)]) == 0
    mapped = json.loads(capsys.readouterr().out)
    assert main(argv) == 0
    plain = json.loads(capsys.readouterr().out)
    assert mapped["status"] == "validated"
    assert mapped["observations"] == plain["observations"]
    assert mapped["contentHash"] != plain["contentHash"]


def test_overlay_promotion_resolves_alias_and_stores_payload(
    migrated_engine, db_session
):
    db_session.add(CatalogModel(slug="gpt-5-5-model", name="GPT-5.5"))
    db_session.commit()
    first = import_bytes(migrated_engine, "deepswe-1-1", DEEPSWE, checked_at=NOW)
    second = import_bytes(migrated_engine, "deepswe-1-1", RAW, checked_at=NOW)
    assert (first.status, second.status) == ("promoted", "promoted")
    assert (
        import_bytes(migrated_engine, "deepswe-1-1", RAW, checked_at=NOW).status
        == "unchanged"
    )
    model = db_session.scalar(select(CatalogModel).filter_by(slug="gpt-5-5-model"))
    alias = db_session.scalar(
        select(CatalogModelAlias).filter_by(source_label="gpt-5-5")
    )
    assert (alias.resolution_status, alias.catalog_model_id) == ("resolved", model.id)
    mapped = db_session.scalars(
        select(CatalogObservation).filter_by(source_snapshot_id=second.snapshot_id)
    ).all()
    assert {o.catalog_model_id for o in mapped if o.source_model_label == "gpt-5-5"} == {
        model.id
    }
    assert {
        o.catalog_model_id for o in mapped if o.source_model_label != "gpt-5-5"
    } == {None}
    # The earlier unmapped snapshot is history, not rewritten.
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(CatalogObservation)
            .where(
                CatalogObservation.source_snapshot_id == first.snapshot_id,
                CatalogObservation.catalog_model_id.is_not(None),
            )
        )
        == 0
    )
    snap = db_session.get(CatalogSourceSnapshot, second.snapshot_id)
    assert snap.content_type == "application/json"
    assert snap.artifact_uri == "urn:sha256:" + snap.content_hash
    assert db_session.get(CatalogSourcePayload, snap.id).raw_bytes == RAW


def test_overlay_target_must_exist(migrated_engine):
    assert (
        import_bytes(migrated_engine, "deepswe-1-1", RAW, checked_at=NOW).status
        == "failed"
    )
