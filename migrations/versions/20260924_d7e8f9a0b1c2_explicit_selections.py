"""B6 explicit selections and immutable execution revisions.

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
"""

import sqlalchemy as sa
from alembic import op

revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_project_owner", "project", ["id", "user_id"])
    op.create_unique_constraint(
        "uq_observation_model_snapshot",
        "catalog_observation",
        ["id", "catalog_model_id", "source_snapshot_id"],
    )
    op.add_column("project", sa.Column("execution_revision_id", sa.Integer()))
    op.add_column("ci_run", sa.Column("execution_revision_id", sa.Integer()))
    op.execute("""CREATE TABLE execution_runtime (
	id SERIAL NOT NULL,
	catalog_model_id INTEGER NOT NULL,
	deployment_id INTEGER NOT NULL,
	task VARCHAR(64) NOT NULL,
	mode VARCHAR(32) NOT NULL,
	capability VARCHAR(64) NOT NULL,
	provider VARCHAR(64) NOT NULL,
	provider_model_id VARCHAR(255) NOT NULL,
	auth_mode VARCHAR(32) NOT NULL,
	credential_env_var VARCHAR(128),
	runtime_version VARCHAR(128) NOT NULL,
	verification_status VARCHAR(32) DEFAULT 'pending' NOT NULL,
	verification_ref TEXT,
	verified_at TIMESTAMP WITH TIME ZONE,
	enabled BOOLEAN DEFAULT 'false' NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_execution_runtime_model UNIQUE (id, catalog_model_id),
	FOREIGN KEY(deployment_id, catalog_model_id) REFERENCES catalog_provider_deployment (id, model_id) ON DELETE RESTRICT,
	CONSTRAINT ck_execution_mode CHECK (mode IN ('single_call','opencode')),
	CONSTRAINT ck_execution_verified_status CHECK (verification_status IN ('pending','verified','retired')),
	CONSTRAINT ck_execution_evidence CHECK (verification_status <> 'verified' OR (verified_at IS NOT NULL AND verification_ref IS NOT NULL)),
	FOREIGN KEY(catalog_model_id) REFERENCES catalog_model (id)
)""")
    op.execute("""CREATE TABLE model_selection (
	id SERIAL NOT NULL,
	project_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	runtime_id INTEGER NOT NULL,
	catalog_model_id INTEGER NOT NULL,
	observation_id INTEGER NOT NULL,
	snapshot_id INTEGER NOT NULL,
	legacy_option_id INTEGER REFERENCES recommendation_option(id),
	legacy_baseline_model_id INTEGER REFERENCES model(id),
	method VARCHAR(32) NOT NULL,
	policy_snapshot JSONB NOT NULL,
	result_snapshot JSONB,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_selection_project UNIQUE (id, project_id),
	FOREIGN KEY(project_id, user_id) REFERENCES project (id, user_id) ON DELETE CASCADE,
	FOREIGN KEY(runtime_id, catalog_model_id) REFERENCES execution_runtime (id, catalog_model_id),
	FOREIGN KEY(observation_id, catalog_model_id, snapshot_id) REFERENCES catalog_observation (id, catalog_model_id, source_snapshot_id),
	CONSTRAINT ck_selection_method CHECK (method IN ('benchmark_ranked','supported_unranked'))
)""")
    op.execute("""CREATE TABLE execution_revision (
	id SERIAL NOT NULL,
	project_id INTEGER NOT NULL,
	selection_id INTEGER NOT NULL,
	configuration JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_revision_project UNIQUE (id, project_id),
	FOREIGN KEY(selection_id, project_id) REFERENCES model_selection (id, project_id) ON DELETE CASCADE
)""")
    op.execute("""CREATE FUNCTION require_selection_hold() RETURNS trigger AS $$
        BEGIN
            PERFORM 1 FROM catalog_snapshot_lifecycle
                WHERE snapshot_id = NEW.snapshot_id AND hold_reason IS NOT NULL
                FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'selection requires an evidence hold';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM catalog_observation
                           WHERE id = NEW.observation_id AND origin = 'source') THEN
                RAISE EXCEPTION 'selection requires a source observation';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql""")
    op.execute(
        "CREATE TRIGGER trg_selection_hold BEFORE INSERT ON model_selection FOR EACH ROW EXECUTE FUNCTION require_selection_hold()"
    )
    op.create_foreign_key(
        "fk_project_execution_revision",
        "project",
        "execution_revision",
        ["execution_revision_id", "id"],
        ["id", "project_id"],
    )
    op.create_foreign_key(
        "fk_run_execution_revision",
        "ci_run",
        "execution_revision",
        ["execution_revision_id", "project_id"],
        ["id", "project_id"],
    )
    for table in ("model_selection", "execution_revision"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_catalog_evidence_mutation()"
        )
    op.execute("""CREATE FUNCTION guard_execution_runtime() RETURNS trigger AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'enabled' - 'verification_status') IS DISTINCT FROM
               (to_jsonb(OLD) - 'enabled' - 'verification_status') THEN
                RAISE EXCEPTION 'runtime identity is immutable; insert a new revision';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql""")
    op.execute(
        "CREATE TRIGGER trg_execution_runtime_identity BEFORE UPDATE ON execution_runtime FOR EACH ROW EXECUTE FUNCTION guard_execution_runtime()"
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM model_selection) OR EXISTS (SELECT 1 FROM execution_runtime) OR EXISTS (SELECT 1 FROM execution_revision)"
        )
    ):
        raise RuntimeError(
            "B6 execution history is populated; preserve it before downgrade"
        )
    op.drop_constraint("fk_run_execution_revision", "ci_run", type_="foreignkey")
    op.drop_constraint("fk_project_execution_revision", "project", type_="foreignkey")
    op.drop_column("ci_run", "execution_revision_id")
    op.drop_column("project", "execution_revision_id")
    for table in ("execution_revision", "model_selection", "execution_runtime"):
        op.drop_table(table)
    op.execute("DROP FUNCTION guard_execution_runtime()")
    op.execute("DROP FUNCTION require_selection_hold()")
    op.drop_constraint(
        "uq_observation_model_snapshot", "catalog_observation", type_="unique"
    )
    op.drop_constraint("uq_project_owner", "project", type_="unique")
