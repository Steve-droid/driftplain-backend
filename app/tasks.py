"""Legacy review/security compatibility facade over app.task_contracts.

Old recommendation/project creation and agent CLI dispatch retain two tasks.
The explicit execution API uses the five-task registry and exact profile support.
"""

from __future__ import annotations

from app.task_contracts import LEGACY_AGENT_TASKS, LegacyTaskType

CI_REVIEW = "ci_review"
SECURITY_ANALYSIS = "security_analysis"

TaskType = LegacyTaskType  # Old recommendation/create endpoints remain two-task.
PROJECT_TASK_TYPES: tuple[str, ...] = (CI_REVIEW, SECURITY_ANALYSIS)

# catalog vocabulary → the agent's short task name (the contract's `task` field)
AGENT_TASK_BY_TASK_TYPE = LEGACY_AGENT_TASKS

# Per-project review preferences (review task only): bounded text appended to the
# agent's system prompt. The bound is the contract's (§3b.1), mirrored by the agent.
REVIEW_PREFERENCES_MAX_LEN = 2000

# Human labels the API/UI/chat can share.
TASK_LABELS: dict[str, str] = {
    CI_REVIEW: "PR code review",
    SECURITY_ANALYSIS: "security analysis",
    "test_generation": "test generation",
    "ci_failure_diagnosis": "CI failure diagnosis",
    "other": "custom task",
}


def is_project_task(task_type: str | None) -> bool:
    return task_type in PROJECT_TASK_TYPES


def agent_task_for(task_type: str) -> str:
    """`ci_review → "review"`, `security_analysis → "security"`; anything else is a
    programming error (the column is validated at create/update)."""
    try:
        return AGENT_TASK_BY_TASK_TYPE[task_type]
    except KeyError:  # pragma: no cover — guarded by the project validators
        raise ValueError(f"{task_type!r} is not a project task type") from None
