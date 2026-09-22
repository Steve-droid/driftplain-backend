"""Catalog contract schemas (used from S5 / S5b).

These ride the deterministic recommender's data; `task_type` / `metric` are open
strings (the ingestion path #3 grows them), matching the schema decision in S2.
Field names are snake_case in code and serialize as camelCase on the wire.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from app.schemas.base import CamelModel

DataPolicy = Literal["trains_on_input", "private"]


class ModelCreate(CamelModel):
    name: str
    vendor: str
    # price_per_mtok = LEGACY blended display/ranking price. input/output = S12 split
    # pricing the savings engine uses (real APIs price input vs output differently).
    price_per_mtok: Optional[Decimal] = None
    input_price_per_mtok: Optional[Decimal] = None
    output_price_per_mtok: Optional[Decimal] = None
    data_policy: Optional[DataPolicy] = None


class ModelOut(CamelModel):
    id: int
    name: str
    vendor: str
    price_per_mtok: Optional[Decimal] = None
    input_price_per_mtok: Optional[Decimal] = None
    output_price_per_mtok: Optional[Decimal] = None
    data_policy: Optional[DataPolicy] = None


class HarnessOut(CamelModel):
    id: int
    name: str
    vendor: Optional[str] = None


class BenchmarkOut(CamelModel):
    id: int
    name: str
    task_type: Optional[str] = None
    as_of: Optional[date] = None
    notes: Optional[str] = None


class BenchmarkResultCreate(CamelModel):
    model_id: int
    benchmark_id: int
    harness_id: Optional[int] = None
    task_type: Optional[str] = None
    score: Optional[Decimal] = None
    metric: Optional[str] = None
    cost_per_mtok: Optional[Decimal] = None
    context_window: Optional[int] = None
    source: Optional[str] = None
    source_document_id: Optional[int] = None
    measured_at: Optional[date] = None


class BenchmarkResultOut(BenchmarkResultCreate):
    id: int


class CatalogRowIn(CamelModel):
    """A denormalized catalog row (what /benchmarks and the seed accept).

    The service resolves the name fields to model/benchmark/harness rows
    (get-or-create) and upserts the benchmark_result. Mirrors the seed format.
    """

    model: str
    vendor: str
    benchmark: str
    metric: str
    score: Decimal
    # cost_per_mtok = the legacy blended price (recommender ranking + display); REQUIRED.
    # The split input/output prices (S12 savings) are OPTIONAL — when absent the upsert
    # backfills both from cost_per_mtok, so old/partial rows stay costable.
    cost_per_mtok: Decimal
    input_price_per_mtok: Optional[Decimal] = None
    output_price_per_mtok: Optional[Decimal] = None
    harness: Optional[str] = None
    harness_vendor: Optional[str] = None
    task_type: Optional[str] = None
    context_window: Optional[int] = None
    source: Optional[str] = None
    measured_at: Optional[date] = None
    # Benchmark-level provenance (P38c). Set on the BENCHMARK, not the row, but
    # accepted/returned per row because the catalog's wire shape is denormalized:
    # `as_of` dates the whole benchmark's figures, `notes` says what changed since.
    # Any upsert path (seed, ingest, POST /benchmarks) can therefore date its source.
    benchmark_as_of: Optional[date] = None
    benchmark_notes: Optional[str] = None


class CatalogRowOut(CatalogRowIn):
    id: int  # benchmark_result id


# B2 public catalog schemas are deliberately separate from the authenticated
# legacy /benchmarks contract above. They contain evidence metadata only.
CatalogResourceType = Literal["model", "benchmark", "provider"]
MetricDirection = Literal["higher", "lower", "non_ranking"]


class CatalogPageInfo(CamelModel):
    limit: int
    next_cursor: Optional[str] = None
    has_more: bool


class CatalogModelOut(CamelModel):
    id: int
    slug: str
    name: str
    organization: Optional[str] = None


class CatalogProviderOut(CamelModel):
    id: int
    slug: str
    name: str


class CatalogProviderDeploymentOut(CamelModel):
    id: int
    provider_id: int
    provider_name: str
    model_id: int
    deployment_key: str
    name: str
    variant: Optional[str] = None


class CatalogModelDetailOut(CatalogModelOut):
    description: Optional[str] = None
    aliases: list[str]
    deployments: list[CatalogProviderDeploymentOut]


class CatalogProviderDetailOut(CatalogProviderOut):
    description: Optional[str] = None
    url: Optional[str] = None
    deployments: list[CatalogProviderDeploymentOut]


class CatalogProtocolOut(CamelModel):
    id: int
    name: str
    runner: Optional[str] = None
    runner_version: Optional[str] = None
    methodology: Optional[str] = None
    configuration: dict


class CatalogBenchmarkVersionOut(CamelModel):
    id: int
    version: str
    release_date: Optional[date] = None
    description: Optional[str] = None
    methodology: Optional[str] = None
    methodology_url: Optional[str] = None
    protocols: list[CatalogProtocolOut]


class CatalogBenchmarkOut(CamelModel):
    id: int
    slug: str
    name: str
    description: Optional[str] = None
    tooltip: Optional[str] = None
    methodology_url: Optional[str] = None
    limitations: Optional[str] = None


class CatalogBenchmarkDetailOut(CatalogBenchmarkOut):
    versions: list[CatalogBenchmarkVersionOut]
    task_types: list[str]


class CatalogMetricValueOut(CamelModel):
    metric_id: int
    key: str
    name: str
    description: Optional[str] = None
    unit: Optional[str] = None
    direction: Optional[MetricDirection] = None
    value: Optional[Decimal] = None
    reported_value: Optional[str] = None
    missing_reason: Optional[str] = None
    category: Optional[str] = None
    subset: Optional[str] = None
    aggregation: Optional[str] = None
    confidence_low: Optional[Decimal] = None
    confidence_high: Optional[Decimal] = None
    confidence_level: Optional[Decimal] = None
    uncertainty_type: Optional[str] = None
    sample_size: Optional[int] = None
    denominator: Optional[int] = None
    attempts: Optional[int] = None


class CatalogObservationOut(CamelModel):
    id: int
    benchmark_id: int
    benchmark_name: str
    version_id: Optional[int] = None
    version: Optional[str] = None
    protocol_id: Optional[int] = None
    protocol: Optional[str] = None
    evaluator_id: Optional[int] = None
    evaluator: Optional[str] = None
    source_snapshot_id: Optional[int] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    source_content_hash: Optional[str] = None
    source_fetched_at: Optional[datetime] = None
    source_publication_date: Optional[date] = None
    source_model_label: str
    model_id: Optional[int] = None
    model_name: Optional[str] = None
    provider_deployment_id: Optional[int] = None
    provider_id: Optional[int] = None
    provider_name: Optional[str] = None
    origin: Literal["source", "legacy_backfill"]
    provenance_status: Literal["complete", "incomplete"]
    task_type: Optional[str] = None
    context_window: Optional[int] = None
    reported_cost_per_mtok: Optional[Decimal] = None
    observed_at: Optional[date] = None
    metrics: list[CatalogMetricValueOut]


class CatalogSearchItemOut(CamelModel):
    type: CatalogResourceType
    id: int
    name: str
    subtitle: Optional[str] = None


class CatalogModelPage(CamelModel):
    items: list[CatalogModelOut]
    page_info: CatalogPageInfo


class CatalogProviderPage(CamelModel):
    items: list[CatalogProviderOut]
    page_info: CatalogPageInfo


class CatalogBenchmarkPage(CamelModel):
    items: list[CatalogBenchmarkOut]
    page_info: CatalogPageInfo


class CatalogObservationPage(CamelModel):
    items: list[CatalogObservationOut]
    page_info: CatalogPageInfo


class CatalogSearchPage(CamelModel):
    items: list[CatalogSearchItemOut]
    page_info: CatalogPageInfo
