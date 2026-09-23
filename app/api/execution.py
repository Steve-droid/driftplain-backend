"""Authenticated execution contracts, deliberately outside /catalog/v1."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, get_db, require_project_token
from app.ci.tokens import hash_token, mint_token
from app.models import CiRun, ExecutionRevision, JenkinsConnection, Project, User
from app.selections import service
from app.selections.schemas import ExplicitProjectCreate, ExplicitProjectUpdate
from app.task_contracts import PROFILES, TASK_CONTRACT_VERSION

router = APIRouter(prefix="/execution/v1", tags=["execution"])
Db = Annotated[Session, Depends(get_db)]
Owner = Annotated[User, Depends(get_current_user)]
TokenProject = Annotated[Project, Depends(require_project_token)]


@router.get("/tasks")
def task_registry(user: Owner):
    return {
        "version": TASK_CONTRACT_VERSION,
        "profiles": [
            {
                "task": task,
                "mode": mode,
                "language": language,
                "proposeFix": fix,
                "capability": values[0],
                "resultKinds": list(values[1]),
            }
            for (task, mode, language, fix), values in PROFILES.items()
        ],
    }


@router.get("/projects/{project_id}/runs/{run_id}/result")
def run_result(project_id: int, run_id: int, db: Db, user: Owner):
    service.owned_project(db, project_id, user)
    run = db.get(CiRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(404, "Run not found")
    return {
        "runId": run.id,
        "executionRevisionId": run.execution_revision_id,
        "taskResult": run.task_result,
        "gate": run.gate,
    }


@router.get("/candidates")
def candidates(
    task: str,
    mode: str,
    db: Db,
    user: Owner,
    language: str | None = None,
    propose_fix: bool = Query(False, alias="proposeFix"),
    q: str = Query("", max_length=200),
    group: str | None = Query(None, max_length=64),
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(100, ge=1, le=100),
):
    return service.candidates(
        db,
        task=task,
        mode=mode,
        language=language,
        propose_fix=propose_fix,
        q=q,
        group=group,
        offset=offset,
        limit=limit,
    )


@router.post("/projects", status_code=201)
def create_project(payload: ExplicitProjectCreate, db: Db, user: Owner):
    return service.create_project(db, payload, user)


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Db, user: Owner):
    project = service.owned_project(db, project_id, user)
    if project.execution_revision_id is None:
        service.invalid("Project uses the legacy contract")
    return service.project_out(db, project)


@router.patch("/projects/{project_id}")
def update_project(
    project_id: int, payload: ExplicitProjectUpdate, db: Db, user: Owner
):
    return service.update_project(db, project_id, payload, user)


@router.get("/projects/{project_id}/revisions/{revision_id}")
def get_revision(project_id: int, revision_id: int, db: Db, user: Owner):
    service.owned_project(db, project_id, user)
    revision = db.get(ExecutionRevision, revision_id)
    if revision is None or revision.project_id != project_id:
        raise HTTPException(404, "Execution revision not found")
    return dict(
        revision.configuration, projectId=project_id, executionRevisionId=revision.id
    )


@router.post("/projects/{project_id}/ci-token")
def issue_token(project_id: int, db: Db, user: Owner):
    return _issue_token(project_id, db, user, rotate=False)


@router.post("/projects/{project_id}/ci-token/rotate")
def rotate_token(project_id: int, db: Db, user: Owner):
    return _issue_token(project_id, db, user, rotate=True)


def _issue_token(project_id, db, user, *, rotate):
    project = service.owned_project(db, project_id, user)
    if project.execution_revision_id is None:
        service.invalid("Project uses the legacy contract")
    conn = db.scalar(
        select(JenkinsConnection).where(JenkinsConnection.project_id == project.id)
    )
    if conn is None:
        conn = JenkinsConnection(project_id=project.id)
        db.add(conn)
    token = None
    if conn.ci_token_hash is None or rotate:
        token = mint_token()
        conn.ci_token_hash = hash_token(token)
    db.commit()
    return {"projectId": project.id, "token": token}


# require_project_token reads the named project_id path parameter, just like legacy.
@router.get("/projects/{project_id}/agent-config")
def agent_config(
    project: TokenProject,
    db: Db,
    task_contract_version: int | None = Query(None, alias="taskContractVersion"),
):
    result = service.agent_config(db, project)
    if task_contract_version is not None and (
        task_contract_version != 1 or result.get("taskContractVersion") != 1
    ):
        raise HTTPException(
            409, "Task contract version is not supported by this revision"
        )
    if (
        result["taskType"] not in ("ci_review", "security_analysis")
        and task_contract_version != 1
    ):
        raise HTTPException(409, "This task requires taskContractVersion=1 negotiation")
    return result
