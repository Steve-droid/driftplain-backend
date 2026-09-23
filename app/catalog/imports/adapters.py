"""Ten family adapters share a candidate contract, never perform I/O or write rows."""

from datetime import date
import hashlib
import re

from app.catalog.imports.contracts import (
    Batch,
    Citation,
    Manifest,
    Metric,
    Row,
    decode,
    fingerprint,
)
from app.catalog.imports.registry import B3_IDS, REGISTRY_HASH, get_source

# Keys are semantic boundaries, not merely display labels. Missing values fail closed;
# genuinely unreported upstream settings require an explicit reason and isolated group.
REQUIRED_PROTOCOL = {
    "gpqa-diamond": {"split", "shots", "prompt", "tools"},
    "hle": {"dataset", "modality", "judge", "tools"},
    "mmlu-pro": {"shots", "prompt"},
    "aime-2025": {"year", "tools", "samples", "effort"},
    "frontiermath-tier4-v2": {"tier", "set_version", "set", "effort", "tools"},
    "arc-agi-2": {"dataset", "set", "system_category", "verification"},
    "swe-bench-verified": {
        "dataset",
        "agent",
        "model",
        "harness_version",
        "attempts",
        "verification",
    },
    "terminal-bench-4": {
        "dataset",
        "dataset_ref",
        "agent",
        "model",
        "effort",
        "attempts",
    },
    "mrcr-v2": {"context_length", "needles"},
    "osworld-verified": {
        "modality",
        "framework",
        "max_steps",
        "runs",
        "task_total",
        "verification",
    },
}
METRICS = {
    "gpqa-diamond": {"accuracy": "higher"},
    "hle": {"accuracy": "higher", "calibration_error": "lower"},
    "mmlu-pro": {"overall_accuracy": "higher"},
    "aime-2025": {"pass_at_1": "higher"},
    "frontiermath-tier4-v2": {"accuracy": "higher"},
    "arc-agi-2": {"task_accuracy": "higher"},
    "swe-bench-verified": {"resolved": "higher"},
    "terminal-bench-4": {"accuracy": "higher"},
    "mrcr-v2": {"exact_match": "higher"},
    "osworld-verified": {"task_success_rate": "higher"},
}


def unknown(reason):
    return {"unknown_reason": reason}


def validate_rows(source_id, rows, source):
    identities = set()
    for row in rows:
        if row.version != source["version"]:
            raise ValueError("benchmark version changed; adapter review required")
        if not REQUIRED_PROTOCOL[source_id] <= row.protocol.keys():
            raise ValueError("missing required protocol semantics")
        for key in REQUIRED_PROTOCOL[source_id]:
            value = row.protocol[key]
            if value is None or value == "" or value == {}:
                raise ValueError("empty protocol setting")
            if isinstance(value, dict) and (
                set(value) != {"unknown_reason"}
                or not isinstance(value["unknown_reason"], str)
                or not value["unknown_reason"].strip()
            ):
                raise ValueError("unknown setting needs an explicit reason")
        numeric_keys = {
            "shots",
            "samples",
            "attempts",
            "max_steps",
            "runs",
            "context_length",
            "needles",
            "task_total",
            "tier",
            "year",
        }
        for key in REQUIRED_PROTOCOL[source_id]:
            value = row.protocol[key]
            if isinstance(value, dict):
                continue
            if key in numeric_keys:
                if (
                    source_id == "swe-bench-verified"
                    and key == "attempts"
                    and value == "2+"
                ):
                    continue
                if type(value) is not int or value < (0 if key == "shots" else 1):
                    raise ValueError("invalid protocol count")
            elif key == "tools" and source_id in ("gpqa-diamond", "hle", "aime-2025"):
                if type(value) is not bool:
                    raise ValueError(
                        "tool policy must be a boolean or explicitly unknown"
                    )
            elif key == "model" and source_id == "swe-bench-verified":
                if (
                    not isinstance(value, list)
                    or not value
                    or any(not isinstance(v, str) or not v.strip() for v in value)
                ):
                    raise ValueError("invalid model tags")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError("protocol identity must be nonempty text")
        if source_id == "hle" and row.protocol["modality"] not in (
            "multimodal",
            "text-only subset",
        ):
            raise ValueError("HLE modality mismatch")
        if source_id == "arc-agi-2" and (
            row.protocol["set"] not in ("public", "semi-private")
            or row.protocol["system_category"]
            not in ("Base LLM", "Reasoning System", "Kaggle")
        ):
            raise ValueError("ARC protocol category mismatch")
        if source_id == "osworld-verified" and (
            row.protocol["modality"]
            not in ("screenshot-only", "screenshot+a11y", "a11y-only")
            or row.protocol["verification"]
            not in ("OSWorld team-verified", "self-reported")
        ):
            raise ValueError("OSWorld modality/verification mismatch")
        if source_id == "gpqa-diamond" and row.protocol["split"] != "Diamond":
            raise ValueError("GPQA split mismatch")
        if source_id == "aime-2025" and row.protocol["year"] != 2025:
            raise ValueError("AIME year mismatch")
        if source_id == "hle" and row.protocol["dataset"] != "HLE final 2500":
            raise ValueError("HLE dataset mismatch")
        if source_id == "frontiermath-tier4-v2" and (
            row.protocol["tier"],
            row.protocol["set_version"],
        ) != (4, "v2"):
            raise ValueError("FrontierMath tier/version mismatch")
        if source_id == "terminal-bench-4" and row.protocol["dataset_ref"] != "v4.0.0":
            raise ValueError("Terminal-Bench dataset mismatch")
        if source_id == "arc-agi-2" and (
            row.protocol["dataset"] != "ARC-AGI-2"
            or row.protocol["verification"] != "verified"
        ):
            raise ValueError("ARC dataset/verification mismatch")
        if source_id == "osworld-verified" and row.protocol["task_total"] not in (
            361,
            369,
        ):
            raise ValueError("OSWorld task total requires separate reviewed policy")
        if source_id == "mrcr-v2" and row.protocol["needles"] not in (2, 4, 8):
            raise ValueError("MRCR needle count mismatch")
        keys = [m.key for m in row.metrics]
        if len(set(keys)) != len(keys) or set(keys) != set(METRICS[source_id]):
            raise ValueError("metric schema changed; adapter review required")
        for metric in row.metrics:
            if (
                metric.direction != METRICS[source_id][metric.key]
                or metric.unit != "percent"
            ):
                raise ValueError("metric unit/direction mismatch")
        identity = (row.locator, fingerprint(row.protocol))
        # Even identical duplicate rows mean the source batch is not unambiguous.
        if identity in identities:
            raise ValueError("duplicate observation identity")
        identities.add(identity)
    minimum = 0 if source_id == "mrcr-v2" else 2 if source_id == "aime-2025" else 3
    if len({r.model_label for r in rows}) < minimum:
        raise ValueError("incomplete source coverage")


def _reviewed(source_id, raw, source, source_registry_hash):
    document = decode(raw)
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
    ):
        raise ValueError("invalid manifest schema version")
    manifest = Manifest.model_validate(document)
    if (
        manifest.source_id != source_id
        or manifest.registry_hash != source_registry_hash
    ):
        raise ValueError("manifest source/registry mismatch")
    validate_rows(source_id, manifest.rows, source)
    return Batch(
        source_id=source_id,
        content_hash=hashlib.sha256(raw).hexdigest(),
        publication_date=manifest.publication_date,
        coverage_note=manifest.coverage_note,
        rows=manifest.rows,
        review=manifest.review,
    )


def _swe(raw, source):
    document = decode(raw)
    if not isinstance(document, dict) or not isinstance(
        document.get("leaderboards"), list
    ):
        raise ValueError("missing leaderboards schema")
    verified = [
        b
        for b in document["leaderboards"]
        if isinstance(b, dict) and b.get("name") == "Verified"
    ]
    if len(verified) != 1 or not isinstance(verified[0].get("results"), list):
        raise ValueError("missing/duplicate Verified leaderboard")
    digest = hashlib.sha256(raw).hexdigest()
    rows = []
    folders = set()
    for data in verified[0]["results"]:
        required = {
            "folder",
            "model_display",
            "agent",
            "date",
            "resolved",
            "checked",
            "tags",
        }
        if not isinstance(data, dict) or not required <= data.keys():
            raise ValueError("SWE schema changed")
        if not isinstance(data["date"], str):
            raise ValueError("SWE publication date must be an ISO date")
        known_unchecked = (
            "false (See README.md for info on how to get your results verified)"
        )
        checked = data["checked"]
        if (
            not (checked is None or type(checked) is bool or checked == known_unchecked)
            or not isinstance(data["tags"], list)
            or any(not isinstance(t, str) for t in data["tags"])
        ):
            raise ValueError("SWE verification/tags schema changed")
        folder = data["folder"]
        if (
            not isinstance(folder, str)
            or not re.fullmatch(r"[A-Za-z0-9_.+-]+", folder)
            or folder in folders
        ):
            raise ValueError("invalid/duplicate submission folder")
        folders.add(folder)
        models = [
            t.removeprefix("Model: ") for t in data["tags"] if t.startswith("Model: ")
        ]
        attempts = [
            t.removeprefix("System: Attempts - ")
            for t in data["tags"]
            if t.startswith("System: Attempts - ")
        ]
        if len(attempts) > 1 or (
            attempts and not (attempts[0].isdigit() or attempts[0] == "2+")
        ):
            raise ValueError("ambiguous attempt count")
        protocol = {
            "dataset": "SWE-bench Verified 500",
            "agent": data["agent"],
            "model": models
            or unknown("No model identifier tag; display label retained"),
            "harness_version": unknown(
                "SWE-bench evaluation harness version not supplied"
            ),
            "agent_version": data.get("mini-swe-agent_version")
            or next(
                (
                    t.removeprefix("Mini: ")
                    for t in data["tags"]
                    if t.startswith("Mini: ")
                ),
                unknown("Agent version not supplied"),
            ),
            "attempts": (int(attempts[0]) if attempts[0].isdigit() else "2+")
            if attempts
            else unknown("Attempts not supplied"),
            "verification": "checked"
            if checked is True
            else unknown("Verification state not supplied")
            if checked is None
            else "unchecked",
            "effort": data.get("reasoning_effort")
            or unknown("Reasoning effort not supplied"),
            "submission": folder,
        }
        metric = Metric(
            key="resolved",
            value=data["resolved"],
            denominator=500,
            attempts=int(attempts[0]) if attempts and attempts[0].isdigit() else None,
            missing_reason="not reported by source"
            if data["resolved"] is None
            else None,
        )
        rows.append(
            Row(
                locator=folder,
                model_label=data["model_display"],
                version=source["version"],
                protocol=protocol,
                evaluator=source["evaluator"],
                publication_date=date.fromisoformat(data["date"]),
                citation=Citation(
                    url=source["resultArtifact"],
                    locator="Verified/" + folder,
                    content_hash=digest,
                ),
                metrics=(metric,),
                source_data=data,
            )
        )
    validate_rows("swe-bench-verified", rows, source)
    return Batch(
        source_id="swe-bench-verified",
        content_hash=digest,
        publication_date=date.fromisoformat(source["publicationDate"]),
        coverage_note="Full Verified section; checked/unchecked systems and submission settings stay separate.",
        rows=tuple(rows),
    )


def parse_candidates(
    source_id: str, raw: bytes, source: dict, *, source_registry_hash: str
) -> Batch:
    """Pure adapter entry point; all source bytes and frozen metadata are supplied."""
    if source_id not in B3_IDS or source["id"] != source_id:
        raise ValueError("unsupported or mismatched source")
    if not isinstance(raw, bytes) or not 0 < len(raw) <= source["maxPayloadBytes"]:
        raise ValueError("source payload outside allowed bounds")
    if source_id == "swe-bench-verified":
        batch = _swe(raw, source)
        # Local file imports must enforce the same immutable provenance as HTTPS fetches.
        if batch.content_hash != source["artifactSha256"]:
            raise ValueError("immutable artifact hash mismatch")
        return batch
    return _reviewed(source_id, raw, source, source_registry_hash)


def parse(source_id: str, raw: bytes) -> Batch:
    """Convenience wrapper that loads verified local metadata before pure parsing."""
    return parse_candidates(
        source_id, raw, get_source(source_id), source_registry_hash=REGISTRY_HASH
    )
