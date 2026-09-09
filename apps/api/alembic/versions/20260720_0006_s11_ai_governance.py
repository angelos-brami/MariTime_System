"""Add S11 desk identity, AI fingerprints, and fail-closed capability controls.

Revision ID: 20260720_0006
Revises: 20260720_0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260720_0006"
down_revision = "20260720_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    desk_role = postgresql.ENUM(
        "analyst",
        "senior_analyst",
        "administrator",
        "security",
        "compliance",
        "service",
        name="desk_role",
        create_type=False,
    )
    auth_assurance = postgresql.ENUM(
        "password",
        "mfa",
        "phishing_resistant",
        "service",
        name="auth_assurance",
        create_type=False,
    )
    capability_mode = postgresql.ENUM(
        "disabled",
        "shadow",
        "assisted",
        "automated",
        name="ai_capability_mode",
        create_type=False,
    )
    for enum_type in (desk_role, auth_assurance, capability_mode):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "desk_users",
        sa.Column("auth_issuer", sa.String(length=255), nullable=False),
        sa.Column("auth_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("role", desk_role, nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_desk_users_email"),
        sa.UniqueConstraint("auth_issuer", "auth_subject", name="uq_desk_users_issuer_subject"),
    )
    op.create_index("ix_desk_users_active_role", "desk_users", ["active", "role"])

    op.create_table(
        "ai_system_versions",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("manifest_schema_version", sa.String(length=16), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["desk_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_ai_system_versions_fingerprint"),
    )
    op.create_index("ix_ai_system_versions_created_at", "ai_system_versions", ["created_at"])

    op.create_table(
        "ai_capability_controls",
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("mode", capability_mode, nullable=False),
        sa.Column("risk_tier", sa.Integer(), nullable=False),
        sa.Column("system_version_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("approval_refs_json", sa.JSON(), nullable=False),
        sa.Column("changed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("auth_assurance", auth_assurance, nullable=False),
        sa.Column("previous_control_id", sa.Uuid(), nullable=True),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("risk_tier BETWEEN 0 AND 4", name="risk_tier_range"),
        sa.CheckConstraint(
            "scope = 'all_model_calls' OR mode = 'disabled' OR system_version_id IS NOT NULL",
            name="enabled_scope_has_system_version",
        ),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["desk_users.id"]),
        sa.ForeignKeyConstraint(["previous_control_id"], ["ai_capability_controls.id"]),
        sa.ForeignKeyConstraint(["system_version_id"], ["ai_system_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "revision", name="uq_ai_capability_controls_scope_revision"),
    )
    op.create_index(
        "ix_ai_capability_controls_scope_time",
        "ai_capability_controls",
        ["scope", "changed_at"],
    )

    for table in ("ai_system_versions", "ai_capability_controls"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_update_delete
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
            """
        )


def downgrade() -> None:
    for table in ("ai_capability_controls", "ai_system_versions"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
    op.drop_index("ix_ai_capability_controls_scope_time", table_name="ai_capability_controls")
    op.drop_table("ai_capability_controls")
    op.drop_index("ix_ai_system_versions_created_at", table_name="ai_system_versions")
    op.drop_table("ai_system_versions")
    op.drop_index("ix_desk_users_active_role", table_name="desk_users")
    op.drop_table("desk_users")
    for name in ("ai_capability_mode", "auth_assurance", "desk_role"):
        postgresql.ENUM(name=name, create_type=False).drop(op.get_bind(), checkfirst=True)
