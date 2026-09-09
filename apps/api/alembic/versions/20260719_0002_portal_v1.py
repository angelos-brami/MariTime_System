"""Add subscriber portal search, identity, and daily briefs.

Revision ID: 20260719_0002
Revises: 20260719_0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260719_0002"
down_revision = "20260719_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("auth_subject", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column(
            "portal_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_index("ix_users_auth_subject", "users", ["auth_subject"], unique=True)
    op.create_check_constraint(
        "account_contract_date_order",
        "accounts",
        "contract_end >= contract_start",
    )

    op.add_column(
        "event_versions",
        sa.Column(
            "search_document",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple', "
                "coalesce(title, '') || ' ' || "
                "coalesce(summary_confirmed, '') || ' ' || "
                "coalesce(summary_reported, '') || ' ' || "
                "coalesce(summary_unknown, '') || ' ' || "
                "coalesce(whats_changed, ''))",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_event_versions_search_document",
        "event_versions",
        ["search_document"],
        unique=False,
        postgresql_using="gin",
    )

    brief_status = postgresql.ENUM("draft", "finalized", name="brief_status", create_type=False)
    brief_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "daily_briefs",
        sa.Column("brief_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("introduction", sa.Text(), nullable=False),
        sa.Column("forward_watch", sa.Text(), nullable=False),
        sa.Column(
            "status",
            brief_status,
            server_default="draft",
            nullable=False,
        ),
        sa.Column("items_json", sa.JSON(), nullable=False),
        sa.Column("source_version_ids", sa.JSON(), nullable=False),
        sa.Column("compiled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("compiled_by", sa.String(length=255), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_by", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("brief_date"),
        sa.CheckConstraint(
            "status <> 'finalized' OR (finalized_at IS NOT NULL AND finalized_by IS NOT NULL)",
            name="daily_brief_finalized_has_reviewer",
        ),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_finalized_brief_mutation()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.status = 'finalized' THEN
            RAISE EXCEPTION 'finalized daily brief is immutable';
          END IF;
          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER daily_briefs_finalized_no_update_delete
        BEFORE UPDATE OR DELETE ON daily_briefs
        FOR EACH ROW EXECUTE FUNCTION prevent_finalized_brief_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS daily_briefs_finalized_no_update_delete ON daily_briefs")
    op.drop_table("daily_briefs")
    op.execute("DROP FUNCTION IF EXISTS prevent_finalized_brief_mutation()")
    postgresql.ENUM(name="brief_status", create_type=False).drop(op.get_bind(), checkfirst=True)
    op.drop_index("ix_event_versions_search_document", table_name="event_versions")
    op.drop_column("event_versions", "search_document")
    op.drop_constraint("account_contract_date_order", "accounts", type_="check")
    op.drop_index("ix_users_auth_subject", table_name="users")
    op.drop_column("users", "portal_enabled")
    op.drop_column("users", "active")
    op.drop_column("users", "auth_subject")
