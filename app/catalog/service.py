"""Catalog store: turn denormalized rows into normalized, idempotent catalog data.

A catalog row (model/vendor + benchmark + optional harness + the metric figures)
is split across the dimension tables (get-or-create) and the benchmark_result
fact (upsert). Re-loading the same row is a no-op on identity and refreshes the
mutable figures — the idempotency the seed and S5b ingestion rely on.

The LLM never runs here: this is plain SQL. The LLM only *fills* the catalog
(S5b ingestion); ranking over it stays deterministic (S6).
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from hashlib import md5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    Benchmark,
    BenchmarkResult,
    CatalogBenchmarkFamily,
    CatalogMetricDefinition,
    CatalogModel,
    CatalogObservation,
    CatalogObservationMetric,
    Harness,
    Model,
)
from app.schemas.catalog import CatalogRowIn, CatalogRowOut


class TaskBenchmarkConflict(Exception):
    """Raised when a row would give a task type a SECOND (benchmark, metric) pair.

    The recommender compares rows only within one (benchmark, metric) group, so a
    task type owning two groups has no well-defined ranking (P38c replaced the old
    "most rows wins" vote with this hard rule). Rejecting at write time means the
    bad state is never created — by the seed, the ingest scripts, or POST /benchmarks.
    """

    def __init__(
        self,
        task_type: str,
        existing: tuple[str, str],
        incoming: tuple[str, str],
    ) -> None:
        self.task_type = task_type
        self.existing = existing
        self.incoming = incoming
        super().__init__(
            f"Task type {task_type!r} is already measured by "
            f"{existing[0]!r} · {existing[1]!r}; refusing to add "
            f"{incoming[0]!r} · {incoming[1]!r}. One benchmark and one metric per "
            "task type — scores from different benchmarks are not comparable."
        )

# Ranking/display cost is blended 3:1 (input:output) — a code-review workload reads a
# large diff and emits compact findings. Quantized to the cost column's 6-dp scale.
_BLEND_QUANT = Decimal("0.000001")


def _ranking_cost(row: CatalogRowIn) -> Decimal | None:
    """The deterministic ranking/display `cost_per_mtok` for a row.

    When BOTH split prices are present we DERIVE it as `(3*input + output)/4` — the
    same formula the seed documents — so seeded and ingested rows share one cost basis
    and the LLM never does ranking arithmetic. Otherwise we fall back to the row's
    provided blended `cost_per_mtok`.
    """
    if row.input_price_per_mtok is not None and row.output_price_per_mtok is not None:
        blended = (
            Decimal(3) * row.input_price_per_mtok + row.output_price_per_mtok
        ) / Decimal(4)
        return blended.quantize(_BLEND_QUANT, rounding=ROUND_HALF_UP)
    return row.cost_per_mtok


def get_or_create_model(db: Session, name: str, vendor: str) -> Model:
    obj = db.scalar(select(Model).where(Model.name == name, Model.vendor == vendor))
    if obj is None:
        obj = Model(name=name, vendor=vendor)
        db.add(obj)
        db.flush()
    return obj


def get_or_create_benchmark(
    db: Session,
    name: str,
    task_type: str | None,
    *,
    as_of: date | None = None,
    notes: str | None = None,
) -> Benchmark:
    """Get-or-create the benchmark dimension, refreshing its provenance (P38c).

    `as_of` / `notes` are benchmark-level facts that arrive on each denormalized row.
    They are refreshed last-write-wins when supplied and left alone when omitted, so
    a partial upsert (e.g. POST /benchmarks with no date) never erases a date the
    seed or an ingest already recorded.
    """
    obj = db.scalar(select(Benchmark).where(Benchmark.name == name))
    if obj is None:
        obj = Benchmark(name=name, task_type=task_type, as_of=as_of, notes=notes)
        db.add(obj)
        db.flush()
        return obj
    if as_of is not None and obj.as_of != as_of:
        obj.as_of = as_of
    if notes is not None and obj.notes != notes:
        obj.notes = notes
    return obj


def get_or_create_harness(
    db: Session, name: str | None, vendor: str | None
) -> Harness | None:
    if name is None:
        return None
    # (name, vendor) natural key; `== None` becomes `IS NULL` in SQLAlchemy.
    obj = db.scalar(
        select(Harness).where(Harness.name == name, Harness.vendor == vendor)
    )
    if obj is None:
        obj = Harness(name=name, vendor=vendor)
        db.add(obj)
        db.flush()
    return obj


def _assert_task_owns_one_group(db: Session, row: CatalogRowIn) -> None:
    """Enforce the P38c invariant: one (benchmark, metric) pair per task type.

    Untyped rows (task_type IS NULL) belong to no task and are never rankable, so
    they are exempt. Checked BEFORE anything is written, so a rejected row leaves no
    partial dimension rows behind.
    """
    if not row.task_type:
        return
    existing = db.execute(
        select(Benchmark.name, BenchmarkResult.metric)
        .join(Benchmark, BenchmarkResult.benchmark_id == Benchmark.id)
        .where(BenchmarkResult.task_type == row.task_type)
        .limit(1)
    ).first()
    if existing is None:
        return
    if (existing[0], existing[1]) != (row.benchmark, row.metric):
        raise TaskBenchmarkConflict(
            row.task_type, (existing[0], existing[1]), (row.benchmark, row.metric)
        )


def upsert_catalog_row(
    db: Session, row: CatalogRowIn, *, source_document_id: int | None = None
) -> CatalogRowOut:
    """Idempotent insert-or-update of one catalog row; returns the stored row.

    `source_document_id` (S5b ingestion) links the row to the source it came from.
    It's NOT part of the row's identity, so two sources stating the same figure still
    dedupe to one row — the latest ingest's provenance wins. A provenance-less upsert
    (seed / `POST /benchmarks`, id=None) never clears an existing link.
    """
    _assert_task_owns_one_group(db, row)
    model = get_or_create_model(db, row.model, row.vendor)
    # Ranking/display blended price (NOT read by the S12 savings engine). DERIVED from
    # the split prices when present (never trusted from the LLM), so seed + ingested
    # rows rank on the same basis. Last-write-wins; the `!=` guard keeps an unchanged
    # re-upsert (seed/ingestion idempotency) from dirtying the row.
    ranking_cost = _ranking_cost(row)
    if ranking_cost is not None and model.price_per_mtok != ranking_cost:
        model.price_per_mtok = ranking_cost
    # S12 split pricing (authoritative for savings): use the row's explicit input/output
    # prices when present; otherwise BACKFILL both from the legacy blended cost_per_mtok
    # so old/partial rows stay costable (input==output==blended reproduces the old math).
    in_price = row.input_price_per_mtok if row.input_price_per_mtok is not None else row.cost_per_mtok
    out_price = row.output_price_per_mtok if row.output_price_per_mtok is not None else row.cost_per_mtok
    if in_price is not None and model.input_price_per_mtok != in_price:
        model.input_price_per_mtok = in_price
    if out_price is not None and model.output_price_per_mtok != out_price:
        model.output_price_per_mtok = out_price
    benchmark = get_or_create_benchmark(
        db,
        row.benchmark,
        row.task_type,
        as_of=row.benchmark_as_of,
        notes=row.benchmark_notes,
    )
    harness = get_or_create_harness(db, row.harness, row.harness_vendor)

    mutable = {
        "task_type": row.task_type,
        "score": row.score,
        "cost_per_mtok": ranking_cost,  # derived 3:1 blend, not the raw row value
        "context_window": row.context_window,
        "source": row.source,
        "measured_at": row.measured_at,
    }
    stmt = pg_insert(BenchmarkResult).values(
        model_id=model.id,
        benchmark_id=benchmark.id,
        harness_id=harness.id if harness else None,
        metric=row.metric,
        source_document_id=source_document_id,
        **mutable,
    )
    # ON CONFLICT on the natural-key constraint → refresh the mutable figures only.
    # Provenance is refreshed too, but ONLY when this upsert carries one (else a
    # seed/API re-upsert would null out an earlier ingestion's source link).
    set_cols = {k: stmt.excluded[k] for k in mutable}
    if source_document_id is not None:
        set_cols["source_document_id"] = stmt.excluded["source_document_id"]
    stmt = stmt.on_conflict_do_update(
        constraint="uq_benchmark_result_identity",
        set_=set_cols,
    ).returning(BenchmarkResult.id)
    result_id = db.execute(stmt).scalar_one()
    stored = db.scalar(select(BenchmarkResult).where(BenchmarkResult.id == result_id))
    _mirror_legacy_observation(db, stored, model, benchmark)
    db.commit()

    return CatalogRowOut(
        id=result_id,
        model=model.name,
        vendor=model.vendor,
        benchmark=benchmark.name,
        harness=harness.name if harness else None,
        harness_vendor=harness.vendor if harness else None,
        metric=row.metric,
        input_price_per_mtok=model.input_price_per_mtok,
        output_price_per_mtok=model.output_price_per_mtok,
        benchmark_as_of=benchmark.as_of,
        benchmark_notes=benchmark.notes,
        **mutable,
    )


def _legacy_fingerprint(*values: object | None) -> str:
    """Match PostgreSQL concat_ws + md5 used by the populated-data backfill.

    This is an idempotency fingerprint for a compatibility row, never a claim that
    the row is an immutable upstream source snapshot.
    """
    joined = "|".join(str(value) for value in values if value is not None)
    return md5(joined.encode(), usedforsecurity=False).hexdigest()


def _mirror_legacy_observation(
    db: Session,
    result: BenchmarkResult,
    model: Model,
    benchmark: Benchmark,
) -> None:
    """Append the legacy mutable row to B2 history in the same transaction.

    Repeating identical content is a no-op; changing a score or any preserved
    evidence field creates a new immutable observation.
    """
    catalog_model = db.scalar(
        select(CatalogModel).where(CatalogModel.legacy_model_id == model.id)
    )
    if catalog_model is None:
        catalog_model = CatalogModel(
            slug=f"legacy-model-{model.id}",
            name=model.name,
            organization=model.vendor,
            legacy_model_id=model.id,
        )
        db.add(catalog_model)
        db.flush()

    family = db.scalar(
        select(CatalogBenchmarkFamily).where(
            CatalogBenchmarkFamily.legacy_benchmark_id == benchmark.id
        )
    )
    if family is None:
        family = CatalogBenchmarkFamily(
            slug=f"legacy-benchmark-{benchmark.id}",
            name=benchmark.name,
            description=benchmark.notes,
            legacy_benchmark_id=benchmark.id,
        )
        db.add(family)
        db.flush()
    elif benchmark.notes is not None:
        family.description = benchmark.notes

    metric_key = result.metric or "legacy-score"
    metric = db.scalar(
        select(CatalogMetricDefinition).where(
            CatalogMetricDefinition.benchmark_family_id == family.id,
            CatalogMetricDefinition.benchmark_version_id.is_(None),
            CatalogMetricDefinition.key == metric_key,
        )
    )
    if metric is None:
        metric = CatalogMetricDefinition(
            benchmark_family_id=family.id,
            benchmark_version_id=None,
            key=metric_key,
            name=result.metric or "Legacy score",
        )
        db.add(metric)
        db.flush()

    configuration_fingerprint = _legacy_fingerprint(
        result.harness_id, result.task_type, result.context_window
    )
    record_fingerprint = _legacy_fingerprint(
        result.id,
        result.score,
        result.metric,
        result.cost_per_mtok,
        result.context_window,
        result.source,
        result.source_document_id,
        result.measured_at,
    )
    locator = f"legacy:benchmark_result:{result.id}"
    existing = db.scalar(
        select(CatalogObservation.id).where(
            CatalogObservation.source_snapshot_id.is_(None),
            CatalogObservation.source_record_locator == locator,
            CatalogObservation.configuration_fingerprint == configuration_fingerprint,
            CatalogObservation.record_fingerprint == record_fingerprint,
        )
    )
    if existing is not None:
        return

    observation = CatalogObservation(
        benchmark_family_id=family.id,
        source_record_locator=locator,
        configuration_fingerprint=configuration_fingerprint,
        record_fingerprint=record_fingerprint,
        source_model_label=model.name,
        catalog_model_id=catalog_model.id,
        origin="legacy_backfill",
        provenance_status="incomplete",
        source_url=result.source,
        task_type=result.task_type,
        context_window=result.context_window,
        reported_cost_per_mtok=result.cost_per_mtok,
        observed_at=result.measured_at,
        notes=benchmark.notes,
        legacy_benchmark_result_id=result.id,
        legacy_source_document_id=result.source_document_id,
        legacy_harness_id=result.harness_id,
    )
    db.add(observation)
    db.flush()
    db.add(
        CatalogObservationMetric(
            observation_id=observation.id,
            metric_definition_id=metric.id,
            value=result.score,
            reported_value=str(result.score) if result.score is not None else None,
            missing_reason=(
                "not reported in legacy row" if result.score is None else None
            ),
        )
    )


def list_catalog(db: Session) -> list[CatalogRowOut]:
    """All catalog rows, denormalized back to names for the API/recommender."""
    results = db.scalars(
        select(BenchmarkResult).order_by(BenchmarkResult.id)
    ).all()
    rows: list[CatalogRowOut] = []
    for r in results:
        rows.append(
            CatalogRowOut(
                id=r.id,
                model=r.model.name,
                vendor=r.model.vendor,
                benchmark=r.benchmark.name,
                harness=r.harness.name if r.harness else None,
                harness_vendor=r.harness.vendor if r.harness else None,
                metric=r.metric,
                task_type=r.task_type,
                score=r.score,
                cost_per_mtok=r.cost_per_mtok,
                input_price_per_mtok=r.model.input_price_per_mtok,
                output_price_per_mtok=r.model.output_price_per_mtok,
                context_window=r.context_window,
                source=r.source,
                measured_at=r.measured_at,
                benchmark_as_of=r.benchmark.as_of,
                benchmark_notes=r.benchmark.notes,
            )
        )
    return rows
