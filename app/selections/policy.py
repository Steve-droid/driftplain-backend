"""Versioned selection policy, independent of the legacy weighted recommender."""

from decimal import Decimal

from app.task_contracts import capability_for

POLICY_VERSION = "2026-09-24.1"


def policy_for(task, mode, language=None, propose_fix=False):
    capability = capability_for(task, mode, language, propose_fix)
    benchmark = version = metric = None
    if task == "ci_review":
        benchmark, version, metric = (
            "codereviewbench",
            "30 PR / 95 confirmed bug Kodus replay group generated 2026-09-11",
            "f1",
        )
    elif task == "security_analysis":
        benchmark, version, metric = (
            "realvuln-3-1-0",
            "benchmark 3.1.0 / ground truth 3.0.0",
            "strict_f3",
        )
    elif task == "test_generation" and language == "python":
        benchmark, version, metric = (
            "testgeneval",
            "Public leaderboard payload inspected 2026-09-22; Extra column",
            "e_at_1",
        )
    return {
        "version": POLICY_VERSION,
        "task": task,
        "mode": mode,
        "language": language,
        "proposeFix": propose_fix,
        "capability": capability,
        "benchmark": benchmark,
        "benchmarkVersion": version,
        "metric": metric,
        "direction": "higher" if metric else None,
        "inputContract": capability + "/v1",
        "outputContract": capability + "/v1",
    }


def order_scores(rows: list[tuple[int, Decimal | None]], direction: str):
    """Competition ranks (1, 1, 3); identifiers stabilize ties, never break them."""
    if direction not in ("higher", "lower"):
        raise ValueError("Metric must be ranking")
    rows = sorted(
        ((key, score) for key, score in rows if score is not None),
        key=lambda r: (-r[1] if direction == "higher" else r[1], r[0]),
    )
    result, previous, rank = [], None, 0
    for position, (key, score) in enumerate(rows, 1):
        if score != previous:
            rank = position
        result.append((key, rank))
        previous = score
    return result
