"""Durable once-per-upstream-failure claims; no runtime activation."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f9a0b1c2d3e4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "diagnosis_claim",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("execution_revision_id", sa.Integer(), nullable=False),
        sa.Column("build_id", sa.String(255), nullable=False),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "project_id", "build_id", "stage", name="uq_diagnosis_failure"
        ),
        sa.ForeignKeyConstraint(
            ["execution_revision_id", "project_id"],
            ["execution_revision.id", "execution_revision.project_id"],
            ondelete="CASCADE",
        ),
    )
    op.execute("""CREATE FUNCTION prevent_diagnosis_claim_update() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Diagnosis claims are immutable'; END $$""")
    op.execute(
        "CREATE TRIGGER diagnosis_claim_immutable BEFORE UPDATE ON diagnosis_claim FOR EACH ROW EXECUTE FUNCTION prevent_diagnosis_claim_update()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM diagnosis_claim) THEN
    RAISE EXCEPTION 'Cannot discard diagnosis invocation history'; END IF; END $$""")
    op.drop_table("diagnosis_claim")
    op.execute("DROP FUNCTION prevent_diagnosis_claim_update()")
