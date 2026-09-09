"""Bind operational corrections to a one-use immutable approval.

Revision ID: 20260721_0010
Revises: 20260721_0009
"""

import sqlalchemy as sa
from alembic import op

revision = "20260721_0010"
down_revision = "20260721_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "corrections",
        sa.Column("correction_approval_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_corrections_correction_approval_id_desk_approvals",
        "corrections",
        "desk_approvals",
        ["correction_approval_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_corrections_correction_approval_id",
        "corrections",
        ["correction_approval_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_corrections_correction_approval_id",
        "corrections",
        type_="unique",
    )
    op.drop_constraint(
        "fk_corrections_correction_approval_id_desk_approvals",
        "corrections",
        type_="foreignkey",
    )
    op.drop_column("corrections", "correction_approval_id")
