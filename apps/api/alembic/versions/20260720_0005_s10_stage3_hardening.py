"""Add S10 WhatsApp, API access, drills, calendar, and AIS cache.

Revision ID: 20260720_0005
Revises: 20260720_0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260720_0005"
down_revision = "20260720_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    drill_type = postgresql.ENUM(
        "restore", "kill_switch", "injection", "load", name="drill_type", create_type=False
    )
    drill_status = postgresql.ENUM("passed", "failed", name="drill_status", create_type=False)
    calendar_event_type = postgresql.ENUM(
        "strike",
        "port_closure",
        "naval_exercise",
        "weather_window",
        "regulatory_deadline",
        "other",
        name="calendar_event_type",
        create_type=False,
    )
    for enum_type in (drill_type, drill_status, calendar_event_type):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "account_api_keys",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("secret_hash", name="uq_account_api_keys_secret_hash"),
    )
    op.create_index("ix_account_api_keys_account_id", "account_api_keys", ["account_id"])
    op.create_index(
        "ix_account_api_keys_prefix_active",
        "account_api_keys",
        ["key_prefix", "revoked_at"],
    )

    op.create_table(
        "whatsapp_webhook_receipts",
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_ref", sa.String(length=255), nullable=False),
        sa.Column("provider_status", sa.String(length=32), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_hash", name="uq_whatsapp_webhook_receipts_event_hash"),
    )
    op.create_index(
        "ix_whatsapp_webhook_provider_ref",
        "whatsapp_webhook_receipts",
        ["provider_ref"],
    )

    op.create_table(
        "operational_drills",
        sa.Column("drill_type", drill_type, nullable=False),
        sa.Column("status", drill_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_by", sa.String(length=255), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "maritime_calendar_events",
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    corridor = postgresql.ENUM(name="corridor", create_type=False)
    op.create_table(
        "maritime_calendar_event_versions",
        sa.Column("calendar_event_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("event_type", calendar_event_type, nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("corridor", corridor, nullable=False),
        sa.Column("ports_json", sa.JSON(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("public_note", sa.Text(), nullable=False),
        sa.Column("source_record_ids", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["calendar_event_id"], ["maritime_calendar_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "calendar_event_id",
            "version_no",
            name="uq_maritime_calendar_event_versions_event_version",
        ),
    )
    op.create_index(
        "ix_maritime_calendar_event_versions_calendar_event_id",
        "maritime_calendar_event_versions",
        ["calendar_event_id"],
    )
    op.create_index(
        "ix_maritime_calendar_versions_start",
        "maritime_calendar_event_versions",
        ["starts_at", "ends_at"],
    )

    op.create_table(
        "ais_position_cache",
        sa.Column("mmsi", sa.String(length=16), nullable=False),
        sa.Column("imo", sa.String(length=16), nullable=True),
        sa.Column("vessel_name", sa.String(length=255), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("course", sa.Float(), nullable=True),
        sa.Column("speed", sa.Float(), nullable=True),
        sa.Column("navigation_status", sa.String(length=128), nullable=True),
        sa.Column("corridor", corridor, nullable=False),
        sa.Column("message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mmsi"),
    )
    op.create_index(
        "ix_ais_position_cache_corridor_time",
        "ais_position_cache",
        ["corridor", "message_at"],
    )

    for table in (
        "whatsapp_webhook_receipts",
        "operational_drills",
        "maritime_calendar_events",
        "maritime_calendar_event_versions",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_update_delete
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
            """
        )
    op.execute(
        """
        CREATE TRIGGER deliveries_final_no_update_delete
        BEFORE UPDATE OR DELETE ON deliveries
        FOR EACH ROW EXECUTE FUNCTION prevent_final_correction_delivery_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS deliveries_final_no_update_delete ON deliveries")
    for table in (
        "maritime_calendar_event_versions",
        "maritime_calendar_events",
        "operational_drills",
        "whatsapp_webhook_receipts",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
    op.drop_index("ix_ais_position_cache_corridor_time", table_name="ais_position_cache")
    op.drop_table("ais_position_cache")
    op.drop_index(
        "ix_maritime_calendar_versions_start",
        table_name="maritime_calendar_event_versions",
    )
    op.drop_index(
        "ix_maritime_calendar_event_versions_calendar_event_id",
        table_name="maritime_calendar_event_versions",
    )
    op.drop_table("maritime_calendar_event_versions")
    op.drop_table("maritime_calendar_events")
    op.drop_table("operational_drills")
    op.drop_index("ix_whatsapp_webhook_provider_ref", table_name="whatsapp_webhook_receipts")
    op.drop_table("whatsapp_webhook_receipts")
    op.drop_index("ix_account_api_keys_prefix_active", table_name="account_api_keys")
    op.drop_index("ix_account_api_keys_account_id", table_name="account_api_keys")
    op.drop_table("account_api_keys")
    for name in ("calendar_event_type", "drill_status", "drill_type"):
        postgresql.ENUM(name=name, create_type=False).drop(op.get_bind(), checkfirst=True)
