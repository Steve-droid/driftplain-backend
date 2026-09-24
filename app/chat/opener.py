"""Legacy calculation text, explicitly limited and never evidence of quality or savings."""

from __future__ import annotations

from decimal import Decimal

from app.schemas.savings import SavingsResponse
from app.tasks import SECURITY_ANALYSIS, TASK_LABELS

_SAVINGS_REF = "savings:project"

# E20: what the agent DOES per task — the spend summary and the opener name it, so
# the chat never describes a security scan as "reviewing PR diffs" (or vice versa).
_TASK_DESCRIPTION = {
    "ci_review": (
        "one call per pull request: reviews the PR diff for security risks and "
        "coding-style issues; findings gate the build on high/critical"
    ),
    SECURITY_ANALYSIS: (
        "an agentic scan of the whole checkout for vulnerabilities; findings carry "
        "CWE ids and a critical one fails the build"
    ),
}


def task_line(task_type: str | None) -> str:
    """'PR code review (ci_review)' — the label + the catalog vocabulary."""
    if not task_type:
        return "CI code review"
    label = TASK_LABELS.get(task_type, task_type.replace("_", " "))
    return f"{label} ({task_type})"


def _task_verb(task_type: str | None) -> str:
    """What the selected model has been doing, for the opener sentence."""
    if task_type == SECURITY_ANALYSIS:
        return "scanned your repository for vulnerabilities (agentic security analysis, CWE-tagged findings)"
    return "reviewed your PR diffs (security + coding-style)"


def _money(value: Decimal | None) -> str:
    """USD with 4 dp (the demo's per-run costs are sub-cent); '—' when unknown."""
    if value is None:
        return "—"
    return f"${value:.4f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}%"


def format_savings_snapshot(savings: SavingsResponse) -> str:
    """Stored legacy estimates and their limits for dormant chat grounding."""
    k = savings.kpis
    selected = savings.selected_model or "the selected model"
    baseline = savings.baseline_model or "the baseline"
    # acceptance_rate is a 0–1 fraction (see app.quality.service); scale for display.
    rate = "not yet rated" if k.acceptance_rate is None else f"{k.acceptance_rate * 100:.0f}%"
    task = savings.task_type
    return "\n".join([
        "Legacy calculation summary (stored estimates, not an invoice):",
        f"- Task: {task_line(task)} — {_TASK_DESCRIPTION.get(task or '', 'CI task')}",
        f"- Selected model: {selected}",
        f"- Baseline model (not run): {baseline}",
        f"- CI runs in range: {k.runs_count}",
        f"- Historical input/output-only selected-model estimate: {_money(k.spend_this_period)}",
        f"- Hypothetical comparison, historically feedback-filtered: {_money(k.cumulative_saved)}",
        f"- Historically excluded comparison amount: {_money(k.quality_risk)}",
        f"- Finding acceptance among rated findings: {rate}",
        "These estimates exclude native cache/reasoning categories and may lack executed rate snapshots.",
        "The baseline was not executed. Do not claim measured savings, cheapest suitability or model equivalence.",
        "Finding acceptance is not recall, coverage or a quality guarantee. Use the usage dashboard for current accounting.",
    ])


def build_opener(savings: SavingsResponse) -> str:
    """Describe legacy stored calculations honestly; never rewrite persisted openers."""
    k = savings.kpis
    if k.runs_count == 0:
        return "No runs yet. The usage dashboard will show reported usage, estimated cost and results."
    return (
        f"Legacy history for {task_line(savings.task_type)}: across {k.runs_count} CI run(s), "
        f"{savings.selected_model or 'the selected model'} has {_task_verb(savings.task_type)}. "
        f"The stored input/output-only estimate is {_money(k.spend_this_period)}. "
        f"The historical hypothetical comparison with {savings.baseline_model or 'the baseline'} "
        f"is {_money(k.cumulative_saved)}; {_money(k.quality_risk)} was excluded by the old feedback filter. "
        "The baseline was not run: this is not measured savings. "
        "Finding acceptance is not recall or a quality guarantee. "
        "Use the usage dashboard for accounting coverage and limitations."
    )


def savings_trace(savings: SavingsResponse) -> tuple[str, str]:
    """The (ref, snippet) for the kind='savings' retrieval-trace row of an answer."""
    return _SAVINGS_REF, format_savings_snapshot(savings)
