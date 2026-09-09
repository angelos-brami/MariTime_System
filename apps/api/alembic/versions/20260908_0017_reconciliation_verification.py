"""Add persisted verification verdict and system-wide budget (work orders 4, 5; sections 19, 31).

Revision ID: 20260908_0017
Revises: 20260908_0016

Additive only.

* ``recon_verification_verdicts`` — the independent verifier's verdict, persisted and
  bound to the exact source (evidence hash), expected job, result (content hash) and
  formula (calculator version) it judged. Immutable: one verdict per case version, carried
  by the existing prevent_immutable_mutation() trigger. The publication gate loads this
  instead of trusting a caller-supplied evidence label.
* ``recon_global_budget_ledgers`` — a system-wide monthly ceiling above the per-run and
  per-account caps, so concurrent agents across tenants cannot together overspend. Mutable
  (a running balance), keyed by period alone.

Constraint names follow eastmed_schema.base.NAMING_CONVENTION.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260908_0017"
down_revision = "20260908_0016"
branch_labels = None
depends_on = None

_IMMUTABLE_TABLES = ("recon_verification_verdicts",)


def upgrade() -> None:
    op.create_table(
        "recon_verification_verdicts",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_version_id", sa.Uuid(), nullable=False),
        sa.Column("expected_job_id", sa.Uuid(), nullable=False),
        sa.Column("case_revision", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("calculator_version", sa.String(length=64), nullable=False),
        sa.Column("verifier_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("completion_status", sa.String(length=32), nullable=False),
        sa.Column("checked", sa.Integer(), nullable=False),
        sa.Column("has_unresolved", sa.Boolean(), nullable=False),
        sa.Column("completes", sa.Boolean(), nullable=False),
        sa.Column("findings_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "checked >= 0",
            name="ck_recon_verification_verdicts_verdict_checked_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"],
            name="fk_recon_verification_verdicts_account_id_accounts",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "case_id"],
            ["recon_operational_cases.account_id", "recon_operational_cases.id"],
            name="fk_recon_verification_verdicts_case",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "case_version_id"],
            ["recon_case_versions.account_id", "recon_case_versions.id"],
            name="fk_recon_verification_verdicts_case_version",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "expected_job_id"],
            ["recon_expected_jobs.account_id", "recon_expected_jobs.id"],
            name="fk_recon_verification_verdicts_expected_job",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recon_verification_verdicts"),
        sa.UniqueConstraint("account_id", "id",
                            name="uq_recon_verification_verdicts_account_id_id"),
        sa.UniqueConstraint("account_id", "case_version_id",
                            name="uq_recon_verification_verdicts_case_version"),
    )

    op.create_table(
        "recon_global_budget_ledgers",
        sa.Column("period_key", sa.String(length=16), nullable=False),
        sa.Column("ceiling_units", sa.Integer(), nullable=False),
        sa.Column("reserved_units", sa.Integer(), nullable=False),
        sa.Column("spent_units", sa.Integer(), nullable=False),
        sa.Column("uncertain_units", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "reserved_units >= 0 AND spent_units >= 0 AND uncertain_units >= 0",
            name="ck_recon_global_budget_ledgers_global_ledger_non_negative",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recon_global_budget_ledgers"),
        sa.UniqueConstraint("period_key", name="uq_recon_global_budget_ledgers_period_key"),
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
    op.drop_table("recon_global_budget_ledgers")
    op.drop_table("recon_verification_verdicts")
