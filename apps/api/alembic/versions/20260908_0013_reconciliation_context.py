"""Add private fleet reconciliation context tables (blueprint work order 2).

Revision ID: 20260908_0013
Revises: 20260721_0012

Additive only. Introduces the recon_* namespace for the autonomous analytical path:
tenant-scoped memberships, voyages/versions, expected jobs, immutable source
captures and observations, operational cases and immutable case versions. Every
private parent has UNIQUE(account_id, id) and children carry composite foreign keys
so a reference proves same-tenant membership (blueprint 16, 17). Immutable tables get
the existing prevent_immutable_mutation() trigger.

Constraint names follow eastmed_schema.base.NAMING_CONVENTION so alembic autogenerate
stays quiet against the models.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260908_0013"
down_revision = "20260721_0012"
branch_labels = None
depends_on = None


RECORD_KIND = postgresql.ENUM(
    "fleet_membership", "voyage_plan", "daily_report", name="recon_record_kind", create_type=False
)
FUEL_GRADE = postgresql.ENUM(
    "HSFO", "VLSFO", "ULSFO", "LSMGO", "MGO", "MDO", "LNG", "LPG", "METHANOL", "BIOFUEL",
    name="recon_fuel_grade", create_type=False,
)
QUANTITY_UNIT = postgresql.ENUM(
    "tonne", "kilogram", "m3", "litre", name="recon_quantity_unit", create_type=False
)
AUTHORITY_ROLE = postgresql.ENUM(
    "owner", "operator", "technical_manager", "charterer",
    name="recon_authority_role", create_type=False,
)
VALUE_ORIGIN = postgresql.ENUM(
    "reported", "calculated", "assumed", name="recon_value_origin", create_type=False
)
AUTOMATION_STATUS = postgresql.ENUM(
    "awaiting_arrival", "received", "validated", "reconciled", "calculated",
    "publication_ready", "published", "verified_complete", "waiting_for_machine_data",
    "recovering", "unresolved", "disabled",
    name="recon_automation_status", create_type=False,
)
BUSINESS_STATUS = postgresql.ENUM(
    "pending", "reconciled", "variance_present", "unresolved", "superseded",
    name="recon_business_status", create_type=False,
)

_ENUMS = (
    RECORD_KIND,
    FUEL_GRADE,
    QUANTITY_UNIT,
    AUTHORITY_ROLE,
    VALUE_ORIGIN,
    AUTOMATION_STATUS,
    BUSINESS_STATUS,
)

_IMMUTABLE_TABLES = (
    "recon_source_captures",
    "recon_observations",
    "recon_voyage_versions",
    "recon_case_versions",
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "recon_vessel_memberships",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("vessel_ref", sa.String(length=128), nullable=False),
        sa.Column("authority_role", AUTHORITY_ROLE, nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_tz", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("valid_to > valid_from",
                           name="ck_recon_vessel_memberships_interval_order"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_vessel_memberships_account_id_accounts"),
        sa.PrimaryKeyConstraint("id", name="pk_recon_vessel_memberships"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_vessel_memberships_account_id_id"),
        sa.UniqueConstraint("account_id", "vessel_ref", "valid_from",
                            name="uq_recon_vessel_memberships_vessel_from"),
    )
    op.create_index("ix_recon_vessel_memberships_account_vessel", "recon_vessel_memberships",
                    ["account_id", "vessel_ref"])

    op.create_table(
        "recon_voyages",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("voyage_ref", sa.String(length=128), nullable=False),
        sa.Column("vessel_ref", sa.String(length=128), nullable=False),
        sa.Column("source_system", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_voyages_account_id_accounts"),
        sa.PrimaryKeyConstraint("id", name="pk_recon_voyages"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_voyages_account_id_id"),
        sa.UniqueConstraint("account_id", "source_system", "voyage_ref",
                            name="uq_recon_voyages_account_source_ref"),
    )

    op.create_table(
        "recon_voyage_versions",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("voyage_id", sa.Uuid(), nullable=False),
        sa.Column("plan_revision", sa.String(length=128), nullable=False),
        sa.Column("source_revision", sa.String(length=128), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("effective_to > effective_from",
                           name="ck_recon_voyage_versions_interval_order"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_voyage_versions_account_id_accounts"),
        sa.ForeignKeyConstraint(["account_id", "voyage_id"],
                                ["recon_voyages.account_id", "recon_voyages.id"],
                                name="fk_recon_voyage_versions_voyage"),
        sa.PrimaryKeyConstraint("id", name="pk_recon_voyage_versions"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_voyage_versions_account_id_id"),
        sa.UniqueConstraint("account_id", "voyage_id", "plan_revision",
                            name="uq_recon_voyage_versions_revision"),
    )

    op.create_table(
        "recon_expected_jobs",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("vessel_ref", sa.String(length=128), nullable=False),
        sa.Column("voyage_ref", sa.String(length=128), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column("result_contract_version", sa.String(length=64), nullable=False),
        sa.Column("required_record_kind", RECORD_KIND, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("period_end > period_start",
                           name="ck_recon_expected_jobs_period_order"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_expected_jobs_account_id_accounts"),
        sa.PrimaryKeyConstraint("id", name="pk_recon_expected_jobs"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_expected_jobs_account_id_id"),
        sa.UniqueConstraint("account_id", "vessel_ref", "required_record_kind", "period_start",
                            "period_end", name="uq_recon_expected_jobs_natural_key"),
    )
    op.create_index("ix_recon_expected_jobs_account_due", "recon_expected_jobs",
                    ["account_id", "due_at"])

    op.create_table(
        "recon_source_captures",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("record_kind", RECORD_KIND, nullable=False),
        sa.Column("source_system", sa.String(length=128), nullable=False),
        sa.Column("external_ref", sa.String(length=255), nullable=False),
        sa.Column("source_revision", sa.String(length=128), nullable=False),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_source_captures_account_id_accounts"),
        sa.PrimaryKeyConstraint("id", name="pk_recon_source_captures"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_source_captures_account_id_id"),
        sa.UniqueConstraint("account_id", "source_system", "external_ref", "source_revision",
                            name="uq_recon_source_captures_revision"),
    )
    op.create_index("ix_recon_source_captures_account_captured", "recon_source_captures",
                    ["account_id", "captured_at"])

    op.create_table(
        "recon_observations",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("capture_id", sa.Uuid(), nullable=False),
        sa.Column("expected_job_id", sa.Uuid(), nullable=True),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("item_key", sa.String(length=128), nullable=False),
        sa.Column("fuel_grade", FUEL_GRADE, nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("unit", QUANTITY_UNIT, nullable=False),
        sa.Column("origin", VALUE_ORIGIN, nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_pointer", sa.String(length=255), nullable=False),
        sa.Column("normalization_version", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("period_end > period_start",
                           name="ck_recon_observations_period_order"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_observations_account_id_accounts"),
        sa.ForeignKeyConstraint(["account_id", "capture_id"],
                                ["recon_source_captures.account_id", "recon_source_captures.id"],
                                name="fk_recon_observations_capture"),
        sa.ForeignKeyConstraint(["account_id", "expected_job_id"],
                                ["recon_expected_jobs.account_id", "recon_expected_jobs.id"],
                                name="fk_recon_observations_expected_job"),
        sa.PrimaryKeyConstraint("id", name="pk_recon_observations"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_observations_account_id_id"),
    )
    op.create_index("ix_recon_observations_account_job", "recon_observations",
                    ["account_id", "expected_job_id"])

    op.create_table(
        "recon_operational_cases",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("expected_job_id", sa.Uuid(), nullable=False),
        sa.Column("automation_status", AUTOMATION_STATUS, nullable=False),
        sa.Column("business_status", BUSINESS_STATUS, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_operational_cases_account_id_accounts"),
        sa.ForeignKeyConstraint(["account_id", "expected_job_id"],
                                ["recon_expected_jobs.account_id", "recon_expected_jobs.id"],
                                name="fk_recon_operational_cases_expected_job"),
        sa.PrimaryKeyConstraint("id", name="pk_recon_operational_cases"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_operational_cases_account_id_id"),
        sa.UniqueConstraint("account_id", "expected_job_id",
                            name="uq_recon_operational_cases_expected_job"),
    )

    op.create_table(
        "recon_case_versions",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("supersedes_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_case_versions_account_id_accounts"),
        sa.ForeignKeyConstraint(["account_id", "case_id"],
                                ["recon_operational_cases.account_id",
                                 "recon_operational_cases.id"],
                                name="fk_recon_case_versions_case"),
        sa.PrimaryKeyConstraint("id", name="pk_recon_case_versions"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_case_versions_account_id_id"),
        sa.UniqueConstraint("account_id", "case_id", "revision",
                            name="uq_recon_case_versions_revision"),
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
    op.drop_table("recon_case_versions")
    op.drop_table("recon_operational_cases")
    op.drop_index("ix_recon_observations_account_job", table_name="recon_observations")
    op.drop_table("recon_observations")
    op.drop_index("ix_recon_source_captures_account_captured", table_name="recon_source_captures")
    op.drop_table("recon_source_captures")
    op.drop_index("ix_recon_expected_jobs_account_due", table_name="recon_expected_jobs")
    op.drop_table("recon_expected_jobs")
    op.drop_table("recon_voyage_versions")
    op.drop_table("recon_voyages")
    op.drop_index("ix_recon_vessel_memberships_account_vessel",
                  table_name="recon_vessel_memberships")
    op.drop_table("recon_vessel_memberships")
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.drop(bind, checkfirst=True)
