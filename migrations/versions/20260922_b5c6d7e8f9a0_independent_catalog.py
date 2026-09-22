"""Independent benchmark catalog identities, observations, and public-query indexes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg


revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def _drop_unused_advisor_table() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM proactive_alert")):
        raise RuntimeError(
            "proactive_alert is not empty; preserve its records before removing the unused table"
        )
    op.drop_table("proactive_alert")
    op.execute("DROP TYPE alert_kind")


def upgrade() -> None:
    # Fail before any DDL if the only removal candidate unexpectedly contains data.
    _drop_unused_advisor_table()
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "catalog_benchmark_family",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("tooltip", sa.Text()),
        sa.Column("methodology_url", sa.String(1024)),
        sa.Column("limitations", sa.Text()),
        sa.Column("legacy_benchmark_id", sa.Integer()),
        sa.UniqueConstraint("slug", name="uq_catalog_benchmark_family_slug"),
        sa.UniqueConstraint(
            "legacy_benchmark_id", name="uq_catalog_benchmark_family_legacy_id"
        ),
    )
    op.create_index(
        "ix_catalog_benchmark_family_name", "catalog_benchmark_family", ["name"]
    )
    op.create_index(
        "ix_catalog_benchmark_family_name_trgm",
        "catalog_benchmark_family",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )

    op.create_table(
        "catalog_benchmark_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "benchmark_family_id",
            sa.Integer(),
            sa.ForeignKey("catalog_benchmark_family.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(255), nullable=False),
        sa.Column("release_date", sa.Date()),
        sa.Column("description", sa.Text()),
        sa.Column("methodology", sa.Text()),
        sa.Column("methodology_url", sa.String(1024)),
        sa.UniqueConstraint(
            "benchmark_family_id", "version", name="uq_catalog_benchmark_version"
        ),
        sa.UniqueConstraint(
            "id", "benchmark_family_id", name="uq_catalog_benchmark_version_family"
        ),
    )
    op.create_index(
        "ix_catalog_benchmark_version_family",
        "catalog_benchmark_version",
        ["benchmark_family_id"],
    )

    op.create_table(
        "catalog_protocol",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "benchmark_version_id",
            sa.Integer(),
            sa.ForeignKey("catalog_benchmark_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("runner", sa.String(255)),
        sa.Column("runner_version", sa.String(255)),
        sa.Column("methodology", sa.Text()),
        sa.Column(
            "configuration",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint(
            "benchmark_version_id",
            "configuration_fingerprint",
            name="uq_catalog_protocol_configuration",
        ),
        sa.UniqueConstraint(
            "id", "benchmark_version_id", name="uq_catalog_protocol_version"
        ),
    )
    op.create_index(
        "ix_catalog_protocol_version", "catalog_protocol", ["benchmark_version_id"]
    )

    op.create_table(
        "catalog_evaluator",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("organization", sa.String(255)),
        sa.Column("url", sa.String(1024)),
        sa.UniqueConstraint(
            "name",
            "organization",
            name="uq_catalog_evaluator_name_org",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_catalog_evaluator_name", "catalog_evaluator", ["name"])

    op.create_table(
        "catalog_source",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("definition_url", sa.String(1024)),
        sa.Column("result_url", sa.String(1024)),
        sa.Column("license_text", sa.Text()),
        sa.Column("attribution", sa.Text()),
        sa.Column("access_notes", sa.Text()),
        sa.UniqueConstraint("slug", name="uq_catalog_source_slug"),
    )
    op.create_index("ix_catalog_source_name", "catalog_source", ["name"])

    op.create_table(
        "catalog_source_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("catalog_source.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("artifact_uri", sa.String(1024)),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column("publication_date", sa.Date()),
        sa.Column("content_type", sa.String(255)),
        sa.Column("byte_count", sa.BigInteger()),
        sa.UniqueConstraint(
            "source_id", "content_hash", name="uq_catalog_source_snapshot_hash"
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_catalog_snapshot_sha256"
        ),
        sa.CheckConstraint(
            "byte_count IS NULL OR byte_count >= 0", name="ck_catalog_snapshot_bytes"
        ),
    )
    op.create_index(
        "ix_catalog_source_snapshot_source", "catalog_source_snapshot", ["source_id"]
    )

    op.create_table(
        "catalog_model",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("organization", sa.String(255)),
        sa.Column("description", sa.Text()),
        sa.Column("legacy_model_id", sa.Integer()),
        sa.UniqueConstraint("slug", name="uq_catalog_model_slug"),
        sa.UniqueConstraint("legacy_model_id", name="uq_catalog_model_legacy_id"),
    )
    op.create_index("ix_catalog_model_name", "catalog_model", ["name"])
    op.create_index(
        "ix_catalog_model_organization", "catalog_model", ["organization"]
    )
    op.create_index(
        "ix_catalog_model_name_trgm",
        "catalog_model",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_catalog_model_org_trgm",
        "catalog_model",
        ["organization"],
        postgresql_using="gin",
        postgresql_ops={"organization": "gin_trgm_ops"},
    )

    op.create_table(
        "catalog_provider",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("url", sa.String(1024)),
        sa.UniqueConstraint("slug", name="uq_catalog_provider_slug"),
    )
    op.create_index("ix_catalog_provider_name", "catalog_provider", ["name"])
    op.create_index(
        "ix_catalog_provider_name_trgm",
        "catalog_provider",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )

    op.create_table(
        "catalog_provider_deployment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "provider_id",
            sa.Integer(),
            sa.ForeignKey("catalog_provider.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey("catalog_model.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("deployment_key", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("variant", sa.String(255)),
        sa.Column("endpoint_url", sa.String(1024)),
        sa.UniqueConstraint(
            "provider_id", "deployment_key", name="uq_catalog_provider_deployment"
        ),
        sa.UniqueConstraint(
            "id", "model_id", name="uq_catalog_provider_deployment_model"
        ),
    )
    op.create_index(
        "ix_catalog_deployment_model", "catalog_provider_deployment", ["model_id"]
    )
    op.create_index(
        "ix_catalog_deployment_provider",
        "catalog_provider_deployment",
        ["provider_id"],
    )
    op.create_index(
        "ix_catalog_deployment_name", "catalog_provider_deployment", ["name"]
    )
    op.create_index(
        "ix_catalog_deployment_name_trgm",
        "catalog_provider_deployment",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_catalog_deployment_key_trgm",
        "catalog_provider_deployment",
        ["deployment_key"],
        postgresql_using="gin",
        postgresql_ops={"deployment_key": "gin_trgm_ops"},
    )

    op.create_table(
        "catalog_model_alias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("catalog_source.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_label", sa.String(512), nullable=False),
        sa.Column("normalized_label", sa.String(512), nullable=False),
        sa.Column("resolution_status", sa.String(32), nullable=False),
        sa.Column(
            "catalog_model_id",
            sa.Integer(),
            sa.ForeignKey("catalog_model.id", ondelete="RESTRICT"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_note", sa.Text()),
        sa.UniqueConstraint(
            "source_id", "source_label", name="uq_catalog_model_alias_source_label"
        ),
        sa.CheckConstraint(
            "resolution_status IN ('resolved', 'unresolved', 'ambiguous')",
            name="ck_catalog_model_alias_status",
        ),
        sa.CheckConstraint(
            "(resolution_status = 'resolved' AND catalog_model_id IS NOT NULL) OR "
            "(resolution_status IN ('unresolved', 'ambiguous') AND catalog_model_id IS NULL)",
            name="ck_catalog_model_alias_target",
        ),
    )
    op.create_index(
        "ix_catalog_model_alias_normalized",
        "catalog_model_alias",
        ["normalized_label"],
    )
    op.create_index(
        "ix_catalog_model_alias_model", "catalog_model_alias", ["catalog_model_id"]
    )
    op.create_index(
        "ix_catalog_model_alias_normalized_trgm",
        "catalog_model_alias",
        ["normalized_label"],
        postgresql_using="gin",
        postgresql_ops={"normalized_label": "gin_trgm_ops"},
    )

    op.create_table(
        "catalog_observation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "benchmark_family_id",
            sa.Integer(),
            sa.ForeignKey("catalog_benchmark_family.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("benchmark_version_id", sa.Integer()),
        sa.Column("protocol_id", sa.Integer()),
        sa.Column(
            "evaluator_id",
            sa.Integer(),
            sa.ForeignKey("catalog_evaluator.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "source_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("catalog_source_snapshot.id", ondelete="RESTRICT"),
        ),
        sa.Column("source_record_locator", sa.String(1024), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("record_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_model_label", sa.String(512), nullable=False),
        sa.Column(
            "catalog_model_id",
            sa.Integer(),
            sa.ForeignKey("catalog_model.id", ondelete="RESTRICT"),
        ),
        sa.Column("provider_deployment_id", sa.Integer()),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("provenance_status", sa.String(32), nullable=False),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("task_type", sa.String(64)),
        sa.Column("context_window", sa.Integer()),
        sa.Column("reported_cost_per_mtok", sa.Numeric(14, 6)),
        sa.Column("observed_at", sa.Date()),
        sa.Column("notes", sa.Text()),
        sa.Column("legacy_benchmark_result_id", sa.Integer()),
        sa.Column("legacy_source_document_id", sa.Integer()),
        sa.Column("legacy_harness_id", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["protocol_id", "benchmark_version_id"],
            ["catalog_protocol.id", "catalog_protocol.benchmark_version_id"],
            name="fk_catalog_observation_protocol_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_deployment_id", "catalog_model_id"],
            ["catalog_provider_deployment.id", "catalog_provider_deployment.model_id"],
            name="fk_catalog_observation_deployment_model",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_snapshot_id",
            "source_record_locator",
            "configuration_fingerprint",
            "record_fingerprint",
            name="uq_catalog_observation_identity",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "origin IN ('source', 'legacy_backfill')", name="ck_catalog_observation_origin"
        ),
        sa.CheckConstraint(
            "provenance_status IN ('complete', 'incomplete')",
            name="ck_catalog_observation_provenance",
        ),
        sa.CheckConstraint(
            "protocol_id IS NULL OR benchmark_version_id IS NOT NULL",
            name="ck_catalog_observation_protocol_version",
        ),
        sa.CheckConstraint(
            "provider_deployment_id IS NULL OR catalog_model_id IS NOT NULL",
            name="ck_catalog_observation_deployment_model",
        ),
        sa.CheckConstraint(
            "provenance_status = 'incomplete' OR "
            "(benchmark_version_id IS NOT NULL AND protocol_id IS NOT NULL AND "
            " evaluator_id IS NOT NULL AND source_snapshot_id IS NOT NULL)",
            name="ck_catalog_observation_complete_provenance",
        ),
    )
    for index_name, column_name in (
        ("ix_catalog_observation_family", "benchmark_family_id"),
        ("ix_catalog_observation_version", "benchmark_version_id"),
        ("ix_catalog_observation_protocol", "protocol_id"),
        ("ix_catalog_observation_evaluator", "evaluator_id"),
        ("ix_catalog_observation_snapshot", "source_snapshot_id"),
        ("ix_catalog_observation_model", "catalog_model_id"),
        ("ix_catalog_observation_deployment", "provider_deployment_id"),
        ("ix_catalog_observation_legacy_result", "legacy_benchmark_result_id"),
    ):
        op.create_index(index_name, "catalog_observation", [column_name])

    op.create_table(
        "catalog_metric_definition",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("benchmark_family_id", sa.Integer(), nullable=False),
        sa.Column("benchmark_version_id", sa.Integer()),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("unit", sa.String(128)),
        sa.Column("direction", sa.String(32)),
        sa.Column("minimum", sa.Numeric(20, 8)),
        sa.Column("maximum", sa.Numeric(20, 8)),
        sa.ForeignKeyConstraint(
            ["benchmark_version_id", "benchmark_family_id"],
            [
                "catalog_benchmark_version.id",
                "catalog_benchmark_version.benchmark_family_id",
            ],
            name="fk_catalog_metric_version_family",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "benchmark_family_id",
            "benchmark_version_id",
            "key",
            name="uq_catalog_metric_scope_key",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('higher', 'lower', 'non_ranking')",
            name="ck_catalog_metric_direction",
        ),
        sa.CheckConstraint(
            "minimum IS NULL OR maximum IS NULL OR minimum <= maximum",
            name="ck_catalog_metric_range",
        ),
    )
    op.create_index(
        "ix_catalog_metric_family",
        "catalog_metric_definition",
        ["benchmark_family_id"],
    )

    op.create_table(
        "catalog_observation_metric",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "observation_id",
            sa.Integer(),
            sa.ForeignKey("catalog_observation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "metric_definition_id",
            sa.Integer(),
            sa.ForeignKey("catalog_metric_definition.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("value", sa.Numeric(20, 8)),
        sa.Column("reported_value", sa.String(255)),
        sa.Column("missing_reason", sa.String(255)),
        sa.Column("category", sa.String(255)),
        sa.Column("subset", sa.String(255)),
        sa.Column("aggregation", sa.String(255)),
        sa.Column("confidence_low", sa.Numeric(20, 8)),
        sa.Column("confidence_high", sa.Numeric(20, 8)),
        sa.Column("confidence_level", sa.Numeric(8, 4)),
        sa.Column("uncertainty_type", sa.String(128)),
        sa.Column("sample_size", sa.Integer()),
        sa.Column("denominator", sa.Integer()),
        sa.Column("attempts", sa.Integer()),
        sa.UniqueConstraint(
            "observation_id",
            "metric_definition_id",
            "category",
            "subset",
            "aggregation",
            name="uq_catalog_observation_metric_context",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "confidence_low IS NULL OR confidence_high IS NULL OR "
            "confidence_low <= confidence_high",
            name="ck_catalog_observation_metric_interval",
        ),
    )
    op.create_index(
        "ix_catalog_observation_metric_observation",
        "catalog_observation_metric",
        ["observation_id"],
    )
    op.create_index(
        "ix_catalog_observation_metric_definition",
        "catalog_observation_metric",
        ["metric_definition_id"],
    )

    op.create_table(
        "catalog_task_benchmark",
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column(
            "benchmark_family_id",
            sa.Integer(),
            sa.ForeignKey("catalog_benchmark_family.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(64)),
        sa.Column("rationale", sa.Text()),
        sa.PrimaryKeyConstraint(
            "task_type", "benchmark_family_id", name="pk_catalog_task_benchmark"
        ),
    )
    op.create_index(
        "ix_catalog_task_benchmark_family",
        "catalog_task_benchmark",
        ["benchmark_family_id"],
    )

    # Backfill only facts the legacy schema actually recorded. Unknown evaluation
    # dimensions stay NULL and are marked incomplete; a DB-row hash is deliberately
    # not promoted to a source snapshot.
    op.execute(
        sa.text(
            """
            INSERT INTO catalog_benchmark_family
                (slug, name, description, legacy_benchmark_id)
            SELECT 'legacy-benchmark-' || id, name, notes, id
            FROM benchmark
            ORDER BY id
            """
        )
    )

    # Evidence rows are append-only. Corrections create a new snapshot/observation
    # instead of rewriting the historical record.
    op.execute(
        """
        CREATE FUNCTION reject_catalog_evidence_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'catalog evidence is immutable; append a new record';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in (
        "catalog_source_snapshot",
        "catalog_observation",
        "catalog_observation_metric",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_catalog_evidence_mutation()
            """
        )
    op.execute(
        sa.text(
            """
            INSERT INTO catalog_model (slug, name, organization, legacy_model_id)
            SELECT 'legacy-model-' || id, name, vendor, id
            FROM model
            ORDER BY id
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO catalog_metric_definition
                (benchmark_family_id, benchmark_version_id, key, name)
            SELECT DISTINCT cbf.id, NULL::integer,
                   COALESCE(NULLIF(br.metric, ''), 'legacy-score'),
                   COALESCE(NULLIF(br.metric, ''), 'Legacy score')
            FROM benchmark_result br
            JOIN catalog_benchmark_family cbf
              ON cbf.legacy_benchmark_id = br.benchmark_id
            ORDER BY cbf.id, COALESCE(NULLIF(br.metric, ''), 'legacy-score')
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO catalog_observation
                (benchmark_family_id, source_record_locator,
                 configuration_fingerprint, record_fingerprint,
                 source_model_label, catalog_model_id, origin, provenance_status,
                 source_url, task_type, context_window, reported_cost_per_mtok,
                 observed_at, notes, legacy_benchmark_result_id,
                 legacy_source_document_id, legacy_harness_id)
            SELECT cbf.id,
                   'legacy:benchmark_result:' || br.id,
                   md5(concat_ws('|', br.harness_id::text, br.task_type,
                                 br.context_window::text)),
                   md5(concat_ws('|', br.id::text, br.score::text, br.metric,
                                 br.cost_per_mtok::text, br.context_window::text,
                                 br.source, br.source_document_id::text,
                                 br.measured_at::text)),
                   m.name, cm.id, 'legacy_backfill', 'incomplete',
                   br.source, br.task_type, br.context_window, br.cost_per_mtok,
                   br.measured_at, b.notes, br.id, br.source_document_id, br.harness_id
            FROM benchmark_result br
            JOIN model m ON m.id = br.model_id
            JOIN benchmark b ON b.id = br.benchmark_id
            JOIN catalog_model cm ON cm.legacy_model_id = br.model_id
            JOIN catalog_benchmark_family cbf
              ON cbf.legacy_benchmark_id = br.benchmark_id
            ORDER BY br.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO catalog_observation_metric
                (observation_id, metric_definition_id, value,
                 reported_value, missing_reason)
            SELECT o.id, md.id, br.score, br.score::text,
                   CASE WHEN br.score IS NULL THEN 'not reported in legacy row' END
            FROM benchmark_result br
            JOIN catalog_observation o ON o.legacy_benchmark_result_id = br.id
            JOIN catalog_metric_definition md
              ON md.benchmark_family_id = o.benchmark_family_id
             AND md.benchmark_version_id IS NULL
             AND md.key = COALESCE(NULLIF(br.metric, ''), 'legacy-score')
            ORDER BY br.id
            """
        )
    )


def downgrade() -> None:
    for table in (
        "catalog_task_benchmark",
        "catalog_observation_metric",
        "catalog_metric_definition",
        "catalog_observation",
        "catalog_model_alias",
        "catalog_provider_deployment",
        "catalog_provider",
        "catalog_model",
        "catalog_source_snapshot",
        "catalog_source",
        "catalog_evaluator",
        "catalog_protocol",
        "catalog_benchmark_version",
        "catalog_benchmark_family",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION reject_catalog_evidence_mutation()")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")

    alert_kind = pg.ENUM(
        "upgrade", "downgrade", name="alert_kind", create_type=False
    )
    alert_kind.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "proactive_alert",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", alert_kind, nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("evidence", pg.JSONB()),
        sa.Column("status", sa.String(32)),
    )
