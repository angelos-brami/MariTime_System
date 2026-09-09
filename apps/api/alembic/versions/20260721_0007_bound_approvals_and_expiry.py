"""Add content-bound desk approvals and expiring AI capability controls.

Revision ID: 20260721_0007
Revises: 20260720_0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_0007"
down_revision = "20260720_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    auth_assurance = postgresql.ENUM(
        "password",
        "mfa",
        "phishing_resistant",
        "service",
        name="auth_assurance",
        create_type=False,
    )
    op.create_table(
        "desk_approvals",
        sa.Column("approval_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("binding_hash", sa.String(length=64), nullable=False),
        sa.Column("binding_payload_json", sa.JSON(), nullable=False),
        sa.Column("primary_user_id", sa.Uuid(), nullable=False),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("auth_assurance", auth_assurance, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "expires_at > approved_at",
            name="approval_expiry_after_approval",
        ),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["desk_users.id"]),
        sa.ForeignKeyConstraint(["primary_user_id"], ["desk_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_desk_approvals_target_binding",
        "desk_approvals",
        ["approval_type", "target_id", "binding_hash"],
    )
    op.execute(
        """
        CREATE TRIGGER desk_approvals_no_update_delete
        BEFORE UPDATE OR DELETE ON desk_approvals
        FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
        """
    )

    op.add_column(
        "event_versions",
        sa.Column("publication_approval_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_event_versions_publication_approval_id_desk_approvals",
        "event_versions",
        "desk_approvals",
        ["publication_approval_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_event_versions_publication_approval_id",
        "event_versions",
        ["publication_approval_id"],
    )

    # Existing enabled rows deliberately receive NULL and therefore fail closed
    # until a new, explicitly time-bounded control revision is created.
    op.add_column(
        "ai_capability_controls",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_capability_controls", "expires_at")
    op.drop_constraint(
        "uq_event_versions_publication_approval_id",
        "event_versions",
        type_="unique",
    )
    op.drop_constraint(
        "fk_event_versions_publication_approval_id_desk_approvals",
        "event_versions",
        type_="foreignkey",
    )
    op.drop_column("event_versions", "publication_approval_id")
    op.execute("DROP TRIGGER IF EXISTS desk_approvals_no_update_delete ON desk_approvals")
    op.drop_index("ix_desk_approvals_target_binding", table_name="desk_approvals")
    op.drop_table("desk_approvals")
