"""Initial canonical Phase 2 schema.

Revision ID: 20260719_0001
Revises: None
"""

from alembic import op

from apps.api.alembic.snapshots.v0001_models import Base

revision = "20260719_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=bind)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_immutable_mutation()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is immutable', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER source_records_no_update_delete
        BEFORE UPDATE OR DELETE ON source_records
        FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER event_versions_no_update_delete
        BEFORE UPDATE OR DELETE ON event_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER lineage_reviews_no_update_delete
        BEFORE UPDATE OR DELETE ON lineage_reviews
        FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER source_record_embeddings_no_update_delete
        BEFORE UPDATE OR DELETE ON source_record_embeddings
        FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER triage_decisions_no_update_delete
        BEFORE UPDATE OR DELETE ON triage_decisions
        FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER alerts_no_update_delete
        BEFORE UPDATE OR DELETE ON alerts
        FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
        """
    )
    for table_name in (
        "event_version_claims",
        "event_version_evidence",
        "pipeline_evaluations",
        "source_record_lineage",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_no_update_delete
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        "source_record_lineage",
        "pipeline_evaluations",
        "event_version_evidence",
        "event_version_claims",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_no_update_delete ON {table_name}")
    op.execute("DROP TRIGGER IF EXISTS alerts_no_update_delete ON alerts")
    op.execute("DROP TRIGGER IF EXISTS triage_decisions_no_update_delete ON triage_decisions")
    op.execute(
        "DROP TRIGGER IF EXISTS source_record_embeddings_no_update_delete "
        "ON source_record_embeddings"
    )
    op.execute("DROP TRIGGER IF EXISTS lineage_reviews_no_update_delete ON lineage_reviews")
    op.execute("DROP TRIGGER IF EXISTS event_versions_no_update_delete ON event_versions")
    op.execute("DROP TRIGGER IF EXISTS source_records_no_update_delete ON source_records")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS prevent_immutable_mutation")
    Base.metadata.drop_all(bind=bind)
