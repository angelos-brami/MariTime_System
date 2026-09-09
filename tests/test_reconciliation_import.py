from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from eastmed_pipeline.reconciliation_import import ImportStatus, import_record
from eastmed_schema.base import Base
from eastmed_schema.enums import (
    AccountTier,
    AuthorityRole,
    FuelGrade,
    QuantityUnit,
    RecordKind,
    ValueOrigin,
)
from eastmed_schema.models import (
    Account,
    ReconExpectedJob,
    ReconObservation,
    ReconOperationalCase,
    ReconSourceCapture,
    ReconVesselMembership,
)
from eastmed_shared.reconciliation.contract import CONTRACT_VERSION, RejectionReason
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

PERIOD_START = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)
PERIOD_END = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)
DUE_AT = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)

RECON_TABLES = [
    "accounts",
    "recon_vessel_memberships",
    "recon_voyages",
    "recon_voyage_versions",
    "recon_expected_jobs",
    "recon_source_captures",
    "recon_observations",
    "recon_operational_cases",
    "recon_case_versions",
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection: Any, _: Any) -> None:  # pragma: no cover - setup
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    _create(engine)
    with Session(engine) as active:
        yield active


def _create(engine: Engine) -> None:
    Base.metadata.create_all(
        engine, tables=[Base.metadata.tables[name] for name in RECON_TABLES]
    )


def make_account(session: Session) -> UUID:
    account = Account(
        company="Design Partner Shipping",
        tier=AccountTier.DATA,
        contract_start=date(2026, 1, 1),
        contract_end=date(2027, 1, 1),
    )
    session.add(account)
    session.flush()
    return account.id


def add_membership(
    session: Session, account_id: UUID, vessel_ref: str = "V3"
) -> None:
    session.add(
        ReconVesselMembership(
            account_id=account_id,
            vessel_ref=vessel_ref,
            authority_role=AuthorityRole.OPERATOR,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_to=datetime(2027, 1, 1, tzinfo=UTC),
        )
    )
    session.flush()


def add_expected_job(
    session: Session, account_id: UUID, vessel_ref: str = "V3"
) -> UUID:
    job = ReconExpectedJob(
        account_id=account_id,
        vessel_ref=vessel_ref,
        voyage_ref="P4",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        due_at=DUE_AT,
        contract_version=CONTRACT_VERSION,
        result_contract_version="result_contract_v1",
        required_record_kind=RecordKind.DAILY_REPORT,
    )
    session.add(job)
    session.flush()
    return job.id


def daily_report(
    *, external_ref: str = "R17", source_revision: str = "1", quantity: str = "24.600"
) -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_VERSION,
        "record_kind": "daily_report",
        "source_system": "noon-connector",
        "external_ref": external_ref,
        "source_revision": source_revision,
        "vessel_ref": "V3",
        "recorded_at": "2026-09-08T06:00:00Z",
        "voyage_ref": "P4",
        "period_start": "2026-09-07T00:00:00Z",
        "period_end": "2026-09-08T00:00:00Z",
        "items": [
            {"item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": quantity, "unit": "tonne"}
        ],
    }


def _observation_count(session: Session, account_id: UUID) -> int:
    return len(
        session.scalars(
            select(ReconObservation).where(ReconObservation.account_id == account_id)
        ).all()
    )


def _capture_count(session: Session, account_id: UUID) -> int:
    return len(
        session.scalars(
            select(ReconSourceCapture).where(ReconSourceCapture.account_id == account_id)
        ).all()
    )


# --- Happy path / period -----------------------------------------------------------


def test_daily_report_reconciles_to_expected_job(session: Session) -> None:
    account_id = make_account(session)
    add_membership(session, account_id)
    job_id = add_expected_job(session, account_id)

    outcome = import_record(session, account_id=account_id, payload=daily_report())

    assert outcome.status is ImportStatus.ACCEPTED
    assert outcome.expected_job_id == job_id
    assert outcome.case_id is not None
    assert len(outcome.observation_ids) == 1
    observation = session.get(ReconObservation, outcome.observation_ids[0])
    assert observation is not None
    assert observation.value == Decimal("24.600000")
    assert observation.expected_job_id == job_id
    case = session.get(ReconOperationalCase, outcome.case_id)
    assert case is not None and case.expected_job_id == job_id


def test_report_without_expected_job_is_out_of_contract(session: Session) -> None:
    account_id = make_account(session)
    add_membership(session, account_id)
    # No expected job materialized for this period.

    outcome = import_record(session, account_id=account_id, payload=daily_report())

    assert outcome.status is ImportStatus.OUT_OF_CONTRACT
    assert outcome.expected_job_id is None
    assert outcome.case_id is None
    assert _observation_count(session, account_id) == 1


# --- Ownership ---------------------------------------------------------------------


def test_report_for_unowned_vessel_is_denied(session: Session) -> None:
    account_id = make_account(session)
    add_expected_job(session, account_id)  # job exists but vessel not a member

    outcome = import_record(session, account_id=account_id, payload=daily_report())

    assert outcome.status is ImportStatus.MEMBERSHIP_DENIED
    assert _capture_count(session, account_id) == 0
    assert _observation_count(session, account_id) == 0


def test_cross_tenant_isolation(session: Session) -> None:
    account_a = make_account(session)
    account_b = make_account(session)
    add_membership(session, account_a)
    add_expected_job(session, account_a)

    accepted = import_record(session, account_id=account_a, payload=daily_report())
    assert accepted.status is ImportStatus.ACCEPTED

    # Account B never provisioned this vessel; the same bytes are denied for B.
    denied = import_record(session, account_id=account_b, payload=daily_report())
    assert denied.status is ImportStatus.MEMBERSHIP_DENIED

    assert _capture_count(session, account_a) == 1
    assert _capture_count(session, account_b) == 0
    assert _observation_count(session, account_b) == 0


# --- Revision ----------------------------------------------------------------------


def test_identical_revision_is_idempotent(session: Session) -> None:
    account_id = make_account(session)
    add_membership(session, account_id)
    add_expected_job(session, account_id)

    first = import_record(session, account_id=account_id, payload=daily_report())
    second = import_record(session, account_id=account_id, payload=daily_report())

    assert first.status is ImportStatus.ACCEPTED
    assert second.status is ImportStatus.IDEMPOTENT
    assert second.capture_id == first.capture_id
    assert _capture_count(session, account_id) == 1
    assert _observation_count(session, account_id) == 1


def test_same_revision_different_payload_is_conflict(session: Session) -> None:
    account_id = make_account(session)
    add_membership(session, account_id)
    add_expected_job(session, account_id)

    import_record(session, account_id=account_id, payload=daily_report(quantity="24.600"))
    conflict = import_record(
        session, account_id=account_id, payload=daily_report(quantity="23.600")
    )

    assert conflict.status is ImportStatus.REVISION_CONFLICT
    # The stored observation is unchanged; the conflicting payload is not admitted.
    assert _observation_count(session, account_id) == 1
    observation = session.scalars(
        select(ReconObservation).where(ReconObservation.account_id == account_id)
    ).one()
    assert observation.value == Decimal("24.600000")


def test_correction_new_revision_adds_row_preserving_original(session: Session) -> None:
    account_id = make_account(session)
    add_membership(session, account_id)
    job_id = add_expected_job(session, account_id)

    import_record(session, account_id=account_id, payload=daily_report(source_revision="1"))
    correction = import_record(
        session,
        account_id=account_id,
        payload=daily_report(source_revision="2", quantity="23.600"),
    )

    assert correction.status is ImportStatus.ACCEPTED
    values = {
        obs.value
        for obs in session.scalars(
            select(ReconObservation).where(ReconObservation.expected_job_id == job_id)
        ).all()
    }
    # The original observation survives immutably; the correction is a new row.
    assert values == {Decimal("24.600000"), Decimal("23.600000")}
    assert _capture_count(session, account_id) == 2


# --- Validation short-circuit ------------------------------------------------------


def test_contract_rejection_short_circuits(session: Session) -> None:
    account_id = make_account(session)
    add_membership(session, account_id)
    add_expected_job(session, account_id)

    payload = daily_report()
    payload["items"][0]["quantity"] = 24.6  # numeric literal, not a decimal string

    outcome = import_record(session, account_id=account_id, payload=payload)

    assert outcome.status is ImportStatus.REJECTED
    assert outcome.rejection is not None
    assert outcome.rejection.reason is RejectionReason.INVALID_QUANTITY
    assert _capture_count(session, account_id) == 0


# --- Schema-level tenant ownership (blueprint 16) ---------------------------------


def test_composite_fk_blocks_cross_tenant_child(session: Session) -> None:
    # A child referencing a parent must match on (account_id, id) together. A capture
    # owned by account A cannot be adopted by an observation under account B, even
    # though account B and the capture id both exist.
    account_a = make_account(session)
    account_b = make_account(session)
    add_membership(session, account_a)
    add_expected_job(session, account_a)
    accepted = import_record(session, account_id=account_a, payload=daily_report())
    assert accepted.capture_id is not None

    session.add(
        ReconObservation(
            account_id=account_b,
            capture_id=accepted.capture_id,
            expected_job_id=None,
            field="fuel_consumed",
            item_key="vlsfo",
            fuel_grade=FuelGrade.VLSFO,
            value=Decimal("1.0"),
            unit=QuantityUnit.TONNE,
            origin=ValueOrigin.REPORTED,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            source_pointer="/items/0",
            normalization_version="fuel_units_v1",
            recorded_at=DUE_AT,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
