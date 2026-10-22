"""Reviewed label mappings for structured sources, stored with the artifact bytes they review.

Structured adapters derive every row from upstream data, so they cannot carry a reviewed
model mapping. An overlay wraps the exact artifact with the mappings; its canonical bytes are
the snapshot payload, so the mapping is part of the immutable, hash-checked snapshot.
"""

import base64
import hashlib

from app.catalog.imports.contracts import (
    Batch,
    Mapping,
    MappingOverlay,
    canonical,
    decode,
)

# Canonical JSON sorts keys, so every overlay starts with its first key.
PREFIX = b'{"artifact_base64":"'
# The catalog_source_payload size limit.
MAX_BYTES = 8_000_000
MAX_MAPPING_BYTES = 256_000


def is_overlay(raw):
    return isinstance(raw, bytes) and raw.startswith(PREFIX)


def accepts_overlay(spec):
    # Reviewed manifests carry mappings per row; only structured sources need an overlay.
    contract = spec.get("importContract")
    if contract:
        return contract["mode"] != "reviewed_manifest"
    return spec["importMode"] == "automatic_structured"


def wrap(spec, artifact, mappings, registry_hash):
    """Build the canonical overlay from artifact bytes and {"review", "mappings"}."""
    if not accepts_overlay(spec):
        raise ValueError("reviewed manifests carry their own mappings")
    if not isinstance(mappings, dict) or set(mappings) != {"review", "mappings"}:
        raise ValueError("mapping file requires exactly review and mappings")
    overlay = MappingOverlay.model_validate(
        {
            "schema_version": 1,
            "source_id": spec["id"],
            "registry_hash": registry_hash,
            "review": mappings["review"],
            "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
            "artifact_base64": base64.b64encode(artifact).decode(),
            "mappings": mappings["mappings"],
        }
    )
    return canonical(overlay).encode()


def unwrap(source_id, raw, spec, registry_hash, parse_artifact):
    """Parse the artifact with its own adapter, then attach the reviewed mappings."""
    if len(raw) > MAX_BYTES:
        raise ValueError("overlay exceeds payload limit")
    overlay = MappingOverlay.model_validate(decode(raw))
    # One canonical encoding per overlay keeps the snapshot hash deterministic.
    if canonical(overlay).encode() != raw:
        raise ValueError("overlay is not canonical")
    if overlay.source_id != source_id or overlay.registry_hash != registry_hash:
        raise ValueError("overlay source/registry mismatch")
    if not accepts_overlay(spec):
        raise ValueError("reviewed manifests carry their own mappings")
    artifact = base64.b64decode(overlay.artifact_base64, validate=True)
    if hashlib.sha256(artifact).hexdigest() != overlay.artifact_sha256:
        raise ValueError("overlay artifact hash mismatch")
    if is_overlay(artifact):
        raise ValueError("nested overlay")
    batch = parse_artifact(artifact)
    citations = {}
    for row in batch.rows:
        citations.setdefault(row.model_label, set()).add(row.citation.content_hash)
    by_label = {}
    for m in overlay.mappings:
        if m.source_label not in citations:
            raise ValueError("mapped label is absent from the artifact")
        if m.source_label in by_label:
            raise ValueError("duplicate mapped label")
        if m.citation.content_hash not in citations[m.source_label]:
            raise ValueError("mapping must cite the artifact it reviews")
        by_label[m.source_label] = Mapping(
            model_slug=m.model_slug, review=m.review, citation=m.citation
        )
    rows = tuple(
        row.model_copy(update={"reviewed_mapping": by_label[row.model_label]})
        if row.model_label in by_label
        else row
        for row in batch.rows
    )
    return Batch(
        source_id=source_id,
        content_hash=hashlib.sha256(raw).hexdigest(),
        publication_date=batch.publication_date,
        coverage_note=batch.coverage_note
        + f" Reviewed label mappings: {len(by_label)} (overlay).",
        rows=rows,
        review=overlay.review,
    )
