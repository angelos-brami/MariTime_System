"""Real PostgreSQL concurrency tests for the durable runtime (blueprint 21, 31).

These exercise the behaviours SQLite cannot demonstrate: row locking, ``FOR UPDATE``
serialization of budget reservations, ``SKIP LOCKED`` claim dispatch, and lease-expiry
recovery fencing a stale commit across separate transactions.

They require a real PostgreSQL and are skipped otherwise — honestly, rather than pretending
SQLite proves them. Point ``EASTMED_TEST_DATABASE_URL`` at a disposable database to run
them (the CI database job does). Each test scopes its assertions to a fresh account id, so
runs do not interfere and no destructive drop is needed.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from eastmed_pipeline.reconciliation_runtime import (
    CommitStatus,
    claim_task,
    commit_stage,
    create_run,
    enqueue_task,
    ensure_ledger,
    reserve_cost,
)
from eastmed_schema.base import Base
from eastmed_schema.enums import (
    AccountTier,
    AutomationStatus,
    BusinessStatus,
    RecordKind,
    WorkflowStage,
)
from eastmed_schema.models import (
    Account,
    ReconBudgetLedger,
    ReconExpectedJob,
    ReconOperationalCase,
    ReconWorkflowRun,
)
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

_PG_URL = os.environ.get("EASTMED_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _PG_URL.startswith("postgresql"),
    reason="EASTMED_TEST_DATABASE_URL is not set to a PostgreSQL database",
)

BASE = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
LATER = BASE + timedelta(minutes=6)  # past the default 5-minute lease

_NEEDED_TABLES = [
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


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_PG_URL, future=True)
    Base.metadata.create_all(eng, tables=[Base.metadata.tables[n] for n in _NEEDED_TABLES])
    yield eng
    eng.dispose()


def _seed_account_case(session: Session) -> tuple[UUID, ReconOperationalCase]:
    account = Account(
        company="Concurrency Partner",
        tier=AccountTier.DATA,
        contract_start=date(2026, 1, 1),
        contract_end=date(2027, 1, 1),
    )
    session.add(account)
    session.flush()
    job = ReconExpectedJob(
        account_id=account.id,
        vessel_ref="V-pg",
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


def _period(moment: datetime) -> str:
    return moment.strftime("%Y-%m")


def test_concurrent_reservations_respect_the_monthly_ceiling(engine: Engine) -> None:
    """Two agents reserve at the same instant against a ledger that fits only one; the
    ``FOR UPDATE`` lock serializes them so exactly one succeeds — no double spend."""
    with Session(engine) as setup:
        account_id, case = _seed_account_case(setup)
        run = create_run(
            setup, account_id=account_id, case=case, workflow_version="wf",
            system_fingerprint="fp", budget_units=1000, deadline_at=BASE + timedelta(hours=6),
        )
        period = _period(BASE)
        ledger = ensure_ledger(setup, account_id=account_id, period=period, ceiling_units=5)
        run_id, ledger_id = run.id, ledger.id
        setup.commit()

    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def _reserve() -> None:
        with Session(engine) as s:
            s.execute(text("SET lock_timeout = '15s'"))
            run_row = s.get(ReconWorkflowRun, run_id)
            ledger_row = s.get(ReconBudgetLedger, ledger_id)
            assert run_row is not None and ledger_row is not None
            barrier.wait(timeout=15)
            reservation = reserve_cost(
                s, account_id=account_id, run=run_row, ledger=ledger_row, units=4
            )
            s.commit()
            with lock:
                results.append(reservation is not None)

    threads = [threading.Thread(target=_reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results) == [False, True]  # exactly one reservation won the 4-of-5 budget
    with Session(engine) as check:
        final = check.get(ReconBudgetLedger, ledger_id)
        assert final is not None and final.reserved_units == 4


def test_concurrent_claims_take_the_task_at_most_once(engine: Engine) -> None:
    """Two workers race for one due task; ``SKIP LOCKED`` gives it to exactly one and the
    other sees no work rather than grabbing the same row."""
    with Session(engine) as setup:
        account_id, case = _seed_account_case(setup)
        run = create_run(
            setup, account_id=account_id, case=case, workflow_version="wf",
            system_fingerprint="fp", budget_units=100, deadline_at=BASE + timedelta(hours=6),
        )
        enqueue_task(setup, account_id=account_id, run=run, stage=WorkflowStage.RECONCILE, now=BASE)
        setup.commit()

    barrier = threading.Barrier(2)
    claimed: list[bool] = []
    lock = threading.Lock()

    def _claim() -> None:
        with Session(engine) as s:
            s.execute(text("SET lock_timeout = '15s'"))
            barrier.wait(timeout=15)
            claim = claim_task(s, account_id=account_id, now=BASE)
            s.commit()
            with lock:
                claimed.append(claim is not None)

    threads = [threading.Thread(target=_claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(claimed) == [False, True]  # one worker claims; the other skips the locked row


def test_expired_lease_recovery_fences_late_commit(engine: Engine) -> None:
    """A crashed worker's late commit is rejected after a recovery worker claims a newer
    epoch — verified across separate committed transactions, not one in-memory session."""
    with Session(engine) as setup:
        account_id, case = _seed_account_case(setup)
        run = create_run(
            setup, account_id=account_id, case=case, workflow_version="wf",
            system_fingerprint="fp", budget_units=100, deadline_at=BASE + timedelta(hours=6),
        )
        enqueue_task(setup, account_id=account_id, run=run, stage=WorkflowStage.RECONCILE, now=BASE)
        setup.commit()

    with Session(engine) as s_a:
        claim_a = claim_task(s_a, account_id=account_id, now=BASE)
        assert claim_a is not None and claim_a.lease_epoch == 1
        s_a.commit()

    # A separate recovery transaction claims the expired lease at a newer epoch.
    with Session(engine) as s_b:
        claim_b = claim_task(s_b, account_id=account_id, now=LATER)
        assert claim_b is not None and claim_b.lease_epoch == 2
        s_b.commit()

    # The crashed worker's late commit is fenced by the epoch check under the row lock.
    with Session(engine) as s_a2:
        stale = commit_stage(s_a2, account_id=account_id, claim=claim_a, now=LATER)
        s_a2.commit()
        assert stale.status is CommitStatus.FENCED

    with Session(engine) as s_b2:
        fresh = commit_stage(s_b2, account_id=account_id, claim=claim_b, now=LATER)
        s_b2.commit()
        assert fresh.status is CommitStatus.COMMITTED


def test_reservation_race_is_gated_by_environment() -> None:
    """A guard so the file is never silently a no-op: if it ran, PostgreSQL was configured."""
    assert _PG_URL.startswith("postgresql")
