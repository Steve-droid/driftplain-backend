"""Persistence-free, bounded candidate contract. Unknown semantics stay explicit."""

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Text = Annotated[str, Field(min_length=1, max_length=1024, pattern=r"\S")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Review(Record):
    reviewer: Text
    reviewed_at: date
    note: Text


class Citation(Record):
    url: Text
    locator: Text
    content_hash: Hash

    @field_validator("url")
    @classmethod
    def https_url(cls, value):
        u = urlsplit(value)
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            raise ValueError("citation requires a public HTTPS URL")
        return value


class Mapping(Record):
    model_slug: Annotated[str, Field(min_length=1, max_length=255)]
    review: Review
    citation: Citation


class Metric(Record):
    key: Annotated[str, Field(min_length=1, max_length=128)]
    value: Decimal | None
    unit: Literal["percent", "ratio", "USD", "tokens", "steps"] = "percent"
    direction: Literal["higher", "lower"] = "higher"
    missing_reason: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    reported_value: Annotated[str, Field(max_length=255)] | None = None
    confidence_low: Decimal | None = None
    confidence_high: Decimal | None = None
    confidence_level: Decimal | None = None
    denominator: Annotated[int, Field(strict=True, gt=0)] | None = None
    attempts: Annotated[int, Field(strict=True, gt=0)] | None = None

    @field_validator(
        "value", "confidence_low", "confidence_high", "confidence_level", mode="before"
    )
    @classmethod
    def real_number(cls, value):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal))
        ):
            raise ValueError("numeric metric required")
        return value

    @model_validator(mode="after")
    def valid(self):
        if (self.value is None) != (self.missing_reason is not None):
            raise ValueError("missing metric requires exactly one missing reason")
        for value in (self.value, self.confidence_low, self.confidence_high):
            if value is not None and (
                not value.is_finite()
                or not 0
                <= value
                <= (
                    100
                    if self.unit == "percent"
                    else 1
                    if self.unit == "ratio"
                    else Decimal("999999999999.99999999")
                )
            ):
                raise ValueError("metric outside finite unit domain")
        interval = (self.confidence_low, self.confidence_high, self.confidence_level)
        if any(v is not None for v in interval):
            if any(v is None for v in interval) or self.value is None:
                raise ValueError("incomplete confidence interval")
            if (
                not self.confidence_low <= self.value <= self.confidence_high
                or not 0 < self.confidence_level < 1
            ):
                raise ValueError("invalid confidence interval")
        return self


class Row(Record):
    locator: Text
    model_label: Annotated[str, Field(min_length=1, max_length=512)]
    version: Annotated[str, Field(min_length=1, max_length=255)]
    protocol: dict[str, Any]
    evaluator: Annotated[str, Field(min_length=1, max_length=255)]
    publication_date: date | None
    citation: Citation
    metrics: Annotated[tuple[Metric, ...], Field(min_length=1, max_length=8)]
    reviewed_mapping: Mapping | None = None
    source_data: dict[str, Any] = Field(default_factory=dict)


class Manifest(Record):
    schema_version: Literal[1]
    source_id: Text
    registry_hash: Hash
    review: Review
    publication_date: date | None
    coverage_note: Annotated[str, Field(min_length=1, max_length=4000)]
    rows: Annotated[tuple[Row, ...], Field(max_length=10000)]


class Batch(Record):
    source_id: str
    content_hash: Hash
    publication_date: date | None
    coverage_note: str
    rows: tuple[Row, ...]
    review: Review | None = None


def canonical(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def bad_constant(value):
        raise ValueError("non-finite JSON number")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid JSON document") from exc
