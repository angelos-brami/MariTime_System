"""Add S8 claim-extraction shadow workflow.

Revision ID: 20260719_0003
Revises: 20260719_0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260719_0003"
down_revision = "20260719_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("model_processing_approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("model_processing_approved_by", sa.String(length=255), nullable=True),
    )

    run_status = postgresql.ENUM(
        "queued",
        "running",
        "succeeded",
        "failed",
        "skipped",
        name="claim_extraction_run_status",
        create_type=False,
    )
    proposal_status = postgresql.ENUM(
        "pending",
        "accepted",
        "edited",
        "rejected",
        name="claim_extraction_proposal_status",
        create_type=False,
    )
    decision = postgresql.ENUM(
        "accept",
        "edit",
        "reject",
        name="claim_extraction_decision",
        create_type=False,
    )
    run_status.create(op.get_bind(), checkfirst=True)
    proposal_status.create(op.get_bind(), checkfirst=True)
    decision.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "claim_extraction_runs",
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("injection_suspected", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_records.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_record_id",
            "prompt_version",
            "model_version",
            name="uq_claim_extraction_runs_record_prompt_model",
        ),
    )
    op.create_index(
        "ix_claim_extraction_runs_source_record_id",
        "claim_extraction_runs",
        ["source_record_id"],
    )
    op.create_index(
        "ix_claim_extraction_runs_status_started",
        "claim_extraction_runs",
        ["status", "started_at"],
    )

    op.create_table(
        "claim_extraction_proposals",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("claimant", sa.String(length=255), nullable=True),
        sa.Column("occurred_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("location_json", sa.JSON(), nullable=True),
        sa.Column("quantities_json", sa.JSON(), nullable=False),
        sa.Column("hedging_language", sa.Boolean(), nullable=False),
        sa.Column("source_sentence_quote", sa.Text(), nullable=False),
        sa.Column("status", proposal_status, server_default="pending", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["claim_extraction_runs.id"]),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_records.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "proposal_index", name="uq_claim_proposals_run_index"),
    )
    op.create_index(
        "ix_claim_extraction_proposals_run_id",
        "claim_extraction_proposals",
        ["run_id"],
    )
    op.create_index(
        "ix_claim_extraction_proposals_source_record_id",
        "claim_extraction_proposals",
        ["source_record_id"],
    )
    op.create_index(
        "ix_claim_proposals_status_record",
        "claim_extraction_proposals",
        ["status", "source_record_id"],
    )

    claim_state = postgresql.ENUM(name="claim_state", create_type=False)
    op.create_table(
        "claim_extraction_reviews",
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("decision", decision, nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("reviewer", sa.String(length=255), nullable=False),
        sa.Column("claim_state", claim_state, nullable=True),
        sa.Column("final_claim_json", sa.JSON(), nullable=True),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("baseline_seconds", sa.Integer(), nullable=False),
        sa.Column("review_seconds", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("resulting_claim_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["claim_extraction_proposals.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["resulting_claim_id"], ["claims.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id"),
        sa.CheckConstraint("baseline_seconds > 0", name="claim_review_baseline_positive"),
        sa.CheckConstraint("review_seconds > 0", name="claim_review_duration_positive"),
    )
    op.create_index(
        "ix_claim_extraction_reviews_event_id",
        "claim_extraction_reviews",
        ["event_id"],
    )

    op.create_table(
        "claim_extraction_qa",
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("evaluator", sa.String(length=255), nullable=False),
        sa.Column("error_found", sa.Boolean(), nullable=False),
        sa.Column("error_codes", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["claim_extraction_reviews.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id"),
    )

    for table in ("claim_extraction_reviews", "claim_extraction_qa"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_update_delete
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
            """
        )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_final_extraction_mutation()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION '% extraction audit row cannot be deleted', TG_TABLE_NAME;
          END IF;
          IF OLD.status NOT IN ('queued', 'running', 'pending') THEN
            RAISE EXCEPTION '% finalized row is immutable', TG_TABLE_NAME;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("claim_extraction_runs", "claim_extraction_proposals"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_final_no_update_delete
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_final_extraction_mutation();
            """
        )


def downgrade() -> None:
    for table in ("claim_extraction_runs", "claim_extraction_proposals"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_final_no_update_delete ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_final_extraction_mutation()")
    for table in ("claim_extraction_reviews", "claim_extraction_qa"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
    op.drop_table("claim_extraction_qa")
    op.drop_index("ix_claim_extraction_reviews_event_id", table_name="claim_extraction_reviews")
    op.drop_table("claim_extraction_reviews")
    op.drop_index("ix_claim_proposals_status_record", table_name="claim_extraction_proposals")
    op.drop_index(
        "ix_claim_extraction_proposals_source_record_id",
        table_name="claim_extraction_proposals",
    )
    op.drop_index("ix_claim_extraction_proposals_run_id", table_name="claim_extraction_proposals")
    op.drop_table("claim_extraction_proposals")
    op.drop_index("ix_claim_extraction_runs_status_started", table_name="claim_extraction_runs")
    op.drop_index("ix_claim_extraction_runs_source_record_id", table_name="claim_extraction_runs")
    op.drop_table("claim_extraction_runs")
    postgresql.ENUM(name="claim_extraction_decision", create_type=False).drop(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(name="claim_extraction_proposal_status", create_type=False).drop(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(name="claim_extraction_run_status", create_type=False).drop(
        op.get_bind(), checkfirst=True
    )
    op.drop_column("sources", "model_processing_approved_by")
    op.drop_column("sources", "model_processing_approved_at")
