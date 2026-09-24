"""Immutable selected-run billing, with null historical snapshots and no seeded rates."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a0b1c2d3e4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "billing_rate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "runtime_id",
            sa.Integer(),
            sa.ForeignKey("execution_runtime.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schedule", JSONB(), nullable=False),
        sa.UniqueConstraint("runtime_id", "effective_at"),
        sa.CheckConstraint("valid_until > effective_at", name="ck_rate_interval"),
    )
    op.add_column(
        "execution_revision", sa.Column("billing_snapshot", JSONB(), nullable=True)
    )
    op.add_column("ci_run", sa.Column("billing", JSONB(), nullable=True))
    op.execute("""CREATE FUNCTION protect_billing_rate() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Billing rates are immutable'; END $$;
    CREATE TRIGGER billing_rate_immutable BEFORE UPDATE OR DELETE ON billing_rate
    FOR EACH ROW EXECUTE FUNCTION protect_billing_rate();
    CREATE FUNCTION protect_run_billing() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.billing IS DISTINCT FROM OLD.billing OR
         (OLD.billing IS NOT NULL AND (NEW.execution_revision_id IS DISTINCT FROM OLD.execution_revision_id OR
          NEW.project_id IS DISTINCT FROM OLD.project_id OR NEW.task IS DISTINCT FROM OLD.task OR
          NEW.tokens_in IS DISTINCT FROM OLD.tokens_in OR NEW.tokens_out IS DISTINCT FROM OLD.tokens_out OR
          NEW.cache_read_tokens IS DISTINCT FROM OLD.cache_read_tokens OR NEW.model_id IS DISTINCT FROM OLD.model_id)) THEN
        RAISE EXCEPTION 'Run billing is immutable';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER run_billing_immutable BEFORE UPDATE ON ci_run FOR EACH ROW EXECUTE FUNCTION protect_run_billing();""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM billing_rate) OR
    EXISTS (SELECT 1 FROM ci_run WHERE billing IS NOT NULL) OR
    EXISTS (SELECT 1 FROM execution_revision WHERE billing_snapshot IS NOT NULL) THEN
      RAISE EXCEPTION 'Cannot discard billing history'; END IF; END $$""")
    op.execute(
        "DROP TRIGGER run_billing_immutable ON ci_run; DROP FUNCTION protect_run_billing()"
    )
    op.drop_column("ci_run", "billing")
    op.drop_column("execution_revision", "billing_snapshot")
    op.drop_table("billing_rate")
    op.execute("DROP FUNCTION protect_billing_rate()")
