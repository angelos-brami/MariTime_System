"""Add S9 reports, correction propagation, and TTV ownership.

Revision ID: 20260720_0004
Revises: 20260719_0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260720_0004"
down_revision = "20260719_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    impact = postgresql.ENUM(
        "non_operational",
        "operationally_relevant",
        name="correction_impact",
        create_type=False,
    )
    impact.create(op.get_bind(), checkfirst=True)

    op.add_column("corrections", sa.Column("version_from_id", sa.Uuid(), nullable=True))
    op.add_column("corrections", sa.Column("version_to_id", sa.Uuid(), nullable=True))
    op.add_column("corrections", sa.Column("impact", impact, nullable=True))
    op.add_column("corrections", sa.Column("root_cause", sa.Text(), nullable=True))
    op.add_column("corrections", sa.Column("corrective_action", sa.Text(), nullable=True))
    op.add_column("corrections", sa.Column("drafted_by", sa.String(length=255), nullable=True))
    op.add_column("corrections", sa.Column("signed_off_by", sa.String(length=255), nullable=True))
    op.add_column("corrections", sa.Column("preview_hash", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_corrections_preview_hash", "corrections", ["preview_hash"])
    op.add_column(
        "corrections",
        sa.Column("propagation_deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_corrections_version_from_id_event_versions",
        "corrections",
        "event_versions",
        ["version_from_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_corrections_version_to_id_event_versions",
        "corrections",
        "event_versions",
        ["version_to_id"],
        ["id"],
    )
    op.create_index("ix_corrections_version_from_id", "corrections", ["version_from_id"])
    op.create_index("ix_corrections_version_to_id", "corrections", ["version_to_id"])

    delivery_channel = postgresql.ENUM(name="delivery_channel", create_type=False)
    delivery_status = postgresql.ENUM(name="delivery_status", create_type=False)
    op.create_table(
        "correction_deliveries",
        sa.Column("correction_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("channel", delivery_channel, nullable=False),
        sa.Column("recipient", sa.String(length=512), nullable=False),
        sa.Column("status", delivery_status, nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("provider_ref", sa.String(length=255), nullable=True),
        sa.Column("message_snapshot_json", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["correction_id"], ["corrections.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "correction_id",
            "user_id",
            "channel",
            name="uq_correction_deliveries_correction_user_channel",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="correction_attempt_count_nonnegative"),
    )
    op.create_index(
        "ix_correction_deliveries_correction_id",
        "correction_deliveries",
        ["correction_id"],
    )
    op.create_index(
        "ix_correction_deliveries_status_retry",
        "correction_deliveries",
        ["status", "next_attempt_at"],
    )

    op.create_table(
        "state_knowledge_reports",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_version_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("requested_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version_content_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["event_version_id"], ["event_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_state_knowledge_reports_event_id", "state_knowledge_reports", ["event_id"])
    op.create_index(
        "ix_state_knowledge_reports_event_version_id",
        "state_knowledge_reports",
        ["event_version_id"],
    )
    op.create_index(
        "ix_state_knowledge_reports_account_id",
        "state_knowledge_reports",
        ["account_id"],
    )

    op.add_column(
        "daily_briefs",
        sa.Column(
            "corrections_json",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column("ttv_log", sa.Column("created_by", sa.String(length=255), nullable=True))
    op.add_column("ttv_log", sa.Column("updated_by", sa.String(length=255), nullable=True))
    op.add_column("ttv_log", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ttv_log", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_ttv_log_event", "ttv_log", ["event_id"])

    for table in ("corrections", "state_knowledge_reports"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_update_delete
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
            """
        )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_final_correction_delivery_mutation()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'correction delivery audit rows cannot be deleted';
          END IF;
          IF OLD.status IN ('delivered', 'failed') THEN
            RAISE EXCEPTION 'final correction delivery is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER correction_deliveries_final_no_update_delete
        BEFORE UPDATE OR DELETE ON correction_deliveries
        FOR EACH ROW EXECUTE FUNCTION prevent_final_correction_delivery_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS correction_deliveries_final_no_update_delete "
        "ON correction_deliveries"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_final_correction_delivery_mutation()")
    for table in ("state_knowledge_reports", "corrections"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
    op.drop_constraint("uq_ttv_log_event", "ttv_log", type_="unique")
    op.drop_column("ttv_log", "updated_at")
    op.drop_column("ttv_log", "created_at")
    op.drop_column("ttv_log", "updated_by")
    op.drop_column("ttv_log", "created_by")
    op.drop_column("daily_briefs", "corrections_json")
    op.drop_index("ix_state_knowledge_reports_account_id", table_name="state_knowledge_reports")
    op.drop_index(
        "ix_state_knowledge_reports_event_version_id", table_name="state_knowledge_reports"
    )
    op.drop_index("ix_state_knowledge_reports_event_id", table_name="state_knowledge_reports")
    op.drop_table("state_knowledge_reports")
    op.drop_index("ix_correction_deliveries_status_retry", table_name="correction_deliveries")
    op.drop_index("ix_correction_deliveries_correction_id", table_name="correction_deliveries")
    op.drop_table("correction_deliveries")
    op.drop_index("ix_corrections_version_to_id", table_name="corrections")
    op.drop_index("ix_corrections_version_from_id", table_name="corrections")
    op.drop_constraint(
        "fk_corrections_version_to_id_event_versions", "corrections", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_corrections_version_from_id_event_versions", "corrections", type_="foreignkey"
    )
    op.drop_column("corrections", "propagation_deadline_at")
    op.drop_constraint("uq_corrections_preview_hash", "corrections", type_="unique")
    op.drop_column("corrections", "preview_hash")
    op.drop_column("corrections", "signed_off_by")
    op.drop_column("corrections", "drafted_by")
    op.drop_column("corrections", "corrective_action")
    op.drop_column("corrections", "root_cause")
    op.drop_column("corrections", "impact")
    op.drop_column("corrections", "version_to_id")
    op.drop_column("corrections", "version_from_id")
    postgresql.ENUM(name="correction_impact", create_type=False).drop(
        op.get_bind(), checkfirst=True
    )
