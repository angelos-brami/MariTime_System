"""Add private publication tables (blueprint work order 7, section 23).

Revision ID: 20260908_0016
Revises: 20260908_0015

Additive only. A validated action intent (immutable proposal), an outbox row (mutable
dispatch state machine with a lease_epoch fencing token), and a case publication (the
immutable accepted private effect, read back to confirm the content hash). The two
snapshot tables carry the existing prevent_immutable_mutation() trigger; the outbox is
mutable. Constraint names follow eastmed_schema.base.NAMING_CONVENTION.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260908_0016"
down_revision = "20260908_0015"
branch_labels = None
depends_on = None

OUTBOX_STATUS = postgresql.ENUM(
    "pending", "claimed", "dispatched", "reconciled", "invalidated", "effect_unknown",
    name="recon_outbox_status", create_type=False,
)
OUTBOX_RESPONSE_CLASS = postgresql.ENUM(
    "accepted", "delivered", "effect_confirmed",
    name="recon_outbox_response_class", create_type=False,
)

_IMMUTABLE_TABLES = (
    "recon_action_intents",
    "recon_case_publications",
)


def upgrade() -> None:
    bind = op.get_bind()
    OUTBOX_STATUS.create(bind, checkfirst=True)
    OUTBOX_RESPONSE_CLASS.create(bind, checkfirst=True)

    op.create_table(
        "recon_action_intents",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_version_id", sa.Uuid(), nullable=False),
        sa.Column("attestation_id", sa.Uuid(), nullable=False),
        sa.Column("action_key", sa.String(length=128), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("destination", sa.String(length=128), nullable=False),
        sa.Column("case_revision", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_action_intents_account_id_accounts"),
        sa.ForeignKeyConstraint(
            ["account_id", "case_id"],
            ["recon_operational_cases.account_id", "recon_operational_cases.id"],
            name="fk_recon_action_intents_case",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "case_version_id"],
            ["recon_case_versions.account_id", "recon_case_versions.id"],
            name="fk_recon_action_intents_case_version",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "attestation_id"],
            ["recon_action_attestations.account_id", "recon_action_attestations.id"],
            name="fk_recon_action_intents_attestation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recon_action_intents"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_action_intents_account_id_id"),
        sa.UniqueConstraint("account_id", "action_key",
                            name="uq_recon_action_intents_action_key"),
    )

    op.create_table(
        "recon_outbox",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("action_key", sa.String(length=128), nullable=False),
        sa.Column("status", OUTBOX_STATUS, nullable=False),
        sa.Column("response_class", OUTBOX_RESPONSE_CLASS, nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False),
        sa.Column("provider_reference", sa.String(length=128), nullable=True),
        sa.Column("terminal_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("dispatch_attempts >= 0",
                           name="ck_recon_outbox_dispatch_attempts_non_negative"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_outbox_account_id_accounts"),
        sa.ForeignKeyConstraint(
            ["account_id", "intent_id"],
            ["recon_action_intents.account_id", "recon_action_intents.id"],
            name="fk_recon_outbox_intent",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recon_outbox"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_outbox_account_id_id"),
        sa.UniqueConstraint("account_id", "intent_id", name="uq_recon_outbox_intent"),
    )
    op.create_index("ix_recon_outbox_due", "recon_outbox",
                    ["account_id", "status", "next_attempt_at"])

    op.create_table(
        "recon_case_publications",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_revision", sa.Integer(), nullable=False),
        sa.Column("destination", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_case_publications_account_id_accounts"),
        sa.ForeignKeyConstraint(
            ["account_id", "intent_id"],
            ["recon_action_intents.account_id", "recon_action_intents.id"],
            name="fk_recon_case_publications_intent",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "case_id"],
            ["recon_operational_cases.account_id", "recon_operational_cases.id"],
            name="fk_recon_case_publications_case",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recon_case_publications"),
        sa.UniqueConstraint("account_id", "id",
                            name="uq_recon_case_publications_account_id_id"),
        sa.UniqueConstraint("account_id", "case_id", "case_revision",
                            name="uq_recon_case_publications_revision"),
    )

    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_update_delete
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
            """
        )


def downgrade() -> None:
    for table in _IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
    op.drop_table("recon_case_publications")
    op.drop_index("ix_recon_outbox_due", table_name="recon_outbox")
    op.drop_table("recon_outbox")
    op.drop_table("recon_action_intents")
    OUTBOX_RESPONSE_CLASS.drop(op.get_bind(), checkfirst=True)
    OUTBOX_STATUS.drop(op.get_bind(), checkfirst=True)
