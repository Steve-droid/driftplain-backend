"""B1 evidence is frozen data, never executable fetch/adapter configuration."""

import copy
import hashlib
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[3] / "data/catalog/b3"
REGISTRY_HASH = "3ada3d03d3bc94518d9369e9279b2ea036ae1db6277ee193d10263e3c76f9495"
B3_IDS = (
    "gpqa-diamond",
    "hle",
    "mmlu-pro",
    "aime-2025",
    "frontiermath-tier4-v2",
    "arc-agi-2",
    "swe-bench-verified",
    "terminal-bench-4",
    "mrcr-v2",
    "osworld-verified",
)


def registry_hash():
    digest = hashlib.sha256((DATA / "registry.json").read_bytes()).hexdigest()
    if digest != REGISTRY_HASH:
        raise ValueError(
            "B1 registry drift: review and verify against portfolio manifest"
        )
    return digest


B4_IDS = (
    "deepswe-1-1",
    "livecodebench-v5",
    "livebench-2026-06-25",
    "codereviewbench",
    "realvuln-3-1-0",
    "testgeneval",
    "swt-bench",
    "logdx-ci",
    "ci-repair-bench",
)


def source_ids():
    return B3_IDS + B4_IDS


def get_source(source_id):
    if source_id not in source_ids():
        raise ValueError("unsupported catalog source")
    registry_hash()
    registry = json.loads((DATA / "registry.json").read_bytes())
    source = copy.deepcopy(next(s for s in registry["sources"] if s["id"] == source_id))
    if source_id in B4_IDS:
        source["importContract"] = json.loads(
            (DATA.parent / "b4/contracts.json").read_bytes()
        )[source_id]
    return source
