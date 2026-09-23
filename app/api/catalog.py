"""Anonymous, read-only, versioned public benchmark catalog API."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.auth.deps import get_db
from app.catalog import queries
from app.catalog.pagination import CatalogCursorError
from app.schemas.catalog import (
    CatalogBenchmarkDetailOut,
    CatalogBenchmarkPage,
    CatalogModelDetailOut,
    CatalogModelPage,
    CatalogObservationOut,
    CatalogObservationPage,
    CatalogProviderDetailOut,
    CatalogProviderPage,
    CatalogSearchPage,
)

router = APIRouter(prefix="/catalog/v1", tags=["public-catalog"])


def _reject_unknown(request: Request, allowed: set[str]) -> None:
    unknown = set(request.query_params) - allowed
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported query parameter: {sorted(unknown)[0]}",
        )


def _cursor_call(callable_, **kwargs):
    try:
        return callable_(**kwargs)
    except (CatalogCursorError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.get("/models", response_model=CatalogModelPage)
def models(
    request: Request,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
    q: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
) -> CatalogModelPage:
    _reject_unknown(request, {"limit", "cursor", "q"})
    return _cursor_call(queries.list_models, db=db, limit=limit, cursor=cursor, q=q)


@router.get("/models/{model_id}", response_model=CatalogModelDetailOut)
def model_detail(model_id: int, db: Session = Depends(get_db)) -> CatalogModelDetailOut:
    result = queries.get_model(db, model_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Catalog model not found")
    return result


@router.get("/providers", response_model=CatalogProviderPage)
def providers(
    request: Request,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
    q: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
) -> CatalogProviderPage:
    _reject_unknown(request, {"limit", "cursor", "q"})
    return _cursor_call(queries.list_providers, db=db, limit=limit, cursor=cursor, q=q)


@router.get("/providers/{provider_id}", response_model=CatalogProviderDetailOut)
def provider_detail(
    provider_id: int, db: Session = Depends(get_db)
) -> CatalogProviderDetailOut:
    result = queries.get_provider(db, provider_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Catalog provider not found")
    return result


@router.get("/benchmarks", response_model=CatalogBenchmarkPage)
def benchmarks(
    request: Request,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
    q: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
) -> CatalogBenchmarkPage:
    _reject_unknown(request, {"limit", "cursor", "q"})
    return _cursor_call(queries.list_benchmarks, db=db, limit=limit, cursor=cursor, q=q)


@router.get("/benchmarks/{benchmark_id}", response_model=CatalogBenchmarkDetailOut)
def benchmark_detail(
    benchmark_id: int, db: Session = Depends(get_db)
) -> CatalogBenchmarkDetailOut:
    result = queries.get_benchmark(db, benchmark_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Catalog benchmark not found")
    return result


@router.get("/observations", response_model=CatalogObservationPage)
def observations(
    request: Request,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
    model_id: int | None = Query(None, alias="modelId", ge=1),
    provider_id: int | None = Query(None, alias="providerId", ge=1),
    benchmark_id: int | None = Query(None, alias="benchmarkId", ge=1),
    version_id: int | None = Query(None, alias="versionId", ge=1),
    protocol_id: int | None = Query(None, alias="protocolId", ge=1),
    evaluator_id: int | None = Query(None, alias="evaluatorId", ge=1),
    snapshot_id: int | None = Query(None, alias="snapshotId", ge=1),
    q: str | None = Query(None, min_length=1, max_length=200),
    db: Session = Depends(get_db),
) -> CatalogObservationPage:
    _reject_unknown(
        request,
        {
            "limit",
            "cursor",
            "modelId",
            "providerId",
            "benchmarkId",
            "versionId",
            "protocolId",
            "evaluatorId",
            "snapshotId",
            "q",
        },
    )
    return _cursor_call(
        queries.list_observations,
        db=db,
        limit=limit,
        cursor=cursor,
        model_id=model_id,
        provider_id=provider_id,
        benchmark_id=benchmark_id,
        version_id=version_id,
        protocol_id=protocol_id,
        evaluator_id=evaluator_id,
        snapshot_id=snapshot_id,
        q=q,
    )


@router.get("/observations/{observation_id}", response_model=CatalogObservationOut)
def observation_detail(
    observation_id: int, db: Session = Depends(get_db)
) -> CatalogObservationOut:
    result = queries.get_observation(db, observation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Catalog observation not found")
    return result


@router.get("/search", response_model=CatalogSearchPage)
def search(
    request: Request,
    type_: Literal["model", "benchmark", "provider"] = Query(alias="type"),
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
    db: Session = Depends(get_db),
) -> CatalogSearchPage:
    _reject_unknown(request, {"type", "q", "limit", "cursor"})
    return _cursor_call(
        queries.search_catalog,
        db=db,
        resource_type=type_,
        q=q,
        limit=limit,
        cursor=cursor,
    )
