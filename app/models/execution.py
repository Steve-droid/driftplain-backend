"""Trusted runtime profiles and owner-scoped immutable selection history."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExecutionRuntime(Base):
    __tablename__ = "execution_runtime"
    __table_args__ = (
        UniqueConstraint("id", "catalog_model_id", name="uq_execution_runtime_model"),
        ForeignKeyConstraint(
            ["deployment_id", "catalog_model_id"],
            ["catalog_provider_deployment.id", "catalog_provider_deployment.model_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("mode IN ('single_call','opencode')", name="ck_execution_mode"),
        CheckConstraint(
            "verification_status IN ('pending','verified','retired')",
            name="ck_execution_verified_status",
        ),
        CheckConstraint(
            "verification_status <> 'verified' OR (verified_at IS NOT NULL AND verification_ref IS NOT NULL)",
            name="ck_execution_evidence",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catalog_model_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_model.id"), nullable=False
    )
    deployment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32))
    capability: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64))
    provider_model_id: Mapped[str] = mapped_column(String(255))
    auth_mode: Mapped[str] = mapped_column(String(32))
    credential_env_var: Mapped[str | None] = mapped_column(String(128))
    runtime_version: Mapped[str] = mapped_column(String(128))
    verification_status: Mapped[str] = mapped_column(
        String(32), server_default="pending"
    )
    verification_ref: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")


class ModelSelection(Base):
    __tablename__ = "model_selection"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_selection_project"),
        ForeignKeyConstraint(
            ["project_id", "user_id"],
            ["project.id", "project.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["runtime_id", "catalog_model_id"],
            ["execution_runtime.id", "execution_runtime.catalog_model_id"],
        ),
        ForeignKeyConstraint(
            ["observation_id", "catalog_model_id", "snapshot_id"],
            [
                "catalog_observation.id",
                "catalog_observation.catalog_model_id",
                "catalog_observation.source_snapshot_id",
            ],
        ),
        CheckConstraint(
            "method IN ('benchmark_ranked','supported_unranked')",
            name="ck_selection_method",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(Integer)
    runtime_id: Mapped[int] = mapped_column(Integer)
    catalog_model_id: Mapped[int] = mapped_column(Integer)
    observation_id: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[int] = mapped_column(Integer)
    legacy_option_id: Mapped[int | None] = mapped_column(
        ForeignKey("recommendation_option.id")
    )
    legacy_baseline_model_id: Mapped[int | None] = mapped_column(ForeignKey("model.id"))
    method: Mapped[str] = mapped_column(String(32))
    policy_snapshot: Mapped[dict] = mapped_column(JSONB)
    result_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExecutionRevision(Base):
    __tablename__ = "execution_revision"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_revision_project"),
        ForeignKeyConstraint(
            ["selection_id", "project_id"],
            ["model_selection.id", "model_selection.project_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer)
    selection_id: Mapped[int] = mapped_column(Integer)
    configuration: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
