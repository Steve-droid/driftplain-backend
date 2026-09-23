"""B3 additive import state and bounded payloads; existing evidence stays immutable.

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
"""

from alembic import op
import sqlalchemy as sa

revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_catalog_observation_label_trgm",
        "catalog_observation",
        ["source_model_label"],
        postgresql_using="gin",
        postgresql_ops={"source_model_label": "gin_trgm_ops"},
    )
    op.create_unique_constraint(
        "uq_catalog_snapshot_id_source", "catalog_source_snapshot", ["id", "source_id"]
    )
    op.create_table(
        "catalog_import_state",
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("catalog_source.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("active_snapshot_id", sa.Integer()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_promoted_at", sa.DateTime(timezone=True)),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("etag", sa.String(512)),
        sa.Column("last_modified", sa.String(512)),
        sa.Column("checked_content_hash", sa.String(64)),
        sa.ForeignKeyConstraint(
            ["active_snapshot_id", "source_id"],
            ["catalog_source_snapshot.id", "catalog_source_snapshot.source_id"],
            name="fk_catalog_active_snapshot_source",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "failure_count >= 0", name="ck_catalog_import_failure_count"
        ),
    )
    op.create_table(
        "catalog_source_payload",
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("catalog_source_snapshot.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "octet_length(raw_bytes) BETWEEN 1 AND 8000000",
            name="ck_catalog_payload_size",
        ),
    )
    op.create_table(
        "catalog_snapshot_lifecycle",
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("catalog_source_snapshot.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("hold_reason", sa.Text()),
    )
    op.execute("""CREATE TRIGGER trg_catalog_source_payload_immutable
        BEFORE UPDATE OR DELETE ON catalog_source_payload FOR EACH ROW
        EXECUTE FUNCTION reject_catalog_evidence_mutation()""")
    op.execute("""CREATE FUNCTION validate_catalog_payload() RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM catalog_source_snapshot s WHERE s.id = NEW.snapshot_id
                AND s.content_hash = encode(sha256(NEW.raw_bytes), 'hex')
                AND s.byte_count = octet_length(NEW.raw_bytes)
            ) THEN
                RAISE EXCEPTION 'catalog payload does not match immutable snapshot';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER trg_catalog_payload_matches_snapshot
        BEFORE INSERT ON catalog_source_payload FOR EACH ROW
        EXECUTE FUNCTION validate_catalog_payload()""")


def downgrade():
    # A downgrade must not erase the sole copy of a reviewed report or its state.
    if op.get_bind().scalar(
        sa.text("""SELECT EXISTS (SELECT 1 FROM catalog_import_state)
        OR EXISTS (SELECT 1 FROM catalog_source_payload)
        OR EXISTS (SELECT 1 FROM catalog_snapshot_lifecycle)""")
    ):
        raise RuntimeError(
            "B3 import state is populated; preserve imported evidence before downgrade"
        )
    op.drop_index("ix_catalog_observation_label_trgm", table_name="catalog_observation")
    op.drop_table("catalog_snapshot_lifecycle")
    op.drop_table("catalog_source_payload")
    op.execute("DROP FUNCTION validate_catalog_payload()")
    op.drop_table("catalog_import_state")
    op.drop_constraint(
        "uq_catalog_snapshot_id_source", "catalog_source_snapshot", type_="unique"
    )
