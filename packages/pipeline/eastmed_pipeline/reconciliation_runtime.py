"""Durable orchestration and concurrency (blueprint work order 5, section 21).

PostgreSQL holds the authoritative workflow state; Redis only carries wake-ups, so the
scheduler can reconstruct due work from ``recon_workflow_tasks`` after a queue loss.
Each stage runs as three boundaries: a short claim transaction bumps the lease epoch
and reserves cost; the bounded external call happens outside any row lock; a fresh
commit transaction verifies the lease epoch still matches before saving results.

The lease epoch is a fencing token. A heartbeat renews a lease only while its epoch
matches; once the lease expires a recovery worker claims a newer epoch, and the old
worker's late commit is rejected — its case never advances twice. Budget reservations
are atomic against the run budget and the monthly ledger so concurrent agents cannot
each spend the same remaining balance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from eastmed_schema.enums import (
    AttemptStatus,
    ReservationStatus,
    TaskStatus,
    WorkflowStage,
)
from eastmed_schema.models import (
    ReconBudgetLedger,
    ReconBudgetReservation,
    ReconGlobalBudgetLedger,
    ReconOperationalCase,
    ReconTaskAttempt,
    ReconWorkflowRun,
    ReconWorkflowTask,
)
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

DEFAULT_LEASE = timedelta(minutes=5)
DEFAULT_BACKOFF = timedelta(minutes=1)


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_postgres(session: Session) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


def _get_task_locked(
    session: Session, *, task_id: UUID, account_id: UUID
) -> ReconWorkflowTask | None:
    """Load a task, taking a row lock on PostgreSQL so the epoch/state check and the
    transition that follows are atomic against a competing worker across transactions.
    On SQLite (single-connection tests) the lock is a no-op; the fencing logic is the same."""
    stmt = select(ReconWorkflowTask).where(ReconWorkflowTask.id == task_id)
    if _is_postgres(session):
        stmt = stmt.with_for_update()
    task = session.scalars(stmt).one_or_none()
    if task is None or task.account_id != account_id:
        return None
    return task


def _lock_ledger_row(session: Session, ledger: object) -> None:
    """Take a row lock on a budget ledger on PostgreSQL so the read-check-reserve sequence
    is serialized: two concurrent agents cannot both see the same remaining balance."""
    if _is_postgres(session):
        session.refresh(ledger, with_for_update=True)


def period_key(moment: datetime) -> str:
    return moment.strftime("%Y-%m")


class CommitStatus(StrEnum):
    COMMITTED = "committed"
    FENCED = "fenced"


@dataclass(frozen=True)
class TaskClaim:
    task_id: UUID
    run_id: UUID
    attempt_id: UUID
    attempt_no: int
    lease_epoch: int
    stage: WorkflowStage


@dataclass(frozen=True)
class StageOutcome:
    status: CommitStatus
    next_task_id: UUID | None = None
    terminal: bool = False
    reason: str | None = None


def create_run(
    session: Session,
    *,
    account_id: UUID,
    case: ReconOperationalCase,
    workflow_version: str,
    system_fingerprint: str,
    budget_units: int,
    deadline_at: datetime,
) -> ReconWorkflowRun:
    run = ReconWorkflowRun(
        account_id=account_id,
        case_id=case.id,
        workflow_version=workflow_version,
        system_fingerprint=system_fingerprint,
        budget_units=budget_units,
        deadline_at=deadline_at,
    )
    session.add(run)
    session.flush()
    return run


def enqueue_task(
    session: Session,
    *,
    account_id: UUID,
    run: ReconWorkflowRun,
    stage: WorkflowStage,
    priority: int = 0,
    next_attempt_at: datetime | None = None,
    max_attempts: int = 3,
    now: datetime | None = None,
) -> ReconWorkflowTask:
    moment = _now(now)
    task = ReconWorkflowTask(
        account_id=account_id,
        run_id=run.id,
        stage=stage,
        status=TaskStatus.READY,
        priority=priority,
        next_attempt_at=next_attempt_at or moment,
        lease_epoch=0,
        lease_until=None,
        attempts=0,
        max_attempts=max_attempts,
    )
    session.add(task)
    session.flush()
    return task


def _due_query(account_id: UUID, now: datetime):  # type: ignore[no-untyped-def]
    return (
        select(ReconWorkflowTask)
        .where(
            ReconWorkflowTask.account_id == account_id,
            or_(
                and_(
                    ReconWorkflowTask.status == TaskStatus.READY,
                    ReconWorkflowTask.next_attempt_at <= now,
                ),
                and_(
                    ReconWorkflowTask.status == TaskStatus.LEASED,
                    ReconWorkflowTask.lease_until.is_not(None),
                    ReconWorkflowTask.lease_until < now,
                    # An expired lease is reclaimable only within the attempt ceiling; a
                    # task that already used every attempt is reaped to DEAD, not reclaimed.
                    ReconWorkflowTask.attempts < ReconWorkflowTask.max_attempts,
                ),
            ),
        )
        .order_by(
            ReconWorkflowTask.priority.desc(),
            ReconWorkflowTask.next_attempt_at,
            ReconWorkflowTask.id,
        )
    )


def find_due_tasks(
    session: Session, *, account_id: UUID, now: datetime | None = None, limit: int = 20
) -> list[ReconWorkflowTask]:
    """Scheduler view: durable due work, reconstructable without the queue."""
    return list(session.scalars(_due_query(account_id, _now(now)).limit(limit)).all())


def reap_expired_tasks(
    session: Session, *, account_id: UUID, now: datetime | None = None
) -> int:
    """Mark expired leases that have exhausted their attempt budget as DEAD.

    A worker that crashed on its final attempt leaves a LEASED task whose lease expires but
    which must never be reclaimed — reclaiming it would run beyond ``max_attempts``. Recovery
    is finite: this is the terminal stop, not silent retry (blueprint 12)."""
    moment = _now(now)
    rows = session.scalars(
        select(ReconWorkflowTask).where(
            ReconWorkflowTask.account_id == account_id,
            ReconWorkflowTask.status == TaskStatus.LEASED,
            ReconWorkflowTask.lease_until.is_not(None),
            ReconWorkflowTask.lease_until < moment,
            ReconWorkflowTask.attempts >= ReconWorkflowTask.max_attempts,
        )
    ).all()
    for task in rows:
        task.status = TaskStatus.DEAD
        task.terminal_reason = "attempts_exhausted"
        task.lease_until = None
    if rows:
        session.flush()
    return len(rows)


def claim_task(
    session: Session,
    *,
    account_id: UUID,
    now: datetime | None = None,
    lease: timedelta = DEFAULT_LEASE,
) -> TaskClaim | None:
    """Boundary 1: claim one due task, bump its lease epoch, open an attempt.

    A due task is a READY task past its next_attempt_at or a LEASED task whose lease has
    expired (recovery). On PostgreSQL the row is locked with SKIP LOCKED so competing
    workers never grab the same task; SQLite tests exercise the single-worker path and
    the epoch fencing logic that the concurrency ultimately protects."""
    moment = _now(now)
    # First retire any expired-but-exhausted task so it is never reclaimed past the ceiling.
    reap_expired_tasks(session, account_id=account_id, now=moment)
    stmt = _due_query(account_id, moment).limit(1)
    if _is_postgres(session):
        stmt = stmt.with_for_update(skip_locked=True)
    task = session.scalars(stmt).first()
    if task is None:
        return None

    task.lease_epoch += 1
    task.status = TaskStatus.LEASED
    task.lease_until = moment + lease
    task.attempts += 1
    attempt = ReconTaskAttempt(
        account_id=account_id,
        task_id=task.id,
        attempt_no=task.attempts,
        lease_epoch=task.lease_epoch,
        lease_until=task.lease_until,
        status=AttemptStatus.RUNNING,
    )
    session.add(attempt)
    session.flush()
    return TaskClaim(
        task_id=task.id,
        run_id=task.run_id,
        attempt_id=attempt.id,
        attempt_no=task.attempts,
        lease_epoch=task.lease_epoch,
        stage=task.stage,
    )


def heartbeat(
    session: Session,
    *,
    account_id: UUID,
    claim: TaskClaim,
    now: datetime | None = None,
    lease: timedelta = DEFAULT_LEASE,
) -> bool:
    """Renew the lease only while this claim still holds the current, unexpired lease.

    A lapsed lease is forfeit: once ``lease_until`` has passed, a recovery worker is
    entitled to claim a newer epoch, so the original holder cannot renew it back to life.
    The row lock makes the expiry/epoch check and the renewal atomic against that claim."""
    moment = _now(now)
    task = _get_task_locked(session, task_id=claim.task_id, account_id=account_id)
    if task is None:
        return False
    if task.lease_epoch != claim.lease_epoch or task.status is not TaskStatus.LEASED:
        return False
    if task.lease_until is None or _as_utc(task.lease_until) < moment:
        return False
    task.lease_until = moment + lease
    session.flush()
    return True


def _load_attempt(session: Session, claim: TaskClaim) -> ReconTaskAttempt | None:
    return session.get(ReconTaskAttempt, claim.attempt_id)


def commit_stage(
    session: Session,
    *,
    account_id: UUID,
    claim: TaskClaim,
    now: datetime | None = None,
    next_stage: WorkflowStage | None = None,
    request_id: str | None = None,
    artifact: dict[str, object] | None = None,
) -> StageOutcome:
    """Boundary 3: commit only if this claim still holds the current lease epoch.

    A stale worker (older epoch, or the task already advanced) is fenced: its attempt is
    recorded as FENCED and the case is not advanced. The winning worker marks its
    attempt COMMITTED, completes the task, and enqueues the next stage atomically."""
    moment = _now(now)
    task = _get_task_locked(session, task_id=claim.task_id, account_id=account_id)
    attempt = _load_attempt(session, claim)
    if task is None or attempt is None:
        return StageOutcome(status=CommitStatus.FENCED, reason="missing task/attempt")

    if task.lease_epoch != claim.lease_epoch or task.status is not TaskStatus.LEASED:
        attempt.status = AttemptStatus.FENCED
        session.flush()
        return StageOutcome(status=CommitStatus.FENCED, reason="stale lease epoch")

    attempt.status = AttemptStatus.COMMITTED
    attempt.request_id = request_id
    attempt.artifact_json = artifact
    task.status = TaskStatus.SUCCEEDED
    task.lease_until = None

    next_task_id: UUID | None = None
    if next_stage is not None:
        run = session.get(ReconWorkflowRun, task.run_id)
        assert run is not None  # noqa: S101 - composite FK guarantees the run exists
        follow = enqueue_task(
            session,
            account_id=account_id,
            run=run,
            stage=next_stage,
            priority=task.priority,
            now=moment,
        )
        next_task_id = follow.id
    session.flush()
    return StageOutcome(status=CommitStatus.COMMITTED, next_task_id=next_task_id)


def fail_attempt(
    session: Session,
    *,
    account_id: UUID,
    claim: TaskClaim,
    reason: str,
    now: datetime | None = None,
    backoff: timedelta = DEFAULT_BACKOFF,
) -> StageOutcome:
    """Record a failed attempt. Retry within the attempt budget, else stop terminally.

    Recovery is never an unlimited loop: once max_attempts is reached the task is DEAD
    with a terminal reason, a finite stopping path (blueprint 12)."""
    moment = _now(now)
    task = _get_task_locked(session, task_id=claim.task_id, account_id=account_id)
    attempt = _load_attempt(session, claim)
    if task is None or attempt is None:
        return StageOutcome(status=CommitStatus.FENCED, reason="missing task/attempt")
    if task.lease_epoch != claim.lease_epoch or task.status is not TaskStatus.LEASED:
        attempt.status = AttemptStatus.FENCED
        session.flush()
        return StageOutcome(status=CommitStatus.FENCED, reason="stale lease epoch")

    attempt.status = AttemptStatus.FAILED
    task.lease_until = None
    if task.attempts >= task.max_attempts:
        task.status = TaskStatus.DEAD
        task.terminal_reason = reason
        session.flush()
        return StageOutcome(status=CommitStatus.COMMITTED, terminal=True, reason=reason)
    task.status = TaskStatus.READY
    task.next_attempt_at = moment + backoff
    session.flush()
    return StageOutcome(status=CommitStatus.COMMITTED, terminal=False, reason=reason)


# --- Atomic budget reservation (blueprint section 31) ----------------------------


def ensure_ledger(
    session: Session, *, account_id: UUID, period: str, ceiling_units: int
) -> ReconBudgetLedger:
    ledger = session.scalars(
        select(ReconBudgetLedger).where(
            ReconBudgetLedger.account_id == account_id,
            ReconBudgetLedger.period_key == period,
        )
    ).one_or_none()
    if ledger is None:
        ledger = ReconBudgetLedger(
            account_id=account_id,
            period_key=period,
            ceiling_units=ceiling_units,
            reserved_units=0,
            spent_units=0,
            uncertain_units=0,
        )
        session.add(ledger)
        session.flush()
    return ledger


def ensure_global_ledger(
    session: Session, *, period: str, ceiling_units: int
) -> ReconGlobalBudgetLedger:
    """The system-wide monthly ledger for a period (across all tenants), above the per-run
    and per-account caps (blueprint 31)."""
    ledger = session.scalars(
        select(ReconGlobalBudgetLedger).where(ReconGlobalBudgetLedger.period_key == period)
    ).one_or_none()
    if ledger is None:
        ledger = ReconGlobalBudgetLedger(
            period_key=period,
            ceiling_units=ceiling_units,
            reserved_units=0,
            spent_units=0,
            uncertain_units=0,
        )
        session.add(ledger)
        session.flush()
    return ledger


def _run_committed(session: Session, run_id: UUID, account_id: UUID) -> int:
    total = session.scalar(
        select(
            func.coalesce(
                func.sum(
                    ReconBudgetReservation.reserved_units
                    + ReconBudgetReservation.spent_units
                    + ReconBudgetReservation.uncertain_units
                ),
                0,
            )
        ).where(
            ReconBudgetReservation.account_id == account_id,
            ReconBudgetReservation.run_id == run_id,
        )
    )
    return int(total or 0)


def _outstanding(ledger: ReconBudgetLedger | ReconGlobalBudgetLedger) -> int:
    return ledger.reserved_units + ledger.spent_units + ledger.uncertain_units


def reserve_cost(
    session: Session,
    *,
    account_id: UUID,
    run: ReconWorkflowRun,
    ledger: ReconBudgetLedger,
    units: int,
    task_id: UUID | None = None,
    global_ledger: ReconGlobalBudgetLedger | None = None,
) -> ReconBudgetReservation | None:
    """Reserve ``units`` atomically against the run budget, the account monthly ledger and,
    when supplied, the system-wide monthly ledger.

    Returns the reservation, or None when any ceiling would be exceeded. On PostgreSQL the
    ledger rows are locked FOR UPDATE before the balances are read, so the whole
    check-and-reserve is serialized and two concurrent agents cannot both spend the same
    remaining balance. The lock and the write are one transaction."""
    if units < 0:
        raise ValueError("cannot reserve negative units")
    # Lock the ledger rows first so the balance we read is the one we reserve against. A
    # consistent lock order (account ledger, then global) avoids a deadlock cycle.
    _lock_ledger_row(session, ledger)
    if global_ledger is not None:
        _lock_ledger_row(session, global_ledger)

    if _run_committed(session, run.id, account_id) + units > run.budget_units:
        return None
    if _outstanding(ledger) + units > ledger.ceiling_units:
        return None
    if global_ledger is not None and (
        _outstanding(global_ledger) + units > global_ledger.ceiling_units
    ):
        return None

    reservation = ReconBudgetReservation(
        account_id=account_id,
        run_id=run.id,
        task_id=task_id,
        period_key=ledger.period_key,
        reserved_units=units,
        spent_units=0,
        uncertain_units=0,
        status=ReservationStatus.RESERVED,
    )
    session.add(reservation)
    ledger.reserved_units += units
    if global_ledger is not None:
        global_ledger.reserved_units += units
    session.flush()
    return reservation


def settle_cost(
    session: Session,
    *,
    reservation: ReconBudgetReservation,
    ledger: ReconBudgetLedger,
    actual_units: int,
    global_ledger: ReconGlobalBudgetLedger | None = None,
) -> None:
    """Record actual spend and release the unused remainder back to every ceiling."""
    if reservation.status is not ReservationStatus.RESERVED:
        raise ValueError("only a reserved allocation can be settled")
    spend = max(0, min(actual_units, reservation.reserved_units))
    held = reservation.reserved_units
    ledger.reserved_units -= held
    ledger.spent_units += spend
    if global_ledger is not None:
        global_ledger.reserved_units -= held
        global_ledger.spent_units += spend
    reservation.reserved_units = 0
    reservation.spent_units = spend
    reservation.status = ReservationStatus.SETTLED
    session.flush()


def release_cost(
    session: Session,
    *,
    reservation: ReconBudgetReservation,
    ledger: ReconBudgetLedger,
    global_ledger: ReconGlobalBudgetLedger | None = None,
) -> None:
    if reservation.status is not ReservationStatus.RESERVED:
        raise ValueError("only a reserved allocation can be released")
    ledger.reserved_units -= reservation.reserved_units
    if global_ledger is not None:
        global_ledger.reserved_units -= reservation.reserved_units
    reservation.reserved_units = 0
    reservation.status = ReservationStatus.RELEASED
    session.flush()


def mark_uncertain(
    session: Session,
    *,
    reservation: ReconBudgetReservation,
    ledger: ReconBudgetLedger,
    global_ledger: ReconGlobalBudgetLedger | None = None,
) -> None:
    """An ambiguous paid timeout keeps a bounded uncertain allocation; a retry needs a
    fresh reservation (blueprint 21, 31)."""
    if reservation.status is not ReservationStatus.RESERVED:
        raise ValueError("only a reserved allocation can become uncertain")
    held = reservation.reserved_units
    ledger.reserved_units -= held
    ledger.uncertain_units += held
    if global_ledger is not None:
        global_ledger.reserved_units -= held
        global_ledger.uncertain_units += held
    reservation.reserved_units = 0
    reservation.uncertain_units = held
    reservation.status = ReservationStatus.UNCERTAIN
    session.flush()
