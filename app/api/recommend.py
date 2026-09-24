"""Retired weighted recommendation routes. Historical records remain readable."""
from fastapi import APIRouter, Depends, HTTPException
from app.auth.deps import get_current_user
from app.models import User

router = APIRouter(prefix='/recommendations', tags=['retired legacy actions'])

RETIRED_DETAIL = (
    'Weighted recommendations and legacy project selection are retired. '
    'Use /execution/v1/candidates and /execution/v1/projects for an explicit '
    'source-backed choice. Existing projects, CI tokens and run history remain available.'
)


@router.post('', deprecated=True, status_code=410)
@router.post('/prefill', deprecated=True, status_code=410)
def retired_recommendation(_: User = Depends(get_current_user)) -> None:
    raise HTTPException(410, RETIRED_DETAIL)
