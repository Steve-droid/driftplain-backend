"""B7 additive task result metadata; do not reinterpret historical findings.

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ci_run", sa.Column("task_result", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "ck_run_task_result_revision",
        "ci_run",
        "task_result IS NULL OR execution_revision_id IS NOT NULL",
    )
    op.execute("""
        CREATE FUNCTION protect_task_result() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.task_result IS DISTINCT FROM OLD.task_result OR
             (OLD.task_result IS NOT NULL AND
              (NEW.execution_revision_id IS DISTINCT FROM OLD.execution_revision_id OR
               NEW.project_id IS DISTINCT FROM OLD.project_id OR
               NEW.task IS DISTINCT FROM OLD.task)) THEN
            RAISE EXCEPTION 'task result history is immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER protect_task_result BEFORE UPDATE ON ci_run
        FOR EACH ROW EXECUTE FUNCTION protect_task_result();
    """)


def downgrade():
    db = op.get_bind()
    if db.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM ci_run WHERE task_result IS NOT NULL)")
    ) or db.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM execution_revision WHERE configuration ? 'taskContractVersion')"
        )
    ):
        raise RuntimeError(
            "B7 downgrade refused: preserve task configuration and result history"
        )
    op.execute("DROP TRIGGER protect_task_result ON ci_run")
    op.execute("DROP FUNCTION protect_task_result()")
    op.drop_constraint("ck_run_task_result_revision", "ci_run", type_="check")
    op.drop_column("ci_run", "task_result")
