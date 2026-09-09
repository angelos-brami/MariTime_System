"""Add immutable AI incident lifecycle records.

Revision ID: 20260721_0012
Revises: 20260721_0011
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_0012"
down_revision = "20260721_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_incidents",
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("capability_scope", sa.String(length=128), nullable=False),
        sa.Column("system_version_id", sa.Uuid(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["reported_by_user_id"], ["desk_users.id"]),
        sa.ForeignKeyConstraint(["system_version_id"], ["ai_system_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_incidents_scope_detected",
        "ai_incidents",
        ["capability_scope", "detected_at"],
    )
    op.create_index("ix_ai_incidents_created_at", "ai_incidents", ["created_at"])
    op.create_table(
        "ai_incident_events",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("containment_action", sa.Text(), nullable=True),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("changed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "auth_assurance",
            postgresql.ENUM(
                "password",
                "mfa",
                "phishing_resistant",
                "service",
                name="auth_assurance",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'contained', 'investigating', 'resolved')",
            name="ai_incident_event_status_valid",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ai_incident_event_severity_valid",
        ),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["desk_users.id"]),
        sa.ForeignKeyConstraint(["incident_id"], ["ai_incidents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "sequence", name="uq_ai_incident_events_sequence"),
    )
    op.create_index(
        "ix_ai_incident_events_incident_time",
        "ai_incident_events",
        ["incident_id", "at"],
    )
    for table in ("ai_incidents", "ai_incident_events"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_update_delete
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
            """
        )


def downgrade() -> None:
    for table in ("ai_incident_events", "ai_incidents"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
    op.drop_index("ix_ai_incident_events_incident_time", table_name="ai_incident_events")
    op.drop_table("ai_incident_events")
    op.drop_index("ix_ai_incidents_created_at", table_name="ai_incidents")
    op.drop_index("ix_ai_incidents_scope_detected", table_name="ai_incidents")
    op.drop_table("ai_incidents")
