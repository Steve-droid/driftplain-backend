"""Atomic pre-call claims, independent of result delivery and immutable revisions."""

import uuid
from fastapi import HTTPException
from pydantic import Field
from sqlalchemy.dialects.postgresql import insert
from app.models.execution import DiagnosisClaim
from app.selections import service
from app.task_contracts import Contract, FailureContext, TaskConfiguration


class ClaimRequest(Contract):
    execution_revision_id: int = Field(gt=0)
    failure: FailureContext


def claim(db, project, request):
    config = service.agent_config(db, project)
    cfg = TaskConfiguration.model_validate(config["taskConfiguration"])
    ctx = request.failure
    if (
        config["taskType"] != "ci_failure_diagnosis"
        or cfg.diagnosis is None
        or config["executionRevisionId"] != request.execution_revision_id
        or ctx.stage != cfg.diagnosis.stage
        or ctx.original_status != "FAILURE"
        or ctx.exit_status in (None, 0)
        or not ctx.log_excerpt.strip()
        or ctx.claim_id
    ):
        raise HTTPException(
            422,
            "Claim requires current configured upstream failure with usable redacted logs",
        )
    identity = uuid.uuid4().hex
    created = db.scalar(
        insert(DiagnosisClaim)
        .values(
            id=identity,
            project_id=project.id,
            execution_revision_id=request.execution_revision_id,
            build_id=ctx.build_id,
            stage=ctx.stage,
            context=ctx.model_dump(mode="json", by_alias=True),
        )
        .on_conflict_do_nothing(constraint="uq_diagnosis_failure")
        .returning(DiagnosisClaim.id)
    )
    db.commit()  # durable BEFORE granting any model authority; no leases or takeover
    return {"claimed": created is not None, "claimId": identity if created else None}


def validate_claim(db, project, revision, payload):
    result = payload.task_result
    if not result or not result.failure:
        return
    ctx = result.failure
    if ctx.build_id != payload.jenkins_build_id:
        raise ValueError("Failure build identity differs from run")
    if not ctx.claim_id:
        if (
            result.execution_status != "skipped"
            or result.runner_usage
            and result.runner_usage.attempts
        ):
            raise ValueError("Invoked diagnosis requires a durable claim")
        return
    row = db.get(DiagnosisClaim, ctx.claim_id)
    expected = ctx.model_copy(update={"claim_id": None}).model_dump(
        mode="json", by_alias=True
    )
    if (
        row is None
        or row.project_id != project.id
        or row.execution_revision_id != revision.id
        or row.context != expected
    ):
        raise ValueError("Diagnosis claim does not match executed failure and revision")
