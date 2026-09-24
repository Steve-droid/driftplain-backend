"""B16 durable report acquisition and operator audit, without startup seeding."""

from alembic import op
import sqlalchemy as sa

revision = "b16c0a7a0001"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade():
    for name, size in [
        ("report_content_hash", 64),
        ("reviewed_report_hash", 64),
        ("report_etag", 512),
        ("report_last_modified", 512),
    ]:
        op.add_column("catalog_import_state", sa.Column(name, sa.String(size)))
    for name in ["refresh_last_checked_at", "refresh_last_successful_check_at"]:
        op.add_column(
            "catalog_import_state", sa.Column(name, sa.DateTime(timezone=True))
        )
    op.add_column(
        "catalog_import_state",
        sa.Column(
            "refresh_failure_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_check_constraint(
        "ck_catalog_refresh_failure_count",
        "catalog_import_state",
        "refresh_failure_count >= 0",
    )
    op.execute("""CREATE TABLE catalog_report_artifact (
      source_id integer NOT NULL REFERENCES catalog_source(id) ON DELETE RESTRICT,
      content_hash varchar(64) NOT NULL,
      artifact_url text NOT NULL,
      raw_bytes bytea NOT NULL,
      fetched_at timestamptz NOT NULL,
      PRIMARY KEY (source_id, content_hash),
      CHECK (octet_length(raw_bytes) BETWEEN 1 AND 8000000),
      CHECK (content_hash = encode(sha256(raw_bytes), 'hex'))
    );
    CREATE TABLE catalog_operator_action (
      id serial PRIMARY KEY,
      source_id integer NOT NULL REFERENCES catalog_source(id) ON DELETE RESTRICT,
      snapshot_id integer NOT NULL,
      previous_snapshot_id integer,
      report_hash varchar(64),
      action varchar(32) NOT NULL CHECK (action IN ('select_snapshot', 'review_report')),
      reason varchar(1024) NOT NULL CHECK (length(trim(reason)) > 0),
      performed_at timestamptz NOT NULL,
      FOREIGN KEY (snapshot_id, source_id) REFERENCES catalog_source_snapshot(id, source_id),
      FOREIGN KEY (previous_snapshot_id, source_id) REFERENCES catalog_source_snapshot(id, source_id),
      FOREIGN KEY (source_id, report_hash) REFERENCES catalog_report_artifact(source_id, content_hash),
      CHECK ((action = 'review_report') = (report_hash IS NOT NULL))
    );
    ALTER TABLE catalog_import_state ADD CONSTRAINT fk_checked_report
      FOREIGN KEY (source_id, report_content_hash) REFERENCES catalog_report_artifact(source_id, content_hash);
    ALTER TABLE catalog_import_state ADD CONSTRAINT fk_reviewed_report
      FOREIGN KEY (source_id, reviewed_report_hash) REFERENCES catalog_report_artifact(source_id, content_hash);
    CREATE TRIGGER catalog_report_artifact_immutable BEFORE UPDATE OR DELETE ON catalog_report_artifact
      FOR EACH ROW EXECUTE FUNCTION reject_catalog_evidence_mutation();
    CREATE TRIGGER catalog_operator_action_immutable BEFORE UPDATE OR DELETE ON catalog_operator_action
      FOR EACH ROW EXECUTE FUNCTION reject_catalog_evidence_mutation();""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM catalog_report_artifact) OR
      EXISTS (SELECT 1 FROM catalog_operator_action) OR
      EXISTS (SELECT 1 FROM catalog_import_state WHERE refresh_last_checked_at IS NOT NULL) THEN
      RAISE EXCEPTION 'Cannot discard refresh history'; END IF; END $$""")
    op.drop_constraint("fk_checked_report", "catalog_import_state")
    op.drop_constraint("fk_reviewed_report", "catalog_import_state")
    op.drop_table("catalog_operator_action")
    op.drop_table("catalog_report_artifact")
    op.drop_constraint("ck_catalog_refresh_failure_count", "catalog_import_state")
    for name in [
        "refresh_last_checked_at",
        "refresh_last_successful_check_at",
        "refresh_failure_count",
    ]:
        op.drop_column("catalog_import_state", name)
    for name in [
        "report_content_hash",
        "reviewed_report_hash",
        "report_etag",
        "report_last_modified",
    ]:
        op.drop_column("catalog_import_state", name)
