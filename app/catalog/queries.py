"""Read-only public catalog queries, independent of runtime enablement."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.catalog.pagination import CatalogCursorError, decode_cursor, encode_cursor
from app.models import (
    CatalogBenchmarkFamily,
    CatalogBenchmarkVersion,
    CatalogEvaluator,
    CatalogMetricDefinition,
    CatalogModel,
    CatalogModelAlias,
    CatalogObservation,
    CatalogObservationMetric,
    CatalogProtocol,
    CatalogProvider,
    CatalogProviderDeployment,
    CatalogSource,
    CatalogSourceSnapshot,
    CatalogTaskBenchmark,
)
from app.schemas.catalog import (
    CatalogBenchmarkDetailOut,
    CatalogBenchmarkOut,
    CatalogBenchmarkPage,
    CatalogBenchmarkVersionOut,
    CatalogMetricValueOut,
    CatalogModelDetailOut,
    CatalogModelOut,
    CatalogModelPage,
    CatalogObservationOut,
    CatalogObservationPage,
    CatalogPageInfo,
    CatalogProtocolOut,
    CatalogProviderDeploymentOut,
    CatalogProviderDetailOut,
    CatalogProviderOut,
    CatalogProviderPage,
    CatalogSearchItemOut,
    CatalogSearchPage,
)

SearchType = Literal["model", "benchmark", "provider"]


def _normalise_query(q: str | None) -> str | None:
    if q is None:
        return None
    value = q.strip().lower()
    return value or None


def _like(value: str, *, prefix: bool = False) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%" if prefix else f"%{escaped}%"


def _contains(column, value: str):
    return column.ilike(_like(value), escape="\\")


def _prefix(column, value: str):
    return column.ilike(_like(value, prefix=True), escape="\\")


def _named_page(
    db: Session,
    *,
    entity,
    resource: str,
    limit: int,
    cursor: str | None,
    q: str | None,
    conditions: list,
):
    q = _normalise_query(q)
    parameters = {"q": q}
    order = "normalized-name,id"
    name_key = func.lower(entity.name).collate("C")
    stmt = select(entity, name_key.label("sort_name")).where(*conditions)
    if cursor:
        last_name, last_id = decode_cursor(
            cursor,
            resource=resource,
            parameters=parameters,
            order=order,
            last_size=2,
        )
        if not isinstance(last_name, str) or not isinstance(last_id, int):
            raise CatalogCursorError("Invalid catalog cursor position")
        stmt = stmt.where(
            or_(name_key > last_name, (name_key == last_name) & (entity.id > last_id))
        )
    pairs = db.execute(stmt.order_by(name_key, entity.id).limit(limit + 1)).all()
    has_more = len(pairs) > limit
    pairs = pairs[:limit]
    rows = [row for row, _sort_name in pairs]
    next_cursor = None
    if has_more:
        last, last_name = pairs[-1]
        next_cursor = encode_cursor(
            resource=resource,
            parameters=parameters,
            order=order,
            last=[last_name, last.id],
        )
    return rows, CatalogPageInfo(limit=limit, next_cursor=next_cursor, has_more=has_more)


def _model_related_match(model, q: str, *, prefix: bool = False, exact: bool = False):
    def predicate(column):
        if exact:
            return func.lower(column) == q
        return _prefix(column, q) if prefix else _contains(column, q)

    alias_match = exists(
        select(CatalogModelAlias.id).where(
            CatalogModelAlias.catalog_model_id == model.id,
            CatalogModelAlias.resolution_status == "resolved",
            predicate(CatalogModelAlias.normalized_label),
        )
    )
    provider_match = exists(
        select(CatalogProviderDeployment.id)
        .join(CatalogProvider, CatalogProvider.id == CatalogProviderDeployment.provider_id)
        .where(
            CatalogProviderDeployment.model_id == model.id,
            or_(
                predicate(CatalogProvider.name),
                predicate(CatalogProviderDeployment.name),
                predicate(CatalogProviderDeployment.deployment_key),
            ),
        )
    )
    return alias_match, provider_match


def _model_match(model, q: str):
    alias_match, provider_match = _model_related_match(model, q)
    return or_(
        _contains(model.name, q),
        _contains(model.slug, q),
        _contains(model.organization, q),
        alias_match,
        provider_match,
    )


def list_models(
    db: Session, *, limit: int, cursor: str | None = None, q: str | None = None
) -> CatalogModelPage:
    normalized = _normalise_query(q)
    conditions = [_model_match(CatalogModel, normalized)] if normalized else []
    rows, page_info = _named_page(
        db,
        entity=CatalogModel,
        resource="models",
        limit=limit,
        cursor=cursor,
        q=normalized,
        conditions=conditions,
    )
    return CatalogModelPage(
        items=[
            CatalogModelOut(
                id=row.id, slug=row.slug, name=row.name, organization=row.organization
            )
            for row in rows
        ],
        page_info=page_info,
    )


def get_model(db: Session, model_id: int) -> CatalogModelDetailOut | None:
    model = db.get(CatalogModel, model_id)
    if model is None:
        return None
    aliases = db.scalars(
        select(CatalogModelAlias.source_label)
        .where(
            CatalogModelAlias.catalog_model_id == model.id,
            CatalogModelAlias.resolution_status == "resolved",
        )
        .order_by(func.lower(CatalogModelAlias.source_label), CatalogModelAlias.id)
    ).all()
    deployments = db.execute(
        select(CatalogProviderDeployment, CatalogProvider.name)
        .join(CatalogProvider, CatalogProvider.id == CatalogProviderDeployment.provider_id)
        .where(CatalogProviderDeployment.model_id == model.id)
        .order_by(func.lower(CatalogProvider.name), CatalogProviderDeployment.id)
    ).all()
    return CatalogModelDetailOut(
        id=model.id,
        slug=model.slug,
        name=model.name,
        organization=model.organization,
        description=model.description,
        aliases=list(aliases),
        deployments=[_deployment_out(row, provider_name) for row, provider_name in deployments],
    )


def list_providers(
    db: Session, *, limit: int, cursor: str | None = None, q: str | None = None
) -> CatalogProviderPage:
    normalized = _normalise_query(q)
    conditions = []
    if normalized:
        conditions.append(
            or_(
                _contains(CatalogProvider.name, normalized),
                _contains(CatalogProvider.slug, normalized),
            )
        )
    rows, page_info = _named_page(
        db,
        entity=CatalogProvider,
        resource="providers",
        limit=limit,
        cursor=cursor,
        q=normalized,
        conditions=conditions,
    )
    return CatalogProviderPage(
        items=[CatalogProviderOut(id=row.id, slug=row.slug, name=row.name) for row in rows],
        page_info=page_info,
    )


def _deployment_out(
    deployment: CatalogProviderDeployment, provider_name: str
) -> CatalogProviderDeploymentOut:
    return CatalogProviderDeploymentOut(
        id=deployment.id,
        provider_id=deployment.provider_id,
        provider_name=provider_name,
        model_id=deployment.model_id,
        deployment_key=deployment.deployment_key,
        name=deployment.name,
        variant=deployment.variant,
    )


def get_provider(db: Session, provider_id: int) -> CatalogProviderDetailOut | None:
    provider = db.get(CatalogProvider, provider_id)
    if provider is None:
        return None
    deployments = db.scalars(
        select(CatalogProviderDeployment)
        .where(CatalogProviderDeployment.provider_id == provider.id)
        .order_by(func.lower(CatalogProviderDeployment.name), CatalogProviderDeployment.id)
    ).all()
    return CatalogProviderDetailOut(
        id=provider.id,
        slug=provider.slug,
        name=provider.name,
        description=provider.description,
        url=provider.url,
        deployments=[_deployment_out(row, provider.name) for row in deployments],
    )


def list_benchmarks(
    db: Session, *, limit: int, cursor: str | None = None, q: str | None = None
) -> CatalogBenchmarkPage:
    normalized = _normalise_query(q)
    conditions = []
    if normalized:
        conditions.append(
            or_(
                _contains(CatalogBenchmarkFamily.name, normalized),
                _contains(CatalogBenchmarkFamily.slug, normalized),
                _contains(CatalogBenchmarkFamily.description, normalized),
            )
        )
    rows, page_info = _named_page(
        db,
        entity=CatalogBenchmarkFamily,
        resource="benchmarks",
        limit=limit,
        cursor=cursor,
        q=normalized,
        conditions=conditions,
    )
    return CatalogBenchmarkPage(
        items=[_benchmark_out(row) for row in rows], page_info=page_info
    )


def _benchmark_out(row: CatalogBenchmarkFamily) -> CatalogBenchmarkOut:
    return CatalogBenchmarkOut(
        id=row.id,
        slug=row.slug,
        name=row.name,
        description=row.description,
        tooltip=row.tooltip,
        methodology_url=row.methodology_url,
        limitations=row.limitations,
    )


def get_benchmark(db: Session, benchmark_id: int) -> CatalogBenchmarkDetailOut | None:
    family = db.get(CatalogBenchmarkFamily, benchmark_id)
    if family is None:
        return None
    versions = db.scalars(
        select(CatalogBenchmarkVersion)
        .where(CatalogBenchmarkVersion.benchmark_family_id == family.id)
        .order_by(CatalogBenchmarkVersion.release_date, CatalogBenchmarkVersion.id)
    ).all()
    protocols_by_version: dict[int, list[CatalogProtocol]] = defaultdict(list)
    if versions:
        for protocol in db.scalars(
            select(CatalogProtocol)
            .where(CatalogProtocol.benchmark_version_id.in_([row.id for row in versions]))
            .order_by(CatalogProtocol.benchmark_version_id, CatalogProtocol.id)
        ):
            protocols_by_version[protocol.benchmark_version_id].append(protocol)
    task_types = db.scalars(
        select(CatalogTaskBenchmark.task_type)
        .where(CatalogTaskBenchmark.benchmark_family_id == family.id)
        .order_by(CatalogTaskBenchmark.task_type)
    ).all()
    base = _benchmark_out(family)
    return CatalogBenchmarkDetailOut(
        **base.model_dump(),
        versions=[
            CatalogBenchmarkVersionOut(
                id=version.id,
                version=version.version,
                release_date=version.release_date,
                description=version.description,
                methodology=version.methodology,
                methodology_url=version.methodology_url,
                protocols=[
                    CatalogProtocolOut(
                        id=protocol.id,
                        name=protocol.name,
                        runner=protocol.runner,
                        runner_version=protocol.runner_version,
                        methodology=protocol.methodology,
                        configuration=protocol.configuration,
                    )
                    for protocol in protocols_by_version[version.id]
                ],
            )
            for version in versions
        ],
        task_types=list(task_types),
    )


def list_observations(
    db: Session,
    *,
    limit: int,
    cursor: str | None = None,
    model_id: int | None = None,
    provider_id: int | None = None,
    benchmark_id: int | None = None,
    version_id: int | None = None,
    protocol_id: int | None = None,
    evaluator_id: int | None = None,
    snapshot_id: int | None = None,
) -> CatalogObservationPage:
    parameters = {
        "modelId": model_id,
        "providerId": provider_id,
        "benchmarkId": benchmark_id,
        "versionId": version_id,
        "protocolId": protocol_id,
        "evaluatorId": evaluator_id,
        "snapshotId": snapshot_id,
    }
    order = "observation-id"
    stmt = select(CatalogObservation)
    for column, value in (
        (CatalogObservation.catalog_model_id, model_id),
        (CatalogObservation.benchmark_family_id, benchmark_id),
        (CatalogObservation.benchmark_version_id, version_id),
        (CatalogObservation.protocol_id, protocol_id),
        (CatalogObservation.evaluator_id, evaluator_id),
        (CatalogObservation.source_snapshot_id, snapshot_id),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    if provider_id is not None:
        stmt = stmt.where(
            CatalogObservation.provider_deployment_id.in_(
                select(CatalogProviderDeployment.id).where(
                    CatalogProviderDeployment.provider_id == provider_id
                )
            )
        )
    if cursor:
        [last_id] = decode_cursor(
            cursor,
            resource="observations",
            parameters=parameters,
            order=order,
            last_size=1,
        )
        if not isinstance(last_id, int):
            raise CatalogCursorError("Invalid catalog cursor position")
        stmt = stmt.where(CatalogObservation.id > last_id)
    rows = db.scalars(stmt.order_by(CatalogObservation.id).limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more:
        next_cursor = encode_cursor(
            resource="observations",
            parameters=parameters,
            order=order,
            last=[rows[-1].id],
        )
    return CatalogObservationPage(
        items=_observation_outs(db, rows),
        page_info=CatalogPageInfo(
            limit=limit, next_cursor=next_cursor, has_more=has_more
        ),
    )


def get_observation(db: Session, observation_id: int) -> CatalogObservationOut | None:
    observation = db.get(CatalogObservation, observation_id)
    return _observation_outs(db, [observation])[0] if observation else None


def _map_by_id(db: Session, entity, ids: set[int | None]):
    real_ids = {value for value in ids if value is not None}
    if not real_ids:
        return {}
    return {row.id: row for row in db.scalars(select(entity).where(entity.id.in_(real_ids)))}


def _observation_outs(
    db: Session, observations: list[CatalogObservation]
) -> list[CatalogObservationOut]:
    if not observations:
        return []
    families = _map_by_id(db, CatalogBenchmarkFamily, {o.benchmark_family_id for o in observations})
    versions = _map_by_id(db, CatalogBenchmarkVersion, {o.benchmark_version_id for o in observations})
    protocols = _map_by_id(db, CatalogProtocol, {o.protocol_id for o in observations})
    evaluators = _map_by_id(db, CatalogEvaluator, {o.evaluator_id for o in observations})
    snapshots = _map_by_id(db, CatalogSourceSnapshot, {o.source_snapshot_id for o in observations})
    sources = _map_by_id(db, CatalogSource, {s.source_id for s in snapshots.values()})
    models = _map_by_id(db, CatalogModel, {o.catalog_model_id for o in observations})
    deployments = _map_by_id(
        db, CatalogProviderDeployment, {o.provider_deployment_id for o in observations}
    )
    providers = _map_by_id(db, CatalogProvider, {d.provider_id for d in deployments.values()})

    metric_rows = db.execute(
        select(CatalogObservationMetric, CatalogMetricDefinition)
        .join(
            CatalogMetricDefinition,
            CatalogMetricDefinition.id == CatalogObservationMetric.metric_definition_id,
        )
        .where(CatalogObservationMetric.observation_id.in_([o.id for o in observations]))
        .order_by(CatalogObservationMetric.observation_id, CatalogObservationMetric.id)
    ).all()
    metrics: dict[int, list[CatalogMetricValueOut]] = defaultdict(list)
    for value, definition in metric_rows:
        metrics[value.observation_id].append(
            CatalogMetricValueOut(
                metric_id=definition.id,
                key=definition.key,
                name=definition.name,
                description=definition.description,
                unit=definition.unit,
                direction=definition.direction,
                value=value.value,
                reported_value=value.reported_value,
                missing_reason=value.missing_reason,
                category=value.category,
                subset=value.subset,
                aggregation=value.aggregation,
                confidence_low=value.confidence_low,
                confidence_high=value.confidence_high,
                confidence_level=value.confidence_level,
                uncertainty_type=value.uncertainty_type,
                sample_size=value.sample_size,
                denominator=value.denominator,
                attempts=value.attempts,
            )
        )

    output: list[CatalogObservationOut] = []
    for observation in observations:
        version = versions.get(observation.benchmark_version_id)
        protocol = protocols.get(observation.protocol_id)
        evaluator = evaluators.get(observation.evaluator_id)
        snapshot = snapshots.get(observation.source_snapshot_id)
        source = sources.get(snapshot.source_id) if snapshot else None
        model = models.get(observation.catalog_model_id)
        deployment = deployments.get(observation.provider_deployment_id)
        provider = providers.get(deployment.provider_id) if deployment else None
        output.append(
            CatalogObservationOut(
                id=observation.id,
                benchmark_id=observation.benchmark_family_id,
                benchmark_name=families[observation.benchmark_family_id].name,
                version_id=version.id if version else None,
                version=version.version if version else None,
                protocol_id=protocol.id if protocol else None,
                protocol=protocol.name if protocol else None,
                evaluator_id=evaluator.id if evaluator else None,
                evaluator=evaluator.name if evaluator else None,
                source_snapshot_id=snapshot.id if snapshot else None,
                source_name=source.name if source else None,
                source_url=snapshot.artifact_uri if snapshot else observation.source_url,
                source_content_hash=snapshot.content_hash if snapshot else None,
                source_fetched_at=snapshot.fetched_at if snapshot else None,
                source_publication_date=snapshot.publication_date if snapshot else None,
                source_model_label=observation.source_model_label,
                model_id=model.id if model else None,
                model_name=model.name if model else None,
                provider_deployment_id=deployment.id if deployment else None,
                provider_id=provider.id if provider else None,
                provider_name=provider.name if provider else None,
                origin=observation.origin,
                provenance_status=observation.provenance_status,
                task_type=observation.task_type,
                context_window=observation.context_window,
                reported_cost_per_mtok=observation.reported_cost_per_mtok,
                observed_at=observation.observed_at,
                metrics=metrics[observation.id],
            )
        )
    return output


def search_catalog(
    db: Session,
    *,
    resource_type: SearchType,
    q: str,
    limit: int,
    cursor: str | None = None,
) -> CatalogSearchPage:
    normalized = _normalise_query(q)
    if normalized is None:
        raise ValueError("Search query must contain a non-space character")
    entity = {
        "model": CatalogModel,
        "benchmark": CatalogBenchmarkFamily,
        "provider": CatalogProvider,
    }[resource_type]
    lower_name = func.lower(entity.name).collate("C")
    if resource_type == "model":
        match = _model_match(CatalogModel, normalized)
        exact_alias, exact_provider = _model_related_match(
            CatalogModel, normalized, exact=True
        )
        prefix_alias, prefix_provider = _model_related_match(
            CatalogModel, normalized, prefix=True
        )
        exact = or_(
            func.lower(entity.name) == normalized,
            func.lower(entity.slug) == normalized,
            func.lower(entity.organization) == normalized,
            exact_alias,
            exact_provider,
        )
        prefix = or_(
            _prefix(entity.name, normalized),
            _prefix(entity.slug, normalized),
            _prefix(entity.organization, normalized),
            prefix_alias,
            prefix_provider,
        )
    else:
        match = or_(
            _contains(entity.name, normalized),
            _contains(entity.slug, normalized),
        )
        exact = or_(func.lower(entity.name) == normalized, func.lower(entity.slug) == normalized)
        prefix = or_(
            _prefix(entity.name, normalized),
            _prefix(entity.slug, normalized),
        )
    rank = case((exact, 0), (prefix, 1), else_=2)
    parameters = {"type": resource_type, "q": normalized}
    order = "match-rank,normalized-name,id"
    stmt = select(
        entity, rank.label("match_rank"), lower_name.label("sort_name")
    ).where(match)
    if cursor:
        last_rank, last_name, last_id = decode_cursor(
            cursor,
            resource="search",
            parameters=parameters,
            order=order,
            last_size=3,
        )
        if not isinstance(last_rank, int) or not isinstance(last_name, str) or not isinstance(last_id, int):
            raise CatalogCursorError("Invalid catalog cursor position")
        stmt = stmt.where(
            or_(
                rank > last_rank,
                (rank == last_rank) & (lower_name > last_name),
                (rank == last_rank) & (lower_name == last_name) & (entity.id > last_id),
            )
        )
    rows = db.execute(stmt.order_by(rank, lower_name, entity.id).limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more:
        last_entity, last_rank, last_name = rows[-1]
        next_cursor = encode_cursor(
            resource="search",
            parameters=parameters,
            order=order,
            last=[last_rank, last_name, last_entity.id],
        )
    return CatalogSearchPage(
        items=[
            CatalogSearchItemOut(
                type=resource_type,
                id=row.id,
                name=row.name,
                subtitle=(row.organization if resource_type == "model" else row.description),
            )
            for row, _rank, _sort_name in rows
        ],
        page_info=CatalogPageInfo(
            limit=limit, next_cursor=next_cursor, has_more=has_more
        ),
    )
