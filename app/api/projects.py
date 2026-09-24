"""Historical project reads/edits; weighted creation and re-picks retired in B17."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, get_db
from app.models import User
from app.projects import service
from app.schemas.project import ProjectOut, ProjectUpdate
from app.api.recommend import RETIRED_DETAIL

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", deprecated=True, status_code=410)
def create_project(current_user: User = Depends(get_current_user)) -> None:
    raise HTTPException(410, RETIRED_DETAIL)


@router.get("", response_model=list[ProjectOut])
def list_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProjectOut]:
    return service.list_projects(db, current_user)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectOut:
    return service.get_project(db, project_id, current_user)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectOut:
    # Resolve ownership before reporting a retired selection mutation. Mixed payloads
    # fail as a whole; name/preferences must never be partly committed.
    project = service.get_project(db, project_id, current_user)
    if not project.execution_revision_id and payload.model_fields_set & {
        "selected_option_id", "baseline_model_id", "task_type"
    }:
        raise HTTPException(410, RETIRED_DETAIL)
    return service.update_project(db, project_id, payload, current_user)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    service.delete_project(db, project_id, current_user)
