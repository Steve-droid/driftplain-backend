"""Construct pre-B17 history through retained internal services, never public APIs.

The tiny isolated app supplies Pydantic/auth/session handling for historical service
regressions and existing-agent fixtures. It is not mounted on the production app.
Public retirement is exercised with the unmodified client in test_legacy_retirement.
"""
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, get_db
from app.models import User
from app.projects.service import create_project
from app.recommend.service import recommend
from app.schemas.project import ProjectCreate, ProjectOut
from app.schemas.recommend import RecommendationRequest, RecommendationResult


def post(client, path, **kwargs):
    history = FastAPI()
    history.dependency_overrides = dict(client.app.dependency_overrides)

    @history.post('/recommendations', response_model=RecommendationResult, status_code=201)
    def recommendation(payload: RecommendationRequest, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
        return recommend(db, payload, user)

    @history.post('/projects', response_model=ProjectOut, status_code=201)
    def project(payload: ProjectCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
        return create_project(db, payload, user)

    with TestClient(history) as fixture_client:
        return fixture_client.post(path, **kwargs)
