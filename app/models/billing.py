"""Append-only exact-runtime rate schedules, independent from benchmark evidence."""

from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class BillingRate(Base):
    __tablename__ = "billing_rate"
    __table_args__ = (
        UniqueConstraint("runtime_id", "effective_at"),
        CheckConstraint("valid_until > effective_at", name="ck_rate_interval"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    runtime_id: Mapped[int] = mapped_column(
        ForeignKey("execution_runtime.id", ondelete="RESTRICT")
    )
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    schedule: Mapped[dict] = mapped_column(JSONB)
