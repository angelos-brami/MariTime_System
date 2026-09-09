"""Add machine authority tables (blueprint work order 6, sections 12, 17, 22).

Revision ID: 20260908_0015
Revises: 20260908_0014

Additive only. Service principals (machine identities distinct from DeskUser),
standing grants (provisioned authority, never created at runtime), and action
attestations that record both allows and denials as machine-readable receipts.
Constraint names follow eastmed_schema.base.NAMING_CONVENTION.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260908_0015"
down_revision = "20260908_0014"
branch_labels = None
depends_on = None

ATTESTATION_DECISION = postgresql.ENUM(
    "allow", "deny", name="recon_attestation_decision", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    ATTESTATION_DECISION.create(bind, checkfirst=True)

    op.create_table(
        "recon_service_principals",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("issuer", sa.String(length=128), nullable=False),
        sa.Column("audience", sa.String(length=128), nullable=False),
        sa.Column("allowed_scopes", sa.JSON(), nullable=False),
        sa.Column("deployment_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_service_principals_account_id_accounts"),
        sa.PrimaryKeyConstraint("id", name="pk_recon_service_principals"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_service_principals_account_id_id"),
        sa.UniqueConstraint("account_id", "subject",
                            name="uq_recon_service_principals_subject"),
    )

    op.create_table(
        "recon_standing_grants",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.String(length=128), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("allowed_case_types", sa.JSON(), nullable=False),
        sa.Column("allowed_destinations", sa.JSON(), nullable=False),
        sa.Column("external_messages", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("financial_commitments", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("required_evidence", sa.JSON(), nullable=False),
        sa.Column("required_artifact", sa.String(length=128), nullable=False),
        sa.Column("qualified_fingerprints", sa.JSON(), nullable=False),
        sa.Column("attestation_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("signing_key_id", sa.String(length=64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("valid_to > valid_from",
                           name="ck_recon_standing_grants_grant_interval_order"),
        sa.CheckConstraint("attestation_ttl_seconds > 0",
                           name="ck_recon_standing_grants_grant_ttl_positive"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_standing_grants_account_id_accounts"),
        sa.PrimaryKeyConstraint("id", name="pk_recon_standing_grants"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_standing_grants_account_id_id"),
        sa.UniqueConstraint("account_id", "policy_id", "policy_revision",
                            name="uq_recon_standing_grants_revision"),
    )

    op.create_table(
        "recon_action_attestations",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("grant_id", sa.Uuid(), nullable=True),
        sa.Column("principal_id", sa.Uuid(), nullable=True),
        sa.Column("action_key", sa.String(length=128), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_revision", sa.Integer(), nullable=False),
        sa.Column("destination", sa.String(length=128), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=128), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("system_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("signing_key_id", sa.String(length=64), nullable=False),
        sa.Column("decision", ATTESTATION_DECISION, nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_action_attestations_account_id_accounts"),
        sa.ForeignKeyConstraint(
            ["account_id", "grant_id"],
            ["recon_standing_grants.account_id", "recon_standing_grants.id"],
            name="fk_recon_action_attestations_grant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recon_action_attestations"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_action_attestations_account_id_id"),
    )
    op.create_index("ix_recon_action_attestations_action_key", "recon_action_attestations",
                    ["account_id", "action_key"])


def downgrade() -> None:
    op.drop_index("ix_recon_action_attestations_action_key",
                  table_name="recon_action_attestations")
    op.drop_table("recon_action_attestations")
    op.drop_table("recon_standing_grants")
    op.drop_table("recon_service_principals")
    ATTESTATION_DECISION.drop(op.get_bind(), checkfirst=True)
