"""Immutable report bytes and explicit operator action history (B16)."""

from datetime import datetime
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class CatalogReportArtifact(Base):
    __tablename__ = "catalog_report_artifact"
    __table_args__ = (
        CheckConstraint("octet_length(raw_bytes) BETWEEN 1 AND 8000000"),
        CheckConstraint("content_hash = encode(sha256(raw_bytes), 'hex')"),
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_source.id", ondelete="RESTRICT"), primary_key=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_url: Mapped[str] = mapped_column(Text)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CatalogOperatorAction(Base):
    __tablename__ = "catalog_operator_action"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "source_id"],
            ["catalog_source_snapshot.id", "catalog_source_snapshot.source_id"],
        ),
        ForeignKeyConstraint(
            ["previous_snapshot_id", "source_id"],
            ["catalog_source_snapshot.id", "catalog_source_snapshot.source_id"],
        ),
        ForeignKeyConstraint(
            ["source_id", "report_hash"],
            [
                "catalog_report_artifact.source_id",
                "catalog_report_artifact.content_hash",
            ],
        ),
        CheckConstraint("action IN ('select_snapshot', 'review_report')"),
        CheckConstraint("length(trim(reason)) > 0"),
        CheckConstraint("(action = 'review_report') = (report_hash IS NOT NULL)"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_source.id", ondelete="RESTRICT")
    )
    snapshot_id: Mapped[int]
    previous_snapshot_id: Mapped[int | None]
    report_hash: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(1024))
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
