"""Reviewed operator-owned rates; no public writer, catalog import, or seeded prices."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    AwareDatetime,
    model_validator,
)
from sqlalchemy import select
from app.models.billing import BillingRate

Category = Literal[
    "input",
    "output",
    "reasoning",
    "cache_read",
    "cache_write",
    "cache_write_5m",
    "cache_write_1h",
]
Rate = Annotated[
    Decimal,
    Field(ge=0, le=1000000, max_digits=18, decimal_places=9, allow_inf_nan=False),
]


class Tier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upToInputTokens: int | None = Field(default=None, ge=0)
    rates: dict[Category, Rate]


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    rateVersion: str = Field(min_length=1, max_length=128)
    source: HttpUrl
    observedAt: AwareDatetime
    currency: Literal["USD"] = "USD"
    serviceTier: Literal[
        "standard", "priority", "flex", "scale", "ultrafast", "all"
    ] = "standard"
    tierEvidence: str = Field(min_length=1, max_length=2000)
    tiers: list[Tier] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def ordered(self):
        bounds = [t.upToInputTokens for t in self.tiers]
        if (
            bounds[-1] is not None
            or any(b is None for b in bounds[:-1])
            or bounds[:-1] != sorted(set(bounds[:-1]))
        ):
            raise ValueError("Tiers must increase and end with an unbounded tier")
        return self


def register_rate(db, runtime_id, effective_at, valid_until, schedule):
    """For reviewed migrations/operator tooling only. Caller owns transaction/approval."""
    value = Schedule.model_validate(schedule)
    if (
        effective_at.tzinfo is None
        or valid_until.tzinfo is None
        or effective_at >= valid_until
    ):
        raise ValueError("A finite aware effective interval is required")
    row = BillingRate(
        runtime_id=runtime_id,
        effective_at=effective_at,
        valid_until=valid_until,
        schedule=value.model_dump(mode="json"),
    )
    db.add(row)
    db.flush()
    return row


def snapshot_for(db, runtime_id):
    now = datetime.now(UTC)
    row = db.scalar(
        select(BillingRate)
        .where(
            BillingRate.runtime_id == runtime_id,
            BillingRate.effective_at <= now,
            BillingRate.valid_until > now,
        )
        .order_by(BillingRate.effective_at.desc(), BillingRate.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {
        **Schedule.model_validate(row.schedule).model_dump(mode="json"),
        "rateId": row.id,
        "runtimeId": runtime_id,
        "effectiveAt": row.effective_at.isoformat(),
        "validUntil": row.valid_until.isoformat(),
        "pinnedAt": now.isoformat(),
    }
