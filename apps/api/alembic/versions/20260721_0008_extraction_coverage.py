"""Add complete-document extraction coverage and source offsets.

Revision ID: 20260721_0008
Revises: 20260721_0007
"""

import sqlalchemy as sa
from alembic import op

revision = "20260721_0008"
down_revision = "20260721_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claim_extraction_runs",
        sa.Column("document_char_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "claim_extraction_runs",
        sa.Column("processed_char_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "claim_extraction_runs",
        sa.Column("segment_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "claim_extraction_runs",
        sa.Column("segment_manifest_json", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column(
        "claim_extraction_runs",
        sa.Column("coverage_complete", sa.Boolean(), server_default="false", nullable=False),
    )

    # Legacy proposals cannot be assigned trustworthy offsets retroactively. The
    # -1 sentinel makes that explicit; all v2 proposals receive exact offsets.
    for column in ("source_start", "source_end", "segment_index"):
        op.add_column(
            "claim_extraction_proposals",
            sa.Column(column, sa.Integer(), server_default="-1", nullable=False),
        )
        op.alter_column("claim_extraction_proposals", column, server_default=None)


def downgrade() -> None:
    for column in ("segment_index", "source_end", "source_start"):
        op.drop_column("claim_extraction_proposals", column)
    for column in (
        "coverage_complete",
        "segment_manifest_json",
        "segment_count",
        "processed_char_count",
        "document_char_count",
    ):
        op.drop_column("claim_extraction_runs", column)
