"""Bind sensitive-claim second review to an immutable approval action.

Revision ID: 20260721_0009
Revises: 20260721_0008
"""

import sqlalchemy as sa
from alembic import op

revision = "20260721_0009"
down_revision = "20260721_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claims",
        sa.Column("second_review_approval_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_claims_second_review_approval_id_desk_approvals",
        "claims",
        "desk_approvals",
        ["second_review_approval_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_claims_second_review_approval_id",
        "claims",
        ["second_review_approval_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_claims_second_review_approval_id",
        "claims",
        type_="unique",
    )
    op.drop_constraint(
        "fk_claims_second_review_approval_id_desk_approvals",
        "claims",
        type_="foreignkey",
    )
    op.drop_column("claims", "second_review_approval_id")
