from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from eastmed_pipeline.reconciliation_runtime import (
    CommitStatus,
    claim_task,
    commit_stage,
    create_run,
    enqueue_task,
    ensure_global_ledger,
    ensure_ledger,
    fail_attempt,
    find_due_tasks,
    heartbeat,
    mark_uncertain,
    period_key,
    reap_expired_tasks,
    reserve_cost,
    settle_cost,
)
from eastmed_schema.base import Base
from eastmed_schema.enums import (
    AccountTier,
    AttemptStatus,
    AutomationStatus,
    BusinessStatus,
    RecordKind,
    TaskStatus,
    WorkflowStage,
)
from eastmed_schema.models import (
    Account,
    ReconExpectedJob,
    ReconOperationalCase,
    ReconTaskAttempt,
    ReconWorkflowRun,
    ReconWorkflowTask,
)
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

BASE = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
LATER = BASE + timedelta(minutes=6)  # past the default 5-minute lease
PERIOD = period_key(BASE)

RECON_TABLES = [
    "accounts",
    "recon_expected_jobs",
    "recon_operational_cases",
    "recon_workflow_runs",
    "recon_workflow_tasks",
    "recon_task_attempts",
    "recon_budget_ledgers",
    "recon_budget_reservations",
    "recon_global_budget_ledgers",
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection: Any, _: Any) -> None:  # pragma: no cover - setup
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[Base.metadata.tables[n] for n in RECON_TABLES])
    with Session(engine) as active:
        yield active


def seed_case(session: Session) -> tuple[UUID, ReconOperationalCase]:
    account = Account(
        company="Design Partner",
        tier=AccountTier.DATA,
        contract_start=date(2026, 1, 1),
        contract_end=date(2027, 1, 1),
    )
    session.add(account)
    session.flush()
    job = ReconExpectedJob(
        account_id=account.id,
        vessel_ref="V3",
        period_start=BASE,
        period_end=BASE + timedelta(days=1),
        due_at=BASE + timedelta(hours=6),
        contract_version="fleet_reconciliation_v1",
        result_contract_version="result_contract_v1",
        required_record_kind=RecordKind.DAILY_REPORT,
    )
    session.add(job)
    session.flush()
    case = ReconOperationalCase(
        account_id=account.id,
        expected_job_id=job.id,
        automation_status=AutomationStatus.RECONCILED,
        business_status=BusinessStatus.PENDING,
        revision=1,
        deadline_at=BASE + timedelta(hours=6),
    )
    session.add(case)
    session.flush()
    return account.id, case


def seed_run_task(
    session: Session, *, budget_units: int = 100, max_attempts: int = 3
) -> tuple[UUID, ReconWorkflowRun, ReconWorkflowTask]:
    account_id, case = seed_case(session)
    run = create_run(
        session,
        account_id=account_id,
        case=case,
        workflow_version="wf_v1",
        system_fingerprint="fp",
        budget_units=budget_units,
        deadline_at=BASE + timedelta(hours=6),
    )
    task = enqueue_task(
        session,
        account_id=account_id,
        run=run,
        stage=WorkflowStage.RECONCILE,
        max_attempts=max_attempts,
        now=BASE,
    )
    return account_id, run, task


# --- Scheduler / queue-loss --------------------------------------------------------


def test_scheduler_finds_due_task_without_a_queue(session: Session) -> None:
    account_id, _, task = seed_run_task(session)
    due = find_due_tasks(session, account_id=account_id, now=BASE)
    assert [t.id for t in due] == [task.id]


def test_future_task_is_not_due(session: Session) -> None:
    account_id, run, _ = seed_run_task(session)
    enqueue_task(
        session,
        account_id=account_id,
        run=run,
        stage=WorkflowStage.CALCULATE,
        next_attempt_at=BASE + timedelta(hours=1),
        now=BASE,
    )
    due = find_due_tasks(session, account_id=account_id, now=BASE)
    assert all(t.stage is not WorkflowStage.CALCULATE for t in due)


# --- Crash / replay / fencing (the work-order-5 gate) ------------------------------


def test_expired_lease_recovery_fences_the_crashed_worker(session: Session) -> None:
    account_id, _, task = seed_run_task(session)

    claim_a = claim_task(session, account_id=account_id, now=BASE)
    assert claim_a is not None and claim_a.lease_epoch == 1

    # Worker A crashes before committing; its lease expires and a recovery worker claims
    # a newer epoch.
    claim_b = claim_task(session, account_id=account_id, now=LATER)
    assert claim_b is not None and claim_b.lease_epoch == 2

    # The crashed worker's late commit is rejected.
    stale = commit_stage(session, account_id=account_id, claim=claim_a, now=LATER)
    assert stale.status is CommitStatus.FENCED

    # The recovery worker commits exactly once and advances to the next stage.
    fresh = commit_stage(
        session, account_id=account_id, claim=claim_b, now=LATER, next_stage=WorkflowStage.CALCULATE
    )
    assert fresh.status is CommitStatus.COMMITTED
    assert fresh.next_task_id is not None

    refreshed = session.get(ReconWorkflowTask, task.id)
    assert refreshed is not None and refreshed.status is TaskStatus.SUCCEEDED

    attempts = session.scalars(
        select(ReconTaskAttempt).where(ReconTaskAttempt.task_id == task.id)
    ).all()
    committed = [a for a in attempts if a.status is AttemptStatus.COMMITTED]
    fenced = [a for a in attempts if a.status is AttemptStatus.FENCED]
    assert len(committed) == 1  # one logical outcome
    assert len(fenced) == 1


def test_double_commit_is_fenced(session: Session) -> None:
    account_id, _, _ = seed_run_task(session)
    claim = claim_task(session, account_id=account_id, now=BASE)
    assert claim is not None
    first = commit_stage(session, account_id=account_id, claim=claim, now=BASE)
    assert first.status is CommitStatus.COMMITTED
    second = commit_stage(session, account_id=account_id, claim=claim, now=BASE)
    assert second.status is CommitStatus.FENCED


def test_heartbeat_renews_only_for_current_epoch(session: Session) -> None:
    account_id, _, _ = seed_run_task(session)
    claim_a = claim_task(session, account_id=account_id, now=BASE)
    assert claim_a is not None
    assert heartbeat(session, account_id=account_id, claim=claim_a, now=BASE) is True

    claim_b = claim_task(session, account_id=account_id, now=LATER)
    assert claim_b is not None
    # A's lease was superseded; its heartbeat no longer renews.
    assert heartbeat(session, account_id=account_id, claim=claim_a, now=LATER) is False


def test_only_current_lease_holder_advances_a_single_claim(session: Session) -> None:
    account_id, _, task = seed_run_task(session)
    claim = claim_task(session, account_id=account_id, now=BASE)
    assert claim is not None
    # No competing claim yet: a fresh commit succeeds.
    assert commit_stage(session, account_id=account_id, claim=claim, now=BASE).status is (
        CommitStatus.COMMITTED
    )


# --- Finite recovery ---------------------------------------------------------------


def test_attempts_exhaust_to_dead(session: Session) -> None:
    account_id, _, task = seed_run_task(session, max_attempts=2)

    claim1 = claim_task(session, account_id=account_id, now=BASE)
    assert claim1 is not None
    retry = fail_attempt(
        session, account_id=account_id, claim=claim1, reason="transient", now=BASE
    )
    assert retry.terminal is False
    assert session.get(ReconWorkflowTask, task.id).status is TaskStatus.READY  # type: ignore[union-attr]

    claim2 = claim_task(session, account_id=account_id, now=BASE + timedelta(minutes=2))
    assert claim2 is not None
    dead = fail_attempt(
        session, account_id=account_id, claim=claim2, reason="transient",
        now=BASE + timedelta(minutes=2),
    )
    assert dead.terminal is True
    final = session.get(ReconWorkflowTask, task.id)
    assert final is not None and final.status is TaskStatus.DEAD
    assert final.terminal_reason == "transient"


# --- Atomic budget -----------------------------------------------------------------


def test_reservations_cannot_exceed_run_budget(session: Session) -> None:
    account_id, run, _ = seed_run_task(session, budget_units=10)
    ledger = ensure_ledger(session, account_id=account_id, period=PERIOD, ceiling_units=1000)

    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=6) is not None
    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=6) is None
    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=4) is not None
    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=1) is None


def test_reservations_cannot_exceed_monthly_ledger(session: Session) -> None:
    account_id, run, _ = seed_run_task(session, budget_units=1000)
    ledger = ensure_ledger(session, account_id=account_id, period=PERIOD, ceiling_units=5)
    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=3) is not None
    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=3) is None


def test_settle_releases_unused_remainder(session: Session) -> None:
    account_id, run, _ = seed_run_task(session, budget_units=10)
    ledger = ensure_ledger(session, account_id=account_id, period=PERIOD, ceiling_units=1000)
    reservation = reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=6)
    assert reservation is not None
    settle_cost(session, reservation=reservation, ledger=ledger, actual_units=4)
    assert ledger.spent_units == 4
    assert ledger.reserved_units == 0
    # Only 4 is now committed, so a fresh 6-unit reservation fits within the budget of 10.
    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=6) is not None


def test_uncertain_allocation_is_retained_and_blocks_double_spend(session: Session) -> None:
    account_id, run, _ = seed_run_task(session, budget_units=10)
    ledger = ensure_ledger(session, account_id=account_id, period=PERIOD, ceiling_units=1000)
    reservation = reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=6)
    assert reservation is not None
    mark_uncertain(session, reservation=reservation, ledger=ledger)
    assert ledger.uncertain_units == 6
    # A retry after the ambiguous timeout needs a fresh reservation and cannot reclaim
    # the uncertain 6 units: only 4 remain.
    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=6) is None
    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=4) is not None


# --- Attempt ceiling: recovery is finite and never runs past max_attempts ----------


def test_expired_exhausted_task_is_reaped_to_dead_not_reclaimed(session: Session) -> None:
    account_id, _, task = seed_run_task(session, max_attempts=1)
    claim = claim_task(session, account_id=account_id, now=BASE)
    assert claim is not None and claim.lease_epoch == 1
    # The worker crashes without failing its (only) attempt; the lease expires. Recovery
    # must NOT reclaim it — that would run beyond max_attempts=1 — so it is retired to DEAD.
    recovered = claim_task(session, account_id=account_id, now=LATER)
    assert recovered is None
    final = session.get(ReconWorkflowTask, task.id)
    assert final is not None and final.status is TaskStatus.DEAD
    assert final.terminal_reason == "attempts_exhausted"


def test_reap_leaves_a_reclaimable_expired_task_alone(session: Session) -> None:
    account_id, _, task = seed_run_task(session, max_attempts=3)
    claim_task(session, account_id=account_id, now=BASE)  # attempts=1 of 3
    # The lease expired but attempts remain: legitimate recovery, not exhaustion.
    assert reap_expired_tasks(session, account_id=account_id, now=LATER) == 0
    still_leased = session.get(ReconWorkflowTask, task.id)
    assert still_leased is not None and still_leased.status is TaskStatus.LEASED
    recovered = claim_task(session, account_id=account_id, now=LATER)
    assert recovered is not None and recovered.lease_epoch == 2


# --- Lease expiry: a lapsed lease is forfeit ---------------------------------------


def test_heartbeat_refuses_an_expired_lease(session: Session) -> None:
    account_id, _, _ = seed_run_task(session)
    claim = claim_task(session, account_id=account_id, now=BASE)
    assert claim is not None
    # No competing claim yet, but the lease itself has lapsed: it cannot be renewed back to
    # life, because a recovery worker is now entitled to claim a newer epoch.
    assert heartbeat(session, account_id=account_id, claim=claim, now=LATER) is False


# --- System-wide monthly budget ----------------------------------------------------


def test_reservations_cannot_exceed_global_monthly_ceiling(session: Session) -> None:
    account_id, run, _ = seed_run_task(session, budget_units=1000)
    ledger = ensure_ledger(session, account_id=account_id, period=PERIOD, ceiling_units=1000)
    global_ledger = ensure_global_ledger(session, period=PERIOD, ceiling_units=5)
    # The per-run and per-account caps are generous; the system-wide cap of 5 binds.
    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=3,
                        global_ledger=global_ledger) is not None
    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=3,
                        global_ledger=global_ledger) is None
    assert global_ledger.reserved_units == 3
    assert reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=2,
                        global_ledger=global_ledger) is not None
    assert global_ledger.reserved_units == 5


def test_settle_returns_units_to_the_global_ledger(session: Session) -> None:
    account_id, run, _ = seed_run_task(session, budget_units=1000)
    ledger = ensure_ledger(session, account_id=account_id, period=PERIOD, ceiling_units=1000)
    global_ledger = ensure_global_ledger(session, period=PERIOD, ceiling_units=10)
    reservation = reserve_cost(session, account_id=account_id, run=run, ledger=ledger, units=6,
                               global_ledger=global_ledger)
    assert reservation is not None and global_ledger.reserved_units == 6
    settle_cost(session, reservation=reservation, ledger=ledger, actual_units=4,
                global_ledger=global_ledger)
    assert global_ledger.reserved_units == 0
    assert global_ledger.spent_units == 4
