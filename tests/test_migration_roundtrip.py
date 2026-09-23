"""S2 contract test: the initial migration round-trips, and the schema carries
NO embedding/vector columns (the recommender is deterministic; pgvector is dropped).

This is an integration test: it needs the compose Postgres (`docker compose up -d db`).
It runs on a *throwaway* database so it never touches dev data, and skips cleanly
if no database is reachable.

What it pins:
- `upgrade head` builds every §5 entity (incl. source_document / chat_message /
  retrieval_trace — the chat #4 + ingestion #3 tables the backlog calls out).
- NO column anywhere is an embedding/vector, and the pgvector type is absent.
- `downgrade base` fully reverses: no tables AND no leftover enum types.
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import get_settings

ROOT = Path(__file__).resolve().parent.parent
TEST_DB = "modelmatch_migration_test"

# Every table the initial migration must create (architecture.md §5 + the
# llm_usage tally added in S2). The backlog names the starred ones explicitly.
EXPECTED_TABLES = {
    "execution_runtime", "model_selection", "execution_revision",
    "user",
    "google_login_nonce",
    "model",
    "agent_runtime_config",
    "harness",
    "benchmark",
    "source_document",  # ingestion #3 input (content_hash = idempotency key)
    "benchmark_result",
    "requirements_profile",
    "recommendation_option",
    "recommendation_evidence",  # FK-enforced provenance join table
    "project",
    "jenkins_connection",
    "ci_run",
    "ci_finding",
    "finding_feedback",
    "chat_message",  # grounded chat #4
    "retrieval_trace",  # grounded chat #4
    "llm_call",
    "llm_usage",
    "catalog_benchmark_family",
    "catalog_benchmark_version",
    "catalog_protocol",
    "catalog_evaluator",
    "catalog_source",
    "catalog_source_snapshot",
    "catalog_import_state",
    "catalog_source_payload",
    "catalog_snapshot_lifecycle",
    "catalog_model",
    "catalog_provider",
    "catalog_provider_deployment",
    "catalog_model_alias",
    "catalog_observation",
    "catalog_metric_definition",
    "catalog_observation_metric",
    "catalog_task_benchmark",
}


def _admin_engine():
    """Engine on the default DB, AUTOCOMMIT — used to create/drop the throwaway DB."""
    admin_url = make_url(get_settings().database_url)
    return create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)


@pytest.fixture
def migration_db():
    """Create a throwaway database, yield an Alembic Config pointed at it, drop it."""
    try:
        admin = _admin_engine()
        with admin.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Postgres not reachable — run `docker compose up -d db`")

    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))

    test_url = make_url(get_settings().database_url).set(database=TEST_DB)

    # Point app.config (and therefore Alembic's env.py) at the throwaway DB.
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url.render_as_string(hide_password=False)
    get_settings.cache_clear()

    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))

    try:
        yield cfg, test_url
    finally:
        if prev is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev
        get_settings.cache_clear()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        admin.dispose()


def _enum_type_names(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT typname FROM pg_type WHERE typtype = 'e'"))
        return {r[0] for r in rows}


def test_upgrade_builds_all_entities_with_no_embedding_columns(migration_db):
    cfg, test_url = migration_db
    from alembic import command

    command.upgrade(cfg, "head")

    engine = create_engine(test_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())

        # Every expected entity exists (alembic_version is bookkeeping, ignored).
        missing = EXPECTED_TABLES - tables
        assert not missing, f"migration did not create: {sorted(missing)}"

        # NO embedding/vector columns anywhere — the deterministic-recommender guarantee.
        for table in EXPECTED_TABLES:
            for col in inspector.get_columns(table):
                name = col["name"].lower()
                assert "embedding" not in name and "vector" not in name, (
                    f"unexpected embedding column {table}.{col['name']}"
                )
                assert "vector" not in str(col["type"]).lower(), (
                    f"unexpected vector type on {table}.{col['name']}"
                )

        # pgvector extension type must not be installed.
        with engine.connect() as conn:
            has_vector = conn.execute(
                text("SELECT 1 FROM pg_type WHERE typname = 'vector'")
            ).first()
        assert has_vector is None, "pgvector type present — embeddings are dropped"

        # Trusted CI runtime config is bootstrapped by migrations, not only by the
        # offline test seed path. The three demo-supported runtime mappings must be
        # present immediately after `upgrade head`.
        with engine.connect() as conn:
            runtime_rows = conn.execute(
                text(
                    """
                    SELECT m.name, m.vendor, arc.provider, arc.provider_model_id,
                           arc.auth_mode, arc.credential_env_var, arc.enabled
                    FROM agent_runtime_config arc
                    JOIN model m ON m.id = arc.model_id
                    ORDER BY m.vendor, m.name
                    """
                )
            ).all()
        assert len(runtime_rows) == 3
        assert runtime_rows == [
            (
                "Nova 2 Lite",
                "Amazon",
                "bedrock",
                "global.amazon.nova-2-lite-v1:0",
                "aws_iam",
                None,
                True,
            ),
            (
                "Claude Haiku 4.5",
                "Anthropic",
                "anthropic",
                "claude-haiku-4-5",
                "api_key",
                "ANTHROPIC_API_KEY",
                True,
            ),
            (
                "Gemini 2.5 Flash",
                "Google",
                "gemini",
                "gemini-2.5-flash",
                "api_key",
                "GOOGLE_API_KEY",
                True,
            ),
        ]
    finally:
        engine.dispose()


def test_downgrade_reverses_tables_and_enum_types(migration_db):
    cfg, test_url = migration_db
    from alembic import command

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(test_url)
    try:
        inspector = inspect(engine)
        leftover_tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert not leftover_tables, f"downgrade left tables: {sorted(leftover_tables)}"

        leftover_enums = _enum_type_names(engine)
        assert not leftover_enums, f"downgrade left enum types: {sorted(leftover_enums)}"
        with engine.connect() as conn:
            assert conn.scalar(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'")
            ) == 0
    finally:
        engine.dispose()


def test_b2_populated_upgrade_backfills_unknown_provenance_and_preserves_legacy_on_downgrade(
    migration_db,
):
    """B2 keeps every legacy row while refusing to invent missing provenance."""
    from alembic import command

    cfg, test_url = migration_db
    command.upgrade(cfg, "a4b5c6d7e8f9")

    engine = create_engine(test_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO model (id, name, vendor) VALUES (901, 'Legacy M', 'Maker')"))
            conn.execute(text("INSERT INTO harness (id, name, vendor) VALUES (902, 'Old Runner', 'Lab')"))
            conn.execute(text(
                "INSERT INTO benchmark (id, name, task_type, as_of, notes) "
                "VALUES (903, 'Legacy Bench', 'ci_review', DATE '2026-01-02', 'Known note')"
            ))
            conn.execute(text(
                "INSERT INTO source_document "
                "(id, kind, uri, s3_key, content_hash, fetched_at, status) VALUES "
                "(904, 'leaderboard', 'https://example.test/report', 'legacy/key', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "TIMESTAMPTZ '2026-01-03 00:00:00+00', 'ingested')"
            ))
            conn.execute(text(
                "INSERT INTO benchmark_result "
                "(id, model_id, harness_id, benchmark_id, task_type, score, metric, "
                " cost_per_mtok, context_window, source, source_document_id, measured_at) VALUES "
                "(905, 901, 902, 903, 'ci_review', 71.25, 'accuracy', 2.5, 128000, "
                " 'https://example.test/report', 904, DATE '2026-01-02')"
            ))

        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT o.origin, o.provenance_status, o.benchmark_version_id, "
                "       o.protocol_id, o.evaluator_id, o.source_snapshot_id, "
                "       o.source_model_label, o.legacy_benchmark_result_id, "
                "       o.legacy_source_document_id, o.legacy_harness_id, om.value "
                "FROM catalog_observation o "
                "JOIN catalog_observation_metric om ON om.observation_id = o.id "
                "WHERE o.legacy_benchmark_result_id = 905"
            )).one()
            assert row == (
                "legacy_backfill", "incomplete", None, None, None, None,
                "Legacy M", 905, 904, 902, 71.25,
            )
            assert conn.scalar(text("SELECT count(*) FROM model WHERE id = 901")) == 1
            assert conn.scalar(text("SELECT count(*) FROM benchmark_result WHERE id = 905")) == 1
            assert "proactive_alert" not in inspect(engine).get_table_names()

        command.downgrade(cfg, "a4b5c6d7e8f9")

        with engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM model WHERE id = 901")) == 1
            assert conn.scalar(text("SELECT count(*) FROM benchmark_result WHERE id = 905")) == 1
            assert "catalog_observation" not in inspect(engine).get_table_names()
            assert "proactive_alert" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_b2_upgrade_refuses_to_drop_a_populated_proactive_alert(migration_db):
    """The unused table is removable only after its real data is proved empty."""
    from alembic import command

    cfg, test_url = migration_db
    command.upgrade(cfg, "a4b5c6d7e8f9")
    engine = create_engine(test_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO \"user\" (id, email, password_hash) VALUES (1, 'u@x', 'h')"))
            conn.execute(text("INSERT INTO project (id, user_id, name) VALUES (1, 1, 'p')"))
            conn.execute(text(
                "INSERT INTO proactive_alert (id, project_id, kind, status) "
                "VALUES (1, 1, 'upgrade', 'open')"
            ))

        with pytest.raises(RuntimeError, match="proactive_alert is not empty"):
            command.upgrade(cfg, "head")

        with engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM proactive_alert")) == 1
            assert "catalog_observation" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_two_tasks_migration_backfills_task_and_cwe_and_reverses(migration_db):
    """E20 (d1e2f3a4b5c6): upgrading a database that holds pre-E20 rows sets each
    project's task from its recommendation, turns `code_review` runs into `ci_review`,
    lifts the CWE prefix out of seeded security findings, and downgrades cleanly."""
    from alembic import command

    cfg, test_url = migration_db
    command.upgrade(cfg, "c9d0e1f2a3b4")  # the revision before P38e

    engine = create_engine(test_url)
    try:
        with engine.begin() as conn:
            # ids from 900 up: the runtime-config seed migration already owns low model ids
            conn.execute(text("INSERT INTO \"user\" (id, email, password_hash) VALUES (1, 'u@x', 'h')"))
            conn.execute(text("INSERT INTO model (id, name, vendor) VALUES (900, 'M', 'V')"))
            conn.execute(text(
                "INSERT INTO requirements_profile (id, user_id, task_types) VALUES "
                "(1, 1, ARRAY['ci_review']), (2, 1, ARRAY['security_analysis']), (3, 1, NULL)"
            ))
            conn.execute(text(
                "INSERT INTO recommendation_option (id, profile_id, model_id) VALUES "
                "(1, 1, 900), (2, 2, 900), (3, 3, 900)"
            ))
            conn.execute(text(
                "INSERT INTO project (id, user_id, name, selected_option_id, baseline_model_id) VALUES "
                "(1, 1, 'review', 1, 900), (2, 1, 'sec', 2, 900), (3, 1, 'legacy', 3, 900), (4, 1, 'none', NULL, 900)"
            ))
            conn.execute(text(
                "INSERT INTO ci_run (id, project_id, jenkins_build_id, task) VALUES "
                "(1, 1, 'a', 'code_review'), (2, 2, 'b', 'security_analysis')"
            ))
            conn.execute(text(
                "INSERT INTO ci_finding (id, ci_run_id, severity, category, file, line, message) VALUES "
                "(1, 2, 'critical', 'security', 'a.py', 1, 'CWE-89: SQL injection — request parameter concatenated'), "
                "(2, 2, 'low', 'security', 'b.py', 2, 'CWE-327: weak hash (MD5) used for a security decision'), "
                "(3, 1, 'low', 'style', 'c.py', 3, 'Unused import')"
            ))

        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            tasks = dict(conn.execute(text("SELECT id, task_type FROM project ORDER BY id")).all())
            assert tasks == {1: "ci_review", 2: "security_analysis", 3: "ci_review", 4: "ci_review"}
            assert conn.execute(text("SELECT review_preferences FROM project WHERE id = 1")).scalar() is None
            run_tasks = dict(conn.execute(text("SELECT id, task FROM ci_run ORDER BY id")).all())
            assert run_tasks == {1: "ci_review", 2: "security_analysis"}
            cwes = dict(conn.execute(text("SELECT id, cwe FROM ci_finding ORDER BY id")).all())
            assert cwes == {
                1: "CWE-89: SQL injection",
                2: "CWE-327: weak hash (MD5) used for a security decision",
                3: None,
            }
            assert conn.execute(text("SELECT cache_read_tokens FROM ci_run WHERE id = 1")).scalar() is None
            # the new default applies to a fresh run
            conn.execute(text("INSERT INTO ci_run (id, project_id, jenkins_build_id) VALUES (3, 1, 'c')"))
            assert conn.execute(text("SELECT task FROM ci_run WHERE jenkins_build_id = 'c'")).scalar() == "ci_review"
            conn.commit()

        command.downgrade(cfg, "c9d0e1f2a3b4")
        inspector = inspect(engine)
        assert "task_type" not in {c["name"] for c in inspector.get_columns("project")}
        assert "review_preferences" not in {c["name"] for c in inspector.get_columns("project")}
        assert "cwe" not in {c["name"] for c in inspector.get_columns("ci_finding")}
        assert "cache_read_tokens" not in {c["name"] for c in inspector.get_columns("ci_run")}
        with engine.connect() as conn:
            run_tasks = dict(conn.execute(text("SELECT id, task FROM ci_run ORDER BY id")).all())
            assert run_tasks[1] == "code_review" and run_tasks[2] == "security_analysis"
        command.upgrade(cfg, "head")  # up / down / up
    finally:
        engine.dispose()


def test_google_migration_preserves_users_and_projects(migration_db):
    from alembic import command
    from app.auth.security import verify_password

    cfg, test_url = migration_db
    command.upgrade(cfg, "d1e2f3a4b5c6")
    engine = create_engine(test_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO \"user\" (id,email,password_hash) VALUES (900,'legacy@example.com','old-hash')"))
            conn.execute(text("INSERT INTO project (id,user_id,name) VALUES (900,900,'preserved-project')"))
        command.upgrade(cfg, "e2f3a4b5c6d7")
        with engine.begin() as conn:
            assert conn.execute(text('SELECT password_hash FROM "user" WHERE id=900')).scalar() == "old-hash"
            conn.execute(text("INSERT INTO \"user\" (id,email,google_subject) VALUES (901,'google@example.com','google-sub')"))
            conn.execute(text("INSERT INTO project (id,user_id,name) VALUES (901,901,'google-project')"))
        command.downgrade(cfg, "d1e2f3a4b5c6")
        with engine.connect() as conn:
            hashes = dict(conn.execute(text('SELECT id,password_hash FROM "user" ORDER BY id')).all())
            assert hashes == {900: "old-hash", 901: "$argon2id$disabled"}
            assert not verify_password(hashes[901], "!")
            assert conn.execute(text("SELECT count(*) FROM project WHERE id IN (900,901)")).scalar() == 2
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()


def test_b3_empty_roundtrip_and_populated_downgrade_guard(migration_db):
    from alembic import command
    from app.catalog.imports.pipeline import import_bytes

    cfg, test_url = migration_db
    command.upgrade(cfg, "b5c6d7e8f9a0")
    engine = create_engine(test_url)
    try:
        with engine.connect() as conn:
            runtime_before = conn.execute(
                text(
                    "SELECT id, model_id, enabled FROM agent_runtime_config ORDER BY id"
                )
            ).all()
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "b5c6d7e8f9a0")
        assert "catalog_import_state" not in inspect(engine).get_table_names()
        command.upgrade(cfg, "head")
        raw = (ROOT / "data/catalog/b3/mmlu-pro.json").read_bytes()
        assert import_bytes(engine, "mmlu-pro", raw).status == "promoted"
        with pytest.raises(RuntimeError, match="preserve imported evidence"):
            command.downgrade(cfg, "b5c6d7e8f9a0")
        # Operational state is mutable; deleting it must not let downgrade erase evidence.
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM catalog_import_state"))
        with pytest.raises(RuntimeError, match="preserve imported evidence"):
            command.downgrade(cfg, "b5c6d7e8f9a0")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM catalog_source_payload")
                ).scalar()
                == 1
            )
            assert (
                conn.execute(
                    text(
                        "SELECT id, model_id, enabled FROM agent_runtime_config ORDER BY id"
                    )
                ).all()
                == runtime_before
            )
    finally:
        engine.dispose()


def test_b6_populated_legacy_upgrade_preserves_tokens_and_history(migration_db):
    from alembic import command
    cfg, url = migration_db
    command.upgrade(cfg, 'c6d7e8f9a0b1')
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            uid = conn.scalar(text("INSERT INTO public.user (email,password_hash) VALUES ('b6-upgrade@example.com','placeholder') RETURNING id"))
            pid = conn.scalar(text("INSERT INTO project (user_id,name,task_type) VALUES (:u,'Legacy','ci_review') RETURNING id"), {'u':uid})
            conn.execute(text("INSERT INTO jenkins_connection (project_id,ci_token_hash) VALUES (:p,:h)"), {'p':pid,'h':'a'*64})
            conn.execute(text("INSERT INTO ci_run (project_id,jenkins_build_id,task) VALUES (:p,'old-build','ci_review')"), {'p':pid})
        command.upgrade(cfg,'head')
        with engine.connect() as conn:
            assert conn.scalar(text('SELECT ci_token_hash FROM jenkins_connection WHERE project_id=:p'),{'p':pid})=='a'*64
            assert conn.scalar(text('SELECT execution_revision_id FROM project WHERE id=:p'),{'p':pid}) is None
            assert conn.scalar(text('SELECT count(*) FROM ci_run WHERE project_id=:p'),{'p':pid})==1
            assert conn.scalar(text('SELECT count(*) FROM execution_runtime'))==0
        command.downgrade(cfg,'c6d7e8f9a0b1')
        command.upgrade(cfg,'head')
        with engine.connect() as conn:
            assert conn.scalar(text('SELECT ci_token_hash FROM jenkins_connection WHERE project_id=:p'),{'p':pid})=='a'*64
    finally:
        engine.dispose()


def test_b7_populated_upgrade_preserves_legacy_and_v2_history(migration_db):
    """Upgrade real B6 records without rewriting configs, findings or feedback."""
    from alembic import command
    from sqlalchemy.orm import Session
    from app.models import CatalogSnapshotLifecycle, ExecutionRevision, ModelSelection, Project, User
    from app.selections.policy import policy_for
    from tests.test_selections import evidence

    cfg, url = migration_db
    command.upgrade(cfg, 'd7e8f9a0b1c2')
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            rt, obs = evidence.__wrapped__(db)[0]
            user = User(email='b7-upgrade@example.com', password_hash='fixture')
            db.add(user)
            db.flush()
            project = Project(user_id=user.id, name='Existing B6')
            db.add(project)
            db.flush()
            db.add(CatalogSnapshotLifecycle(snapshot_id=obs.source_snapshot_id, activated_at=rt.verified_at, hold_reason='execution history'))
            db.flush()
            selection = ModelSelection(project_id=project.id, user_id=user.id, runtime_id=rt.id,
                catalog_model_id=rt.catalog_model_id, observation_id=obs.id, snapshot_id=obs.source_snapshot_id,
                method='supported_unranked', policy_snapshot=policy_for('ci_review', 'single_call'))
            db.add(selection)
            db.flush()
            old_config = {'contractVersion': 2, 'taskType': 'ci_review', 'executionMode': 'single_call',
                          'model': {'providerModelId': rt.provider_model_id}, 'reviewPreferences': 'Preserve verbatim'}
            rev = ExecutionRevision(project_id=project.id, selection_id=selection.id, configuration=old_config)
            db.add(rev)
            db.flush()
            project.execution_revision_id = rev.id
            pid, uid, rid = project.id, user.id, rev.id
            db.commit()
        with engine.begin() as conn:
            run_id = conn.scalar(text("INSERT INTO ci_run (project_id,jenkins_build_id,task,gate,actual_cost) VALUES (:p,'old-b7','ci_review','fail',0.12345) RETURNING id"), {'p': pid})
            fid = conn.scalar(text("INSERT INTO ci_finding (ci_run_id,category,message,cwe) VALUES (:r,'security','Prior finding','CWE-89') RETURNING id"), {'r': run_id})
            conn.execute(text("INSERT INTO finding_feedback (ci_finding_id,user_id,verdict) VALUES (:f,:u,'accept')"), {'f': fid, 'u': uid})
            conn.execute(text("INSERT INTO jenkins_connection (project_id,ci_token_hash) VALUES (:p,:h)"), {'p': pid, 'h': 'c'*64})
        command.upgrade(cfg, 'head')
        with engine.connect() as conn:
            assert conn.scalar(text('SELECT configuration FROM execution_revision WHERE id=:r'), {'r': rid}) == old_config
            assert conn.scalar(text('SELECT task_result FROM ci_run WHERE id=:r'), {'r': run_id}) is None
            assert str(conn.scalar(text('SELECT actual_cost FROM ci_run WHERE id=:r'), {'r': run_id})) == '0.123450'
            assert conn.scalar(text('SELECT verdict FROM finding_feedback WHERE ci_finding_id=:f'), {'f': fid}) == 'accept'
            assert conn.scalar(text('SELECT ci_token_hash FROM jenkins_connection WHERE project_id=:p'), {'p': pid}) == 'c'*64
        # B6 history does not block reversing the empty B7 addition.
        command.downgrade(cfg, 'd7e8f9a0b1c2')
        command.upgrade(cfg, 'head')
        with engine.connect() as conn:
            assert conn.scalar(text('SELECT configuration FROM execution_revision WHERE id=:r'), {'r': rid}) == old_config
            assert conn.scalar(text('SELECT cwe FROM ci_finding WHERE id=:f'), {'f': fid}) == 'CWE-89'
    finally:
        engine.dispose()
