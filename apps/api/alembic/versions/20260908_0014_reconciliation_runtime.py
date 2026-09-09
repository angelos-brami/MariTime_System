"""Add durable orchestration runtime tables (blueprint work order 5, section 21).

Revision ID: 20260908_0014
Revises: 20260908_0013

Additive only. Workflow runs, the durable task queue with a lease epoch fencing token,
per-attempt records, and atomic budget ledgers/reservations. These tables are mutable
(state machine + lease/budget bookkeeping) so they carry no immutability trigger; the
fencing invariant is enforced in application code and by the lease_epoch column.
Constraint names follow eastmed_schema.base.NAMING_CONVENTION.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260908_0014"
down_revision = "20260908_0013"
branch_labels = None
depends_on = None


WORKFLOW_STAGE = postgresql.ENUM(
    "reconcile", "calculate", "verify", "explain", "publish",
    name="recon_workflow_stage", create_type=False,
)
TASK_STATUS = postgresql.ENUM(
    "ready", "leased", "waiting", "succeeded", "failed", "dead",
    name="recon_task_status", create_type=False,
)
ATTEMPT_STATUS = postgresql.ENUM(
    "running", "committed", "fenced", "failed", name="recon_attempt_status", create_type=False
)
RESERVATION_STATUS = postgresql.ENUM(
    "reserved", "settled", "released", "uncertain",
    name="recon_reservation_status", create_type=False,
)

_ENUMS = (WORKFLOW_STAGE, TASK_STATUS, ATTEMPT_STATUS, RESERVATION_STATUS)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "recon_workflow_runs",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_version", sa.String(length=64), nullable=False),
        sa.Column("system_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("budget_units", sa.Integer(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("budget_units >= 0", name="ck_recon_workflow_runs_budget_non_negative"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_workflow_runs_account_id_accounts"),
        sa.ForeignKeyConstraint(
            ["account_id", "case_id"],
            ["recon_operational_cases.account_id", "recon_operational_cases.id"],
            name="fk_recon_workflow_runs_case",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recon_workflow_runs"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_workflow_runs_account_id_id"),
    )

    op.create_table(
        "recon_workflow_tasks",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("stage", WORKFLOW_STAGE, nullable=False),
        sa.Column("status", TASK_STATUS, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("terminal_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("attempts >= 0 AND max_attempts >= 1",
                           name="ck_recon_workflow_tasks_attempt_counts_valid"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_workflow_tasks_account_id_accounts"),
        sa.ForeignKeyConstraint(
            ["account_id", "run_id"],
            ["recon_workflow_runs.account_id", "recon_workflow_runs.id"],
            name="fk_recon_workflow_tasks_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recon_workflow_tasks"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_workflow_tasks_account_id_id"),
    )
    op.create_index("ix_recon_workflow_tasks_due", "recon_workflow_tasks",
                    ["account_id", "status", "next_attempt_at"])

    op.create_table(
        "recon_task_attempts",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", ATTEMPT_STATUS, nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("artifact_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_task_attempts_account_id_accounts"),
        sa.ForeignKeyConstraint(
            ["account_id", "task_id"],
            ["recon_workflow_tasks.account_id", "recon_workflow_tasks.id"],
            name="fk_recon_task_attempts_task",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recon_task_attempts"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_task_attempts_account_id_id"),
        sa.UniqueConstraint("account_id", "task_id", "attempt_no",
                            name="uq_recon_task_attempts_attempt_no"),
    )

    op.create_table(
        "recon_budget_ledgers",
        sa.Column("account_id", sa.Uuid(), nullable=False),
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
            name="ck_recon_budget_ledgers_ledger_non_negative",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_budget_ledgers_account_id_accounts"),
        sa.PrimaryKeyConstraint("id", name="pk_recon_budget_ledgers"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_budget_ledgers_account_id_id"),
        sa.UniqueConstraint("account_id", "period_key", name="uq_recon_budget_ledgers_period"),
    )

    op.create_table(
        "recon_budget_reservations",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("period_key", sa.String(length=16), nullable=False),
        sa.Column("reserved_units", sa.Integer(), nullable=False),
        sa.Column("spent_units", sa.Integer(), nullable=False),
        sa.Column("uncertain_units", sa.Integer(), nullable=False),
        sa.Column("status", RESERVATION_STATUS, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "reserved_units >= 0 AND spent_units >= 0 AND uncertain_units >= 0",
            name="ck_recon_budget_reservations_reservation_non_negative",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"],
                                name="fk_recon_budget_reservations_account_id_accounts"),
        sa.ForeignKeyConstraint(
            ["account_id", "run_id"],
            ["recon_workflow_runs.account_id", "recon_workflow_runs.id"],
            name="fk_recon_budget_reservations_run",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recon_budget_reservations"),
        sa.UniqueConstraint("account_id", "id", name="uq_recon_budget_reservations_account_id_id"),
    )


def downgrade() -> None:
    op.drop_table("recon_budget_reservations")
    op.drop_table("recon_budget_ledgers")
    op.drop_table("recon_task_attempts")
    op.drop_index("ix_recon_workflow_tasks_due", table_name="recon_workflow_tasks")
    op.drop_table("recon_workflow_tasks")
    op.drop_table("recon_workflow_runs")
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.drop(bind, checkfirst=True)
