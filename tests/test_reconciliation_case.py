from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from eastmed_pipeline.reconciliation_case import (
    commit_case_version,
    compute_case_result,
    read_back,
    verify_case,
)
from eastmed_pipeline.reconciliation_import import ImportStatus, import_record
from eastmed_schema.base import Base
from eastmed_schema.enums import AccountTier, AuthorityRole, RecordKind
from eastmed_schema.models import (
    Account,
    ReconCaseVersion,
    ReconExpectedJob,
    ReconOperationalCase,
    ReconVesselMembership,
)
from eastmed_shared.reconciliation.contract import CONTRACT_VERSION
from eastmed_shared.reconciliation.verifier import (
    CaseCompletionStatus,
    VerificationStatus,
    finalize_case_status,
)
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

PERIOD_START = "2026-09-07T00:00:00Z"
PERIOD_END = "2026-09-08T00:00:00Z"

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

    Base.metadata.create_all(
        engine, tables=[Base.metadata.tables[name] for name in RECON_TABLES]
    )
    with Session(engine) as active:
        yield active


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


def add_membership(session: Session, account_id: UUID) -> None:
    session.add(
        ReconVesselMembership(
            account_id=account_id,
            vessel_ref="V3",
            authority_role=AuthorityRole.OPERATOR,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_to=datetime(2027, 1, 1, tzinfo=UTC),
        )
    )
    session.flush()


def add_expected_job(session: Session, account_id: UUID) -> ReconExpectedJob:
    job = ReconExpectedJob(
        account_id=account_id,
        vessel_ref="V3",
        voyage_ref="P4",
        period_start=datetime(2026, 9, 7, tzinfo=UTC),
        period_end=datetime(2026, 9, 8, tzinfo=UTC),
        due_at=datetime(2026, 9, 8, 6, tzinfo=UTC),
        contract_version=CONTRACT_VERSION,
        result_contract_version="result_contract_v1",
        required_record_kind=RecordKind.DAILY_REPORT,
    )
    session.add(job)
    session.flush()
    return job


def voyage_plan(*, planned: str = "23.000") -> dict[str, Any]:
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
        "items": [
            {
                "item_key": "vlsfo",
                "fuel_grade": "VLSFO",
                "quantity": planned,
                "unit": "tonne",
                "period_start": PERIOD_START,
                "period_end": PERIOD_END,
            }
        ],
    }


def daily_report(*, source_revision: str = "1", actual: str = "24.600") -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_VERSION,
        "record_kind": "daily_report",
        "source_system": "noon-connector",
        "external_ref": "R17",
        "source_revision": source_revision,
        "vessel_ref": "V3",
        "recorded_at": "2026-09-08T06:00:00Z",
        "voyage_ref": "P4",
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "items": [
            {"item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": actual, "unit": "tonne"}
        ],
    }


def _seed_case(
    session: Session, *, with_plan: bool = True
) -> tuple[UUID, ReconExpectedJob, ReconOperationalCase]:
    account_id = make_account(session)
    add_membership(session, account_id)
    job = add_expected_job(session, account_id)
    if with_plan:
        assert import_record(session, account_id=account_id, payload=voyage_plan()).status is (
            ImportStatus.ACCEPTED
        )
    outcome = import_record(session, account_id=account_id, payload=daily_report())
    assert outcome.status is ImportStatus.ACCEPTED
    assert outcome.case_id is not None
    case = session.get(ReconOperationalCase, outcome.case_id)
    assert case is not None
    return account_id, job, case


def test_case_result_matches_worked_example(session: Session) -> None:
    account_id, job, _ = _seed_case(session)
    result = compute_case_result(session, account_id=account_id, expected_job=job)
    assert result.unresolved is False
    assert len(result.result_json["variances"]) == 1
    variance = result.result_json["variances"][0]
    assert variance["status"] == "reconciled"
    assert variance["delta"] == "1.60"
    assert variance["percent"] == "6.96"
    assert len(result.content_hash) == 64


def test_read_back_confirms_content_hash(session: Session) -> None:
    account_id, job, case = _seed_case(session)
    result = compute_case_result(session, account_id=account_id, expected_job=job)
    version = commit_case_version(session, account_id=account_id, case=case, result=result)
    assert version.revision == 1
    assert read_back(session, account_id=account_id, case_version_id=version.id) is True
    assert read_back(session, account_id=account_id, case_version_id=uuid4()) is False


def test_replay_produces_identical_hash(session: Session) -> None:
    account_id, job, _ = _seed_case(session)
    first = compute_case_result(session, account_id=account_id, expected_job=job)
    replay = compute_case_result(session, account_id=account_id, expected_job=job)
    assert first.content_hash == replay.content_hash
    assert first.evidence_hash == replay.evidence_hash


def test_missing_plan_is_unresolved(session: Session) -> None:
    account_id, job, _ = _seed_case(session, with_plan=False)
    result = compute_case_result(session, account_id=account_id, expected_job=job)
    assert result.unresolved is True
    variance = result.result_json["variances"][0]
    assert variance["status"] == "unresolved"
    assert variance["reason"] == "missing_input"


def test_correction_creates_superseding_version(session: Session) -> None:
    account_id, job, case = _seed_case(session)
    original = compute_case_result(session, account_id=account_id, expected_job=job)
    v1 = commit_case_version(session, account_id=account_id, case=case, result=original)

    # A correction arrives as a new source revision (24.6 -> 23.6).
    corrected_import = import_record(
        session, account_id=account_id, payload=daily_report(source_revision="2", actual="23.600")
    )
    assert corrected_import.status is ImportStatus.ACCEPTED

    corrected = compute_case_result(session, account_id=account_id, expected_job=job)
    # The current actual is the corrected revision; the case reflects +0.60 / +2.61%.
    variance = corrected.result_json["variances"][0]
    assert variance["delta"] == "0.60"
    assert variance["percent"] == "2.61"
    assert corrected.content_hash != original.content_hash

    v2 = commit_case_version(session, account_id=account_id, case=case, result=corrected)
    assert v2.revision == 2
    assert v2.supersedes_version_id == v1.id

    # Both immutable versions remain and each reads back to its own stored hash.
    versions = session.scalars(
        select(ReconCaseVersion).where(ReconCaseVersion.case_id == case.id)
    ).all()
    assert {v.revision for v in versions} == {1, 2}
    assert read_back(session, account_id=account_id, case_version_id=v1.id) is True
    assert read_back(session, account_id=account_id, case_version_id=v2.id) is True


def test_verify_case_and_completion_when_narrative_withheld(session: Session) -> None:
    # A verified deterministic result completes even with no optional narrative.
    account_id, job, _ = _seed_case(session)
    result = compute_case_result(session, account_id=account_id, expected_job=job)
    verdict = verify_case(session, account_id=account_id, expected_job=job, result=result)
    assert verdict.status is VerificationStatus.VERIFIED
    completion = finalize_case_status(verdict, has_unresolved=result.unresolved)
    assert completion is CaseCompletionStatus.COMPLETED


def test_unresolved_case_does_not_complete(session: Session) -> None:
    account_id, job, _ = _seed_case(session, with_plan=False)
    result = compute_case_result(session, account_id=account_id, expected_job=job)
    verdict = verify_case(session, account_id=account_id, expected_job=job, result=result)
    # No reconciled variances to check, so verification passes vacuously...
    assert verdict.status is VerificationStatus.VERIFIED
    # ...but the unresolved required fact still blocks completion.
    assert result.unresolved is True
    completion = finalize_case_status(verdict, has_unresolved=result.unresolved)
    assert completion is CaseCompletionStatus.UNVERIFIABLE


def test_other_vessel_plan_cannot_supply_missing_plan(session: Session) -> None:
    account_id, job, _ = _seed_case(session, with_plan=False)
    session.add(ReconVesselMembership(
        account_id=account_id, vessel_ref="V9", authority_role=AuthorityRole.OPERATOR,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_to=datetime(2027, 1, 1, tzinfo=UTC),
    ))
    session.flush()
    other = voyage_plan(planned="40.000")
    other.update(vessel_ref="V9", voyage_ref="OTHER", external_ref="OTHER")
    assert import_record(session, account_id=account_id, payload=other).status is (
        ImportStatus.ACCEPTED
    )
    result = compute_case_result(session, account_id=account_id, expected_job=job)
    assert result.unresolved is True


def test_other_voyage_plan_cannot_supply_missing_plan(session: Session) -> None:
    account_id, job, _ = _seed_case(session, with_plan=False)
    other = voyage_plan()
    other.update(voyage_ref="OTHER", external_ref="OTHER")
    assert import_record(session, account_id=account_id, payload=other).status is (
        ImportStatus.ACCEPTED
    )
    assert compute_case_result(session, account_id=account_id, expected_job=job).unresolved


def test_missing_report_never_becomes_empty_completed_analysis(session: Session) -> None:
    account_id = make_account(session)
    job = add_expected_job(session, account_id)
    result = compute_case_result(session, account_id=account_id, expected_job=job)
    verdict = verify_case(session, account_id=account_id, expected_job=job, result=result)
    assert result.unresolved is True
    assert finalize_case_status(verdict, has_unresolved=result.unresolved) is (
        CaseCompletionStatus.UNVERIFIABLE
    )


def test_wrong_voyage_report_does_not_attach_to_expected_job(session: Session) -> None:
    account_id = make_account(session)
    add_membership(session, account_id)
    job = add_expected_job(session, account_id)
    wrong = daily_report()
    wrong["voyage_ref"] = "OTHER"
    outcome = import_record(session, account_id=account_id, payload=wrong)
    assert outcome.status is ImportStatus.OUT_OF_CONTRACT
    assert outcome.expected_job_id is None
    assert compute_case_result(session, account_id=account_id, expected_job=job).unresolved
