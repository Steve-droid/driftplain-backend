"""Separate execution surface. No catalog write or public runtime projection."""

import hashlib
import json
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from app.auth.deps import require_owner, require_real_project
from app.models import (
    CatalogBenchmarkFamily,
    CatalogBenchmarkVersion,
    CatalogMetricDefinition,
    CatalogModel,
    CatalogObservation,
    CatalogObservationMetric,
    CatalogProtocol,
    CatalogProvider,
    CatalogProviderDeployment,
    CatalogSnapshotLifecycle,
    CatalogSourceSnapshot,
    ExecutionRevision,
    ExecutionRuntime,
    ModelSelection,
    Project,
)
from app.selections.policy import order_scores, policy_for
from app.task_contracts import TaskConfiguration, configure

# Trusted provider identifiers; no endpoint supplied by users or catalog imports.
PROVIDERS = {
    "anthropic": ("api_key", "ANTHROPIC_API_KEY"),
    "google": ("api_key", "GEMINI_API_KEY"),
    "gemini": ("api_key", "GEMINI_API_KEY"),
    "openai": ("api_key", "OPENAI_API_KEY"),
    "deepseek": ("api_key", "DEEPSEEK_API_KEY"),
    "bedrock": ("aws_iam", None),
}


def invalid(message):
    raise HTTPException(422, message)


def policy(task, mode, language=None, propose_fix=False):
    try:
        return policy_for(task, mode, language, propose_fix)
    except ValueError as e:
        invalid(str(e))


def runtime_valid(runtime, deployment, provider, p):
    return (
        runtime.enabled
        and runtime.verification_status == "verified"
        and runtime.verified_at is not None
        and bool(runtime.verification_ref)
        and runtime.task == p["task"]
        and runtime.mode == p["mode"]
        and runtime.capability == p["capability"]
        and bool(runtime.runtime_version)
        and PROVIDERS.get(runtime.provider)
        == (runtime.auth_mode, runtime.credential_env_var)
        and deployment.model_id == runtime.catalog_model_id
        and deployment.deployment_key == runtime.provider_model_id
        and provider.slug == runtime.provider
    )


def _unknown(value):
    if isinstance(value, dict):
        return (
            "unknown_reason" in value
            or "unreported_settings_scope" in value
            or any(_unknown(v) for v in value.values())
        )
    if isinstance(value, list):
        return any(_unknown(v) for v in value)
    return False


def comparison_group(obs, protocol, family, version, definition, metric, p):
    """A group is exact snapshot/evaluator/settings/metric coverage, never a max."""
    if (
        not p["metric"]
        or family.slug != p["benchmark"]
        or version is None
        or version.version != p["benchmarkVersion"]
        or protocol is None
        or obs.provenance_status != "complete"
        or _unknown(protocol.configuration)
        or definition.key != p["metric"]
        or definition.direction != p["direction"]
        or metric.value is None
        or metric.missing_reason is not None
    ):
        return None
    c = protocol.configuration
    if p["task"] == "ci_review" and not (
        c.get("runner") == "kodus"
        and c.get("runner_version")
        and c.get("execution_mode") == "replay"
        and c.get("judge") == "claude-haiku-4-5"
        and c.get("pull_requests") == 30
        and c.get("bugs") == 95
        and c.get("coverage") == "complete"
        and c.get("recommendation_eligible") is True
        and c.get("comparison_group")
    ):
        return None
    if p["task"] == "test_generation" and not (
        c.get("configuration") == "Extra"
        and c.get("language") == "Python"
        and c.get("attempts") == 1
        and c.get("runner")
    ):
        return None
    # No accepted RealVuln 3.1 score artifact exists. A later policy revision must
    # explicitly name its runner/prompt/coverage before this gate can open.
    if p["task"] == "security_analysis":
        return None
    group = [
        obs.source_snapshot_id,
        obs.evaluator_id,
        obs.benchmark_version_id,
        c.get("comparison_group")
        if p["task"] == "ci_review"
        else protocol.configuration_fingerprint,
        definition.id,
        metric.category,
        metric.subset,
        metric.aggregation,
        metric.sample_size,
        metric.denominator,
        metric.attempts,
    ]
    return hashlib.sha256(json.dumps(group, sort_keys=True).encode()).hexdigest()


def reported_rank(obs):
    try:
        data = json.loads(obs.notes or "{}").get("source_data", {})
        value = data.get("rank") if isinstance(data, dict) else None
        return value if type(value) is int and value > 0 else None
    except (ValueError, AttributeError):
        return None


def candidates(
    db,
    *,
    task,
    mode,
    language=None,
    propose_fix=False,
    q="",
    group=None,
    offset=0,
    limit=100,
    catalog_model_id=None,
    observation_id=None,
):
    p = policy(task, mode, language, propose_fix)
    runtimes = db.execute(
        select(
            ExecutionRuntime, CatalogModel, CatalogProviderDeployment, CatalogProvider
        )
        .join(CatalogModel, CatalogModel.id == ExecutionRuntime.catalog_model_id)
        .join(
            CatalogProviderDeployment,
            CatalogProviderDeployment.id == ExecutionRuntime.deployment_id,
        )
        .join(
            CatalogProvider, CatalogProvider.id == CatalogProviderDeployment.provider_id
        )
        .where(
            ExecutionRuntime.task == task,
            ExecutionRuntime.mode == mode,
            ExecutionRuntime.capability == p["capability"],
            ExecutionRuntime.enabled.is_(True),
            ExecutionRuntime.verification_status == "verified",
        )
    ).all()
    runtimes = [r for r in runtimes if runtime_valid(r[0], r[2], r[3], p)]
    if not runtimes:
        return {"policy": p, "groups": [], "items": [], "total": 0, "offset": offset}
    model_ids = {r[1].id for r in runtimes}
    # Only canonical, source-backed observations establish selection membership.
    evidence = db.execute(
        select(
            CatalogObservation,
            CatalogProtocol,
            CatalogBenchmarkFamily,
            CatalogBenchmarkVersion,
            CatalogMetricDefinition,
            CatalogObservationMetric,
        )
        .join(
            CatalogBenchmarkFamily,
            CatalogBenchmarkFamily.id == CatalogObservation.benchmark_family_id,
        )
        .outerjoin(
            CatalogProtocol, CatalogProtocol.id == CatalogObservation.protocol_id
        )
        .outerjoin(
            CatalogBenchmarkVersion,
            CatalogBenchmarkVersion.id == CatalogObservation.benchmark_version_id,
        )
        .join(
            CatalogObservationMetric,
            CatalogObservationMetric.observation_id == CatalogObservation.id,
        )
        .join(
            CatalogMetricDefinition,
            CatalogMetricDefinition.id == CatalogObservationMetric.metric_definition_id,
        )
        .where(
            CatalogObservation.origin == "source",
            CatalogObservation.source_snapshot_id.is_not(None),
            or_(
                CatalogObservation.catalog_model_id.in_(model_ids),
                CatalogBenchmarkFamily.slug == p["benchmark"],
            ),
        )
    ).all()
    observations, scores, groups = {}, {}, {}
    ambiguous = set()
    for obs, proto, family, version, definition, metric in evidence:
        if obs.catalog_model_id in model_ids:
            observations[obs.id] = obs
        g = comparison_group(obs, proto, family, version, definition, metric, p)
        if g:
            if obs.id in scores:
                ambiguous.add(obs.id)
            scores[obs.id] = (g, metric, proto)
            groups.setdefault(g, {})[obs.id] = metric.value
    # Several primary metric scopes require a future explicit metric-choice
    # contract; do not silently select whichever database row was returned last.
    for obs_id in ambiguous:
        scores.pop(obs_id, None)
        for values in groups.values():
            values.pop(obs_id, None)
    source_ranks = {
        g: dict(order_scores(list(values.items()), p["direction"]))
        for g, values in groups.items()
    }
    items = []
    for rt, model, deployment, provider in runtimes:
        for obs in observations.values():
            if obs.catalog_model_id != model.id:
                continue
            scored = scores.get(obs.id)
            g = scored[0] if scored else None
            items.append(
                {
                    "runtimeId": rt.id,
                    "catalogModelId": model.id,
                    "model": model.name,
                    "provider": provider.name,
                    "providerModelId": rt.provider_model_id,
                    "deploymentId": deployment.id,
                    "observationId": obs.id,
                    "snapshotId": obs.source_snapshot_id,
                    "method": "benchmark_ranked"
                    if group is not None and g == group
                    else "supported_unranked",
                    "group": g,
                    "score": str(scored[1].value) if scored else None,
                    "reportedValue": scored[1].reported_value if scored else None,
                    "sourceRank": reported_rank(obs),
                    "sourceGroupRank": source_ranks[g][obs.id] if scored else None,
                    "benchmarkRunner": scored[2].runner if scored else None,
                    "executionMode": rt.mode,
                    "runtimeVersion": rt.runtime_version,
                    "rank": None,
                    "position": None,
                }
            )
    group_info = [
        {
            "id": g,
            "totalResults": len(values),
            "supportedResults": len(
                {i["observationId"] for i in items if i["group"] == g}
            ),
        }
        for g, values in sorted(groups.items())
        if any(i["group"] == g for i in items)
    ]
    if group is not None and group not in {g["id"] for g in group_info}:
        invalid("Comparable group is absent from the eligible set")
    ranked = [i for i in items if i["method"] == "benchmark_ranked"]
    from decimal import Decimal

    ranked.sort(
        key=lambda i: (
            -Decimal(i["score"]) if p["direction"] == "higher" else Decimal(i["score"]),
            i["catalogModelId"],
            i["runtimeId"],
            i["observationId"],
        )
    )
    previous, rank = None, 0
    for pos, i in enumerate(ranked, 1):
        if i["score"] != previous:
            rank = pos
        i["rank"], i["position"] = rank, pos
        previous = i["score"]
    unranked = sorted(
        (i for i in items if i["method"] == "supported_unranked"),
        key=lambda i: (
            i["model"].casefold(),
            i["provider"].casefold(),
            i["runtimeId"],
            i["observationId"],
        ),
    )
    items = [
        i
        for i in ranked + unranked
        if q.casefold() in (i["model"] + " " + i["provider"]).casefold()
        and (catalog_model_id is None or i["catalogModelId"] == catalog_model_id)
        and (observation_id is None or i["observationId"] == observation_id)
    ]
    return {
        "policy": p,
        "groups": group_info,
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
    }


def owned_project(db, project_id, user):
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None:
        raise HTTPException(404, "Project not found")
    require_owner(project.user_id, user)
    require_real_project(project)
    return project


def require_runtime(db, runtime_id, p):
    row = db.execute(
        select(ExecutionRuntime, CatalogProviderDeployment, CatalogProvider)
        .join(
            CatalogProviderDeployment,
            CatalogProviderDeployment.id == ExecutionRuntime.deployment_id,
        )
        .join(
            CatalogProvider, CatalogProvider.id == CatalogProviderDeployment.provider_id
        )
        .where(ExecutionRuntime.id == runtime_id)
        .with_for_update(of=ExecutionRuntime)
        .execution_options(populate_existing=True)
    ).first()
    if row is None or not runtime_valid(*row, p):
        invalid("Exact task/mode runtime configuration is not enabled and verified")
    return row[0]


def save_selection(db, project, choice, task_configuration=None):
    p = policy(choice.task, choice.mode, choice.language, choice.propose_fix)
    rt = require_runtime(db, choice.runtime_id, p)
    # Specific choices validate independently of pagination and any browser visit.
    result = candidates(
        db,
        task=choice.task,
        mode=choice.mode,
        language=choice.language,
        propose_fix=choice.propose_fix,
        group=choice.group,
        limit=2**31 - 1,
    )
    item = next(
        (
            i
            for i in result["items"]
            if i["runtimeId"] == rt.id and i["observationId"] == choice.observation_id
        ),
        None,
    )
    if item is None or item["method"] != choice.method:
        invalid("Selection needs matching source evidence and selection method")
    if choice.method == "supported_unranked" and choice.group is not None:
        invalid("Unranked selections do not carry a recommendation group")
    snapshot = db.get(CatalogSourceSnapshot, item["snapshotId"])
    # Upsert obtains the same lifecycle row lock used by import promotion. A hold
    # precedes references and rolls back with a rejected transaction.
    db.execute(
        insert(CatalogSnapshotLifecycle)
        .values(
            snapshot_id=snapshot.id,
            activated_at=snapshot.fetched_at or datetime.now(UTC),
            hold_reason="execution history",
        )
        .on_conflict_do_update(
            index_elements=["snapshot_id"], set_={"hold_reason": "execution history"}
        )
    )
    selection = ModelSelection(
        project_id=project.id,
        user_id=project.user_id,
        runtime_id=rt.id,
        catalog_model_id=rt.catalog_model_id,
        observation_id=choice.observation_id,
        snapshot_id=snapshot.id,
        legacy_option_id=project.selected_option_id,
        legacy_baseline_model_id=project.baseline_model_id,
        method=choice.method,
        policy_snapshot=p,
        result_snapshot=item if choice.method == "benchmark_ranked" else None,
    )
    db.add(selection)
    db.flush()
    project.task_type = choice.task
    project.selected_option_id = None
    project.baseline_model_id = None
    return new_revision(db, project, selection, rt, task_configuration)


def new_revision(db, project, selection, rt, task_configuration=None):
    p = selection.policy_snapshot
    try:
        task_contract = configure(
            p["task"], p["mode"], task_configuration, p["language"], p["proposeFix"]
        )
    except ValueError as e:
        invalid(str(e))
    model = db.get(CatalogModel, rt.catalog_model_id)
    config = {
        "contractVersion": 2,
        "billingContractVersion": 1,
        "taskType": selection.policy_snapshot["task"],
        "executionMode": rt.mode,
        "capability": rt.capability,
        "runtimeId": rt.id,
        "runtimeVersion": rt.runtime_version,
        "selectionId": selection.id,
        "catalogModelId": rt.catalog_model_id,
        "deploymentId": rt.deployment_id,
        "model": {
            "name": model.name,
            "provider": rt.provider,
            "providerModelId": rt.provider_model_id,
            "authMode": rt.auth_mode,
            "credentialEnvVar": rt.credential_env_var,
        },
        "reviewPreferences": project.review_preferences
        if rt.task == "ci_review"
        else None,
        "policy": selection.policy_snapshot,
        **task_contract,
    }
    from app.billing.rates import snapshot_for
    revision = ExecutionRevision(
        billing_snapshot=snapshot_for(db, rt.id),
        project_id=project.id, selection_id=selection.id, configuration=config
    )
    db.add(revision)
    db.flush()
    project.execution_revision_id = revision.id
    return revision


def create_project(db, payload, user):
    project = Project(
        user_id=user.id,
        name=payload.name,
        task_type=payload.selection.task,
        review_preferences=payload.review_preferences,
    )
    db.add(project)
    db.flush()
    save_selection(db, project, payload.selection, payload.task_configuration)
    db.commit()
    return project_out(db, project)


def update_project(db, project_id, payload, user):
    project = owned_project(db, project_id, user)
    if payload.name is not None:
        project.name = payload.name
    if "review_preferences" in payload.model_fields_set:
        project.review_preferences = payload.review_preferences
    if payload.selection:
        # Preserve custom instructions on same-profile re-picks. A profile switch
        # requires a new explicit configuration, never silently widens authority.
        task_config = payload.task_configuration
        if (
            "task_configuration" not in payload.model_fields_set
            and project.execution_revision_id
        ):
            old = db.get(ExecutionRevision, project.execution_revision_id).configuration
            new_policy = policy(
                payload.selection.task,
                payload.selection.mode,
                payload.selection.language,
                payload.selection.propose_fix,
            )
            if old["capability"] == new_policy["capability"] and old.get(
                "taskConfiguration"
            ):
                task_config = TaskConfiguration.model_validate(old["taskConfiguration"])
        save_selection(db, project, payload.selection, task_config)
    elif project.execution_revision_id:
        revision = db.get(ExecutionRevision, project.execution_revision_id)
        selection = db.get(ModelSelection, revision.selection_id)
        rt = require_runtime(db, selection.runtime_id, selection.policy_snapshot)
        if {"review_preferences", "task_configuration"} & payload.model_fields_set:
            task_config = payload.task_configuration
            if (
                "task_configuration" not in payload.model_fields_set
                and revision.configuration.get("taskConfiguration")
            ):
                task_config = TaskConfiguration.model_validate(
                    revision.configuration["taskConfiguration"]
                )
            new_revision(db, project, selection, rt, task_config)
    else:
        invalid("Legacy project needs an explicit selection to use this edit contract")
    db.commit()
    return project_out(db, project)


def project_out(db, project):
    revision = db.get(ExecutionRevision, project.execution_revision_id)
    selection = db.get(ModelSelection, revision.selection_id)
    return {
        "id": project.id,
        "name": project.name,
        "userId": project.user_id,
        "executionRevisionId": revision.id,
        "selectionId": selection.id,
        "selectionMethod": selection.method,
        "legacyOptionId": selection.legacy_option_id,
        "selectedAt": selection.created_at,
        "revisionCreatedAt": revision.created_at,
        "observationId": selection.observation_id,
        "snapshotId": selection.snapshot_id,
        "baselineModelId": None,
        "configuration": revision.configuration,
        "policyResult": selection.result_snapshot,
    }


def agent_config(db, project):
    # Serialize fetch with re-pick; runtime lock serializes concurrent disabling.
    db.refresh(project, with_for_update=True)
    if project.execution_revision_id is None:
        invalid("Project uses the legacy agent configuration contract")
    revision = db.get(ExecutionRevision, project.execution_revision_id)
    selection = db.get(ModelSelection, revision.selection_id)
    require_runtime(db, selection.runtime_id, selection.policy_snapshot)
    result = dict(
        revision.configuration, projectId=project.id, executionRevisionId=revision.id
    )
    db.commit()
    return result
