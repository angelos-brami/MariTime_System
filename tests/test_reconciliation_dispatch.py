"""The connected reconciliation runtime: scheduled input to private report, no button press.

Drives real imported fixtures through ``run_reconciliation_pass`` and asserts the case
reaches ``verified_complete`` with a persisted publication — and that the same durable state
lets a fresh process resume mid-workflow, withholds an unresolved case honestly, and never
produces a duplicate effect on a second pass.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from eastmed_pipeline.reconciliation_dispatch import (
    DispatchResult,
    execute_next,
    run_reconciliation_pass,
)
from eastmed_pipeline.reconciliation_import import ImportStatus, import_record
from eastmed_schema.base import Base
from eastmed_schema.enums import (
    AccountTier,
    AuthorityRole,
    AutomationStatus,
    BusinessStatus,
    RecordKind,
)
from eastmed_schema.models import (
    Account,
    ReconCasePublication,
    ReconCaseVersion,
    ReconOperationalCase,
    ReconServicePrincipal,
    ReconStandingGrant,
    ReconVesselMembership,
)
from eastmed_shared.reconciliation.contract import CONTRACT_VERSION
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
PERIOD_START = "2026-09-07T00:00:00Z"
PERIOD_END = "2026-09-08T00:00:00Z"
FP = "fp-dispatch"
CAP = "operations.private_publish"
SUBJECT = "svc-1"

RECON_TABLES = [name for name in Base.metadata.tables if name.startswith("recon_")] + ["accounts"]


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine("sqlite:///:memory:")

    @event.listens_for(eng, "connect")
    def _fk(dbapi_connection: Any, _: Any) -> None:  # pragma: no cover - setup
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng, tables=[Base.metadata.tables[n] for n in RECON_TABLES])
    yield eng
    eng.dispose()


def _seed_authority(session: Session, account_id: UUID) -> None:
    session.add(
        ReconServicePrincipal(
            account_id=account_id, subject=SUBJECT, issuer="provisioning", audience="operations",
            allowed_scopes=[CAP], deployment_fingerprint=FP, active=True,
        )
    )
    session.add(
        ReconStandingGrant(
            account_id=account_id, policy_id="tenant-routine-operations-v1", policy_revision=1,
            capability=CAP, allowed_case_types=["daily_reconciliation"],
            allowed_destinations=["tenant_private_portal"], external_messages=False,
            financial_commitments=False,
            required_evidence=["required_inputs_complete", "accepted_facts"],
            required_artifact="reproducible_calculation_receipt", qualified_fingerprints=[FP],
            attestation_ttl_seconds=600, signing_key_id="key-1",
            valid_from=NOW - timedelta(hours=1), valid_to=NOW + timedelta(hours=1), active=True,
        )
    )
    session.flush()


def _seed_environment(session: Session) -> UUID:
    account = Account(
        company="Design Partner", tier=AccountTier.DATA,
        contract_start=date(2026, 1, 1), contract_end=date(2027, 1, 1),
    )
    session.add(account)
    session.flush()
    account_id = account.id
    session.add(
        ReconVesselMembership(
            account_id=account_id, vessel_ref="V3", authority_role=AuthorityRole.OPERATOR,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC), valid_to=datetime(2027, 1, 1, tzinfo=UTC),
        )
    )
    session.flush()
    # Freeze the expected job before the report arrives (blueprint 05).
    from eastmed_schema.models import ReconExpectedJob

    session.add(
        ReconExpectedJob(
            account_id=account_id, vessel_ref="V3", voyage_ref="P4",
            period_start=datetime(2026, 9, 7, tzinfo=UTC),
            period_end=datetime(2026, 9, 8, tzinfo=UTC),
            due_at=NOW, contract_version=CONTRACT_VERSION,
            result_contract_version="result_contract_v1",
            required_record_kind=RecordKind.DAILY_REPORT,
        )
    )
    _seed_authority(session, account_id)
    return account_id


def _voyage_plan(planned: str = "23.000") -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_VERSION,
        "record_kind": "voyage_plan",
        "source_system": "planner",
        "external_ref": "P4",
        "source_revision": "1",
        "vessel_ref": "V3",
        "recorded_at": "2026-09-06T00:00:00Z",
        "voyage_ref": "P4",
        "plan_revision": "rev-1",
        "effective_from": "2026-09-07T00:00:00Z",
        "effective_to": "2026-09-14T00:00:00Z",
        "items": [{
            "item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": planned, "unit": "tonne",
            "period_start": PERIOD_START, "period_end": PERIOD_END,
        }],
    }


def _daily_report(actual: str = "24.600") -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_VERSION,
        "record_kind": "daily_report",
        "source_system": "noon-connector",
        "external_ref": "R17",
        "source_revision": "1",
        "vessel_ref": "V3",
        "recorded_at": "2026-09-08T06:00:00Z",
        "voyage_ref": "P4",
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "items": [{
            "item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": actual, "unit": "tonne",
        }],
    }


def _ingest(session: Session, account_id: UUID, *, with_plan: bool = True) -> None:
    if with_plan:
        assert import_record(
            session, account_id=account_id, payload=_voyage_plan(), now=NOW
        ).status is ImportStatus.ACCEPTED
    outcome = import_record(session, account_id=account_id, payload=_daily_report(), now=NOW)
    assert outcome.status is ImportStatus.ACCEPTED and outcome.case_id is not None


def _pass(session: Session, account_id: UUID) -> Any:
    return run_reconciliation_pass(
        session, account_id=account_id, principal_subject=SUBJECT, system_fingerprint=FP, now=NOW
    )


def _publications(session: Session, account_id: UUID) -> int:
    return session.scalar(
        select(func.count()).select_from(ReconCasePublication).where(
            ReconCasePublication.account_id == account_id
        )
    ) or 0


def _versions(session: Session, account_id: UUID) -> int:
    return session.scalar(
        select(func.count()).select_from(ReconCaseVersion).where(
            ReconCaseVersion.account_id == account_id
        )
    ) or 0


# --- End to end: a scheduled input reaches a private report with no button press --------


def test_scheduled_pass_drives_a_case_to_verified_complete(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = _seed_environment(session)
        _ingest(session, account_id)
        # The case is only imported/validated; nobody pressed publish.
        case = session.scalars(
            select(ReconOperationalCase).where(ReconOperationalCase.account_id == account_id)
        ).one()
        assert case.automation_status is AutomationStatus.VALIDATED

        summary = _pass(session, account_id)

        assert summary.scheduled == 1
        assert summary.completed == 1
        refreshed = session.get(ReconOperationalCase, case.id)
        assert refreshed is not None
        assert refreshed.automation_status is AutomationStatus.VERIFIED_COMPLETE
        assert _publications(session, account_id) == 1


def test_workflow_resumes_across_a_restart(engine: Engine) -> None:
    # Process 1: schedule + run only the RECONCILE stage, then "crash".
    with Session(engine) as s1:
        account_id = _seed_environment(s1)
        _ingest(s1, account_id)
        from eastmed_pipeline.reconciliation_dispatch import enqueue_ready_cases

        enqueue_ready_cases(s1, account_id=account_id, system_fingerprint=FP, now=NOW)
        step = execute_next(
            s1, account_id=account_id, principal_subject=SUBJECT, system_fingerprint=FP, now=NOW
        )
        assert step.result is DispatchResult.ADVANCED  # reconcile done, publish enqueued
        case_id = step.case_id
        s1.commit()

    # Process 2: a fresh session sees only durable state and resumes to completion.
    with Session(engine) as s2:
        assert _publications(s2, account_id) == 0  # not published before the restart
        summary = _pass(s2, account_id)
        assert summary.scheduled == 0  # already scheduled; nothing new to enqueue
        assert summary.completed == 1
        assert case_id is not None
        case = s2.get(ReconOperationalCase, case_id)
        assert case is not None and case.automation_status is AutomationStatus.VERIFIED_COMPLETE
        assert _publications(s2, account_id) == 1


def test_unresolved_case_is_withheld_not_published(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = _seed_environment(session)
        _ingest(session, account_id, with_plan=False)  # a report with no matching plan

        summary = _pass(session, account_id)

        assert summary.unresolved == 1
        assert summary.completed == 0
        case = session.scalars(
            select(ReconOperationalCase).where(ReconOperationalCase.account_id == account_id)
        ).one()
        assert case.business_status is BusinessStatus.UNRESOLVED
        assert case.automation_status is not AutomationStatus.VERIFIED_COMPLETE
        assert _publications(session, account_id) == 0


def test_second_pass_produces_no_duplicate_effect(engine: Engine) -> None:
    with Session(engine) as session:
        account_id = _seed_environment(session)
        _ingest(session, account_id)
        first = _pass(session, account_id)
        assert first.completed == 1

        # A second scheduler pass finds no due work and changes nothing.
        second = _pass(session, account_id)
        assert second.scheduled == 0
        assert all(step.result is DispatchResult.IDLE for step in second.steps) or not second.steps
        assert _publications(session, account_id) == 1
        assert _versions(session, account_id) == 1
