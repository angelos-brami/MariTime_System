"""Add persistent two-person approval requests.

Revision ID: 20260721_0011
Revises: 20260721_0010
"""

import sqlalchemy as sa
from alembic import op

revision = "20260721_0011"
down_revision = "20260721_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "desk_approval_requests",
        sa.Column("request_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_payload_json", sa.JSON(), nullable=False),
        sa.Column("primary_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("request_reason", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')",
            name="approval_request_status_valid",
        ),
        sa.CheckConstraint(
            "expires_at > requested_at",
            name="approval_request_expiry_after_request",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL AND decided_by_user_id IS NULL) "
            "OR (status <> 'pending' AND decided_at IS NOT NULL)",
            name="approval_request_decision_state_consistent",
        ),
        sa.ForeignKeyConstraint(["approval_id"], ["desk_approvals.id"]),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["desk_users.id"]),
        sa.ForeignKeyConstraint(["primary_user_id"], ["desk_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_id", name="uq_desk_approval_requests_approval_id"),
    )
    op.create_index(
        "ix_desk_approval_requests_queue",
        "desk_approval_requests",
        ["status", "request_type", "requested_at"],
    )
    op.create_index(
        "ix_desk_approval_requests_primary_target",
        "desk_approval_requests",
        ["primary_user_id", "target_id", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_desk_approval_requests_primary_target",
        table_name="desk_approval_requests",
    )
    op.drop_index("ix_desk_approval_requests_queue", table_name="desk_approval_requests")
    op.drop_table("desk_approval_requests")
