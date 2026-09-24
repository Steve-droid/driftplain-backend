"""Owner-scoped reported-run estimates and feedback; never provider account spend."""

from datetime import UTC, datetime, timedelta
from sqlalchemy import Numeric, case, cast, func, select
from app.models import CiRun, ExecutionRevision, CiFinding, FindingFeedback, Model
from app.savings.dashboard import _require_owned_project
from app.billing.pricing import usage_status


def project_usage(db, project_id, user, range_label="all", offset=0, limit=100):
    project = _require_owned_project(db, project_id, user)
    filters = [CiRun.project_id == project_id]
    if range_label != "all":
        filters.append(
            CiRun.created_at
            >= datetime.now(UTC) - timedelta(days=int(range_label[:-1]))
        )
    explicit = CiRun.execution_revision_id.is_not(None)
    state = func.coalesce(CiRun.billing["status"].astext, "unavailable")
    cost = cast(CiRun.billing["knownCost"].astext, Numeric())
    columns = [func.count().label("reportedRuns")]
    for name in ("complete", "partial", "unavailable"):
        condition = explicit & (state == name)
        columns.append(func.count().filter(condition).label(name + "Runs"))
        if name != "unavailable":
            columns.append(func.sum(case((condition, cost))).label(name + "Cost"))
    columns.append(func.count().filter(~explicit).label("legacyRuns"))
    raw = (
        db.execute(select(*columns).select_from(CiRun).where(*filters)).mappings().one()
    )
    totals = {
        k: str(v) if k.endswith("Cost") and v is not None else v for k, v in raw.items()
    }
    # Full-period aggregates stay in PostgreSQL; bounded result/config JSON is paged.
    rows = db.execute(
        select(CiRun, ExecutionRevision, Model.name)
        .outerjoin(
            ExecutionRevision, CiRun.execution_revision_id == ExecutionRevision.id
        )
        .outerjoin(Model, CiRun.model_id == Model.id)
        .where(*filters)
        .order_by(CiRun.created_at.desc().nullslast(), CiRun.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    summary = (
        db.execute(
            select(
                func.count(CiFinding.id).label("total"),
                func.count()
                .filter(FindingFeedback.verdict == "accept")
                .label("accepted"),
                func.count()
                .filter(FindingFeedback.verdict == "reject")
                .label("rejected"),
            )
            .select_from(CiFinding)
            .join(CiRun, CiFinding.ci_run_id == CiRun.id)
            .outerjoin(
                FindingFeedback,
                (FindingFeedback.ci_finding_id == CiFinding.id)
                & (FindingFeedback.user_id == user.id),
            )
            .where(*filters)
        )
        .mappings()
        .one()
    )
    overall = dict(summary)
    overall["rated"] = overall["accepted"] + overall["rejected"]
    ids = [r.id for r, _, _ in rows]
    feedback = {i: {"accepted": 0, "rejected": 0, "rated": 0, "total": 0} for i in ids}
    if ids:
        findings = db.execute(
            select(CiFinding.ci_run_id, FindingFeedback.verdict)
            .outerjoin(
                FindingFeedback,
                (FindingFeedback.ci_finding_id == CiFinding.id)
                & (FindingFeedback.user_id == user.id),
            )
            .where(CiFinding.ci_run_id.in_(ids))
        )
        for rid, verdict in findings:
            f = feedback[rid]
            f["total"] += 1
            if verdict in ("accept", "reject"):
                f["rated"] += 1
                f["accepted" if verdict == "accept" else "rejected"] += 1
    output = []
    for run, revision, legacy_name in rows:
        billing = run.billing
        if billing is None:
            billing = {
                "version": 1,
                "basis": "legacy_input_output_only"
                if not revision
                else "historical_unpriced",
                "status": "unavailable",
                "knownCost": None,
                "currency": "USD",
                "categories": [],
                "rateSnapshot": None,
                "reasons": [
                    "legacy_attribution_and_cache_limits"
                    if not revision
                    else "no_historical_rate_snapshot"
                ],
            }
        config = revision.configuration if revision else {}
        result = run.task_result
        output.append(
            {
                "id": run.id,
                "jenkinsBuildId": run.jenkins_build_id,
                "createdAt": run.created_at.isoformat() if run.created_at else None,
                "model": config.get("model", {}).get("providerModelId")
                if revision
                else legacy_name,
                "provider": config.get("model", {}).get("provider"),
                "runtimeId": config.get("runtimeId"),
                "runtimeVersion": config.get("runtimeVersion"),
                "deploymentId": config.get("deploymentId"),
                "executionRevisionId": run.execution_revision_id,
                "task": run.task,
                "mode": config.get("executionMode"),
                "gate": run.gate,
                "gateReason": run.gate_reason,
                "billing": billing,
                "legacyCost": str(run.actual_cost)
                if not revision and run.actual_cost is not None
                else None,
                "usage": (result.get("providerUsage") or result.get("runnerUsage"))
                if result
                else None,
                "legacyUsage": {
                    "inputTokens": run.tokens_in,
                    "outputTokens": run.tokens_out,
                    "cacheReadTokens": run.cache_read_tokens,
                }
                if not revision
                else None,
                "usageStatus": usage_status(
                    result.get("providerUsage"), result.get("runnerUsage")
                )
                if result
                else "unavailable",
                "taskResult": result,
                "feedback": feedback[run.id],
            }
        )
    return {
        "version": 1,
        "projectId": project.id,
        "isExample": project.is_example,
        "range": range_label,
        "totals": totals,
        "feedback": overall,
        "runs": output,
        "offset": offset,
        "limit": limit,
        "total": totals["reportedRuns"],
        "limitations": [
            "Estimates cover reported runs only, not the provider account or invoice.",
            "Unreported aborted requests may incur charges.",
            "Complete and partial estimates are separate; feedback does not change cost.",
            "Legacy amounts use input/output only with limited model attribution.",
            "Rates are pinned when the executed configuration revision is created.",
        ],
    }
