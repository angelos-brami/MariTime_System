from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from eastmed_pipeline.reconciliation_publication import (
    propose_publication,
    publish_case,
    reconcile_publication,
)
from eastmed_pipeline.reconciliation_read import (
    RecomputeStatus,
    get_case_timeline,
    get_case_view,
    get_daily_report,
    get_service_health,
    list_cases,
    request_recompute,
)
from eastmed_schema.base import Base
from eastmed_schema.enums import (
    AccountTier,
    AutomationStatus,
    BusinessStatus,
    FuelGrade,
    QuantityUnit,
    RecordKind,
    ValueOrigin,
)
from eastmed_schema.models import (
    Account,
    ReconCaseVersion,
    ReconExpectedJob,
    ReconObservation,
    ReconOperationalCase,
    ReconServicePrincipal,
    ReconSourceCapture,
    ReconStandingGrant,
    ReconVerificationVerdict,
)
from eastmed_shared.reconciliation.policy import canonical_payload_hash
from eastmed_shared.reconciliation.read_model import (
    CUSTOMER_ACTIONS,
    EmptyState,
    EvidenceStatus,
    PresentationStatus,
    build_fleet_total,
    daily_report_empty_state,
    label_variances,
    next_machine_step,
    presentation_status,
)
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
CAP = "operations.private_publish"
FP = "fp1"
P_START = datetime(2026, 9, 7, tzinfo=UTC)
P_END = datetime(2026, 9, 8, tzinfo=UTC)


def variance(*, grade: str = "VLSFO", delta: str = "1.60", percent: str = "6.96") -> dict[str, Any]:
    return {
        "function": "fuel_variance",
        "fuel_grade": grade,
        "unit": "tonne",
        "period_start": P_START.isoformat(),
        "period_end": P_END.isoformat(),
        "actual": "24.6",
        "planned": "23.0",
        "delta_unrounded": delta,
        "delta": delta,
        "percent_unrounded": percent,
        "percent": percent,
        "percent_reason": None,
        "rounding": "ROUND_HALF_UP@2",
        "formula_version": "fuel_variance_v1",
        "status": "reconciled",
    }


def result_json(*, variances: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "result_contract_version": "result_contract_v1",
        "vessel_ref": "V3",
        "period_start": P_START.isoformat(),
        "period_end": P_END.isoformat(),
        "calculator_version": "recon_calculator_v1",
        "variances": variances if variances is not None else [variance()],
    }


TABLES = [
    "accounts",
    "recon_expected_jobs",
    "recon_operational_cases",
    "recon_case_versions",
    "recon_verification_verdicts",
    "recon_source_captures",
    "recon_observations",
    "recon_service_principals",
    "recon_standing_grants",
    "recon_action_attestations",
    "recon_action_intents",
    "recon_outbox",
    "recon_case_publications",
    "recon_workflow_runs",
    "recon_workflow_tasks",
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection: Any, _: Any) -> None:  # pragma: no cover - setup
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[Base.metadata.tables[n] for n in TABLES])
    with Session(engine) as active:
        yield active


def make_account(session: Session, *, with_authority: bool = True) -> UUID:
    account = Account(
        company="Design Partner",
        tier=AccountTier.DATA,
        contract_start=date(2026, 1, 1),
        contract_end=date(2027, 1, 1),
    )
    session.add(account)
    session.flush()
    if with_authority:
        session.add(
            ReconServicePrincipal(
                account_id=account.id, subject="svc-1", issuer="provisioning",
                audience="operations", allowed_scopes=[CAP], deployment_fingerprint=FP, active=True,
            )
        )
        session.add(
            ReconStandingGrant(
                account_id=account.id, policy_id="p1", policy_revision=1, capability=CAP,
                allowed_case_types=["daily_reconciliation"],
                allowed_destinations=["tenant_private_portal"],
                external_messages=False, financial_commitments=False,
                required_evidence=["required_inputs_complete", "accepted_facts"],
                required_artifact="reproducible_calculation_receipt",
                qualified_fingerprints=[FP], attestation_ttl_seconds=120, signing_key_id="key-1",
                valid_from=NOW - timedelta(hours=1), valid_to=NOW + timedelta(hours=1), active=True,
            )
        )
        session.flush()
    return account.id


def add_job(session: Session, account_id: UUID, *, vessel_ref: str = "V3") -> ReconExpectedJob:
    job = ReconExpectedJob(
        account_id=account_id, vessel_ref=vessel_ref, voyage_ref="P4",
        period_start=P_START, period_end=P_END, due_at=NOW,
        contract_version="fleet_reconciliation_v1", result_contract_version="result_contract_v1",
        required_record_kind=RecordKind.DAILY_REPORT,
    )
    session.add(job)
    session.flush()
    return job


def add_case(
    session: Session, account_id: UUID, job: ReconExpectedJob, *,
    automation: AutomationStatus, business: BusinessStatus = BusinessStatus.VARIANCE_PRESENT,
) -> ReconOperationalCase:
    case = ReconOperationalCase(
        account_id=account_id, expected_job_id=job.id, automation_status=automation,
        business_status=business, revision=1, deadline_at=NOW + timedelta(hours=2),
    )
    session.add(case)
    session.flush()
    return case


def add_version(
    session: Session, account_id: UUID, case: ReconOperationalCase, content: dict[str, Any]
) -> ReconCaseVersion:
    version = ReconCaseVersion(
        account_id=account_id, case_id=case.id, revision=1, evidence_hash="ev-1",
        content_hash=canonical_payload_hash(content), result_json=content,
        supersedes_version_id=None,
    )
    session.add(version)
    session.flush()
    return version


def add_verdict(
    session: Session, account_id: UUID, case: ReconOperationalCase, version: ReconCaseVersion
) -> None:
    """Persist a completing verdict bound to the version so publication is entitled."""
    session.add(
        ReconVerificationVerdict(
            account_id=account_id, case_id=case.id, case_version_id=version.id,
            expected_job_id=case.expected_job_id, case_revision=version.revision,
            content_hash=version.content_hash, evidence_hash=version.evidence_hash,
            calculator_version=str(version.result_json.get("calculator_version", "")),
            verifier_version="recon_verifier_v1", status="verified",
            completion_status="completed", checked=1, has_unresolved=False,
            completes=True, findings_json=[],
        )
    )
    session.flush()


def add_observation(session: Session, account_id: UUID, job: ReconExpectedJob) -> None:
    capture = ReconSourceCapture(
        account_id=account_id, record_kind=RecordKind.DAILY_REPORT, source_system="reporter",
        external_ref="R17", source_revision="1", raw_hash="h1", payload_json={}, captured_at=NOW,
    )
    session.add(capture)
    session.flush()
    session.add(
        ReconObservation(
            account_id=account_id, capture_id=capture.id, expected_job_id=job.id,
            field="fuel_consumed", item_key="vlsfo", fuel_grade=FuelGrade.VLSFO,
            value=Decimal("24.6"), unit=QuantityUnit.TONNE, origin=ValueOrigin.REPORTED,
            period_start=P_START, period_end=P_END, source_pointer="$.items[0]",
            normalization_version="fuel_units_v1", recorded_at=NOW,
        )
    )
    session.flush()


def complete_case(
    session: Session, account_id: UUID, job: ReconExpectedJob
) -> ReconOperationalCase:
    """Drive a case all the way to verified-complete via the real WO7 flow."""
    case = add_case(session, account_id, job, automation=AutomationStatus.CALCULATED)
    content = result_json()
    version = add_version(session, account_id, case, content)
    add_verdict(session, account_id, case, version)
    proposed = propose_publication(
        session, account_id=account_id, case=case, case_version=version,
        principal_subject="svc-1", system_fingerprint=FP, now=NOW,
    )
    from eastmed_schema.models import ReconActionIntent

    intent = session.get(ReconActionIntent, proposed.intent_id)
    assert intent is not None
    publish_case(session, account_id=account_id, intent=intent, current_payload=content, now=NOW)
    reconcile_publication(session, account_id=account_id, intent=intent, now=NOW)
    return case


# --- Pure presentation rules --------------------------------------------------------


@pytest.mark.parametrize(
    ("automation", "business", "expected"),
    [
        ("verified_complete", "reconciled", PresentationStatus.COMPLETE),
        ("published", "variance_present", PresentationStatus.IN_PROGRESS),
        ("publication_ready", "variance_present", PresentationStatus.IN_PROGRESS),
        ("calculated", "variance_present", PresentationStatus.IN_PROGRESS),
        ("awaiting_arrival", "pending", PresentationStatus.AWAITING_DATA),
        ("waiting_for_machine_data", "pending", PresentationStatus.AWAITING_DATA),
        ("recovering", "pending", PresentationStatus.DEGRADED),
        ("calculated", "unresolved", PresentationStatus.UNRESOLVED),
        ("unresolved", "unresolved", PresentationStatus.UNRESOLVED),
        ("disabled", "pending", PresentationStatus.DISABLED),
    ],
)
def test_presentation_status_never_greens_a_non_complete_case(
    automation: str, business: str, expected: PresentationStatus
) -> None:
    assert presentation_status(automation_status=automation, business_status=business) is expected


def test_only_verified_complete_maps_to_complete() -> None:
    # A published but unconfirmed case is a proposed result, visibly not complete.
    assert presentation_status(automation_status="published", business_status="reconciled") is (
        PresentationStatus.IN_PROGRESS
    )
    assert next_machine_step("published") == "confirm_read_back"
    assert next_machine_step("verified_complete") is None


def test_label_variances_labels_origins_and_collects_unresolved() -> None:
    views, unresolved = label_variances(
        [variance(), {"fuel_grade": "MGO", "status": "unresolved", "reason": "missing_input"}]
    )
    reconciled = next(v for v in views if v.status == "reconciled")
    origins = {mv.name: mv.origin for mv in reconciled.values}
    assert origins == {"actual": "reported", "planned": "reported",
                       "delta": "calculated", "percent": "calculated"}
    assert unresolved == ("MGO",)


def test_build_fleet_total_sums_only_compatible_quantities() -> None:
    total = build_fleet_total(
        [("VLSFO", "tonne", Decimal("1.60")), ("VLSFO", "tonne", Decimal("0.40")),
         ("MGO", "tonne", Decimal("2.00"))],
        excluded_unresolved=0, missing_reports=0,
    )
    groups = {(g.fuel_grade, g.unit): g for g in total.groups}
    assert groups[("VLSFO", "tonne")].total_delta == "2.00"
    assert groups[("VLSFO", "tonne")].vessel_count == 2
    assert total.complete is True


def test_fleet_total_with_exclusions_is_not_complete() -> None:
    total = build_fleet_total([], excluded_unresolved=1, missing_reports=0)
    assert total.complete is False


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (dict(service_disabled=True), EmptyState.DISABLED_SERVICE),
        (dict(feed_authorized=False), EmptyState.NO_AUTHORIZED_FEED),
        (dict(expected_jobs=0), EmptyState.NO_EXPECTED_JOBS),
        (dict(invalid_inputs=True), EmptyState.INVALID_INPUTS),
        (dict(calculations_unavailable=True), EmptyState.CALCULATIONS_UNAVAILABLE),
        (dict(arrived_jobs=1), EmptyState.FEED_DELAYED),
        (dict(complete_jobs=2, arrived_jobs=2, variance_count=0),
         EmptyState.NO_RELEVANT_DIFFERENCES),
        (dict(complete_jobs=2, arrived_jobs=2, variance_count=1), EmptyState.NONE),
    ],
)
def test_daily_report_empty_state_distinguishes_reasons(
    kwargs: dict[str, Any], expected: EmptyState
) -> None:
    base = dict(
        expected_jobs=2, feed_authorized=True, feed_delayed=False, service_disabled=False,
        invalid_inputs=False, calculations_unavailable=False, arrived_jobs=2,
        complete_jobs=0, variance_count=1,
    )
    base.update(kwargs)
    assert daily_report_empty_state(**base) is expected  # type: ignore[arg-type]


# --- Case view ----------------------------------------------------------------------


def test_case_view_complete_is_confirmed_and_has_no_customer_actions(session: Session) -> None:
    account_id = make_account(session)
    job = add_job(session, account_id)
    case = complete_case(session, account_id, job)

    view = get_case_view(session, account_id=account_id, case_id=case.id)
    assert view is not None
    assert view.presentation_status is PresentationStatus.COMPLETE
    assert view.evidence_status is EvidenceStatus.CONFIRMED
    assert view.is_completed is True
    assert view.receipts["read_back_confirmed"] is True
    assert view.next_machine_step is None
    assert view.render()["customer_actions"] == list(CUSTOMER_ACTIONS) == []
    # Provenance/receipts carry hashes, not the raw payload.
    assert view.receipts["content_hash"]


def test_case_view_published_but_unconfirmed_is_in_progress(session: Session) -> None:
    account_id = make_account(session)
    job = add_job(session, account_id)
    case = add_case(session, account_id, job, automation=AutomationStatus.CALCULATED)
    content = result_json()
    version = add_version(session, account_id, case, content)
    add_verdict(session, account_id, case, version)
    from eastmed_schema.models import ReconActionIntent

    proposed = propose_publication(
        session, account_id=account_id, case=case, case_version=version,
        principal_subject="svc-1", system_fingerprint=FP, now=NOW,
    )
    intent = session.get(ReconActionIntent, proposed.intent_id)
    assert intent is not None
    publish_case(session, account_id=account_id, intent=intent, current_payload=content, now=NOW)

    view = get_case_view(session, account_id=account_id, case_id=case.id)
    assert view is not None
    assert view.presentation_status is PresentationStatus.IN_PROGRESS
    assert view.evidence_status is EvidenceStatus.PENDING
    assert view.receipts["read_back_confirmed"] is False


def test_case_view_unresolved_is_incomplete(session: Session) -> None:
    account_id = make_account(session)
    job = add_job(session, account_id)
    case = add_case(
        session, account_id, job, automation=AutomationStatus.UNRESOLVED,
        business=BusinessStatus.UNRESOLVED,
    )
    add_version(
        session, account_id, case,
        result_json(variances=[{"fuel_grade": "VLSFO", "status": "unresolved",
                                "reason": "missing_input"}]),
    )
    view = get_case_view(session, account_id=account_id, case_id=case.id)
    assert view is not None
    assert view.presentation_status is PresentationStatus.UNRESOLVED
    assert view.evidence_status is EvidenceStatus.INCOMPLETE
    assert view.unresolved_fields == ("VLSFO",)


def test_case_view_cross_tenant_is_not_disclosed(session: Session) -> None:
    tenant_a = make_account(session)
    tenant_b = make_account(session, with_authority=False)
    job = add_job(session, tenant_a)
    case = add_case(session, tenant_a, job, automation=AutomationStatus.CALCULATED)
    # Tenant B asking for tenant A's case gets nothing, not an existence signal.
    assert get_case_view(session, account_id=tenant_b, case_id=case.id) is None
    assert get_case_view(session, account_id=tenant_a, case_id=case.id) is not None


# --- Listing / paging ---------------------------------------------------------------


def test_list_cases_stable_cursor_paging_no_skip_or_dup(session: Session) -> None:
    account_id = make_account(session, with_authority=False)
    for _ in range(5):
        job = add_job(session, account_id, vessel_ref=f"V{_}")
        add_case(session, account_id, job, automation=AutomationStatus.CALCULATED)

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        page = list_cases(session, account_id=account_id, cursor=cursor, limit=2)
        seen.extend(str(item.case_id) for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == 5
    assert len(set(seen)) == 5  # no duplicates, no skips


def test_list_cases_status_filter(session: Session) -> None:
    account_id = make_account(session, with_authority=False)
    j1 = add_job(session, account_id, vessel_ref="V1")
    add_case(session, account_id, j1, automation=AutomationStatus.AWAITING_ARRIVAL,
             business=BusinessStatus.PENDING)
    j2 = add_job(session, account_id, vessel_ref="V2")
    add_case(session, account_id, j2, automation=AutomationStatus.CALCULATED)

    awaiting = list_cases(session, account_id=account_id, status=PresentationStatus.AWAITING_DATA)
    assert [c.expected_job_id for c in awaiting.items] == [j1.id]


# --- Timeline -----------------------------------------------------------------------


def test_timeline_records_versions_and_publication(session: Session) -> None:
    account_id = make_account(session)
    job = add_job(session, account_id)
    case = complete_case(session, account_id, job)
    timeline = get_case_timeline(session, account_id=account_id, case_id=case.id)
    assert timeline is not None
    kinds = [e.kind for e in timeline.entries]
    assert "version_committed" in kinds and "published" in kinds
    # Public-safe: entries expose reason codes and hashes, never the result payload.
    for entry in timeline.entries:
        rendered = entry.render()
        assert "result_json" not in rendered and "payload" not in rendered


def test_timeline_cross_tenant_is_not_disclosed(session: Session) -> None:
    tenant_a = make_account(session)
    tenant_b = make_account(session, with_authority=False)
    job = add_job(session, tenant_a)
    case = add_case(session, tenant_a, job, automation=AutomationStatus.CALCULATED)
    assert get_case_timeline(session, account_id=tenant_b, case_id=case.id) is None


# --- Daily report -------------------------------------------------------------------


def test_daily_report_counts_and_fleet_total(session: Session) -> None:
    account_id = make_account(session, with_authority=False)
    j1 = add_job(session, account_id, vessel_ref="V1")
    c1 = add_case(session, account_id, j1, automation=AutomationStatus.CALCULATED)
    add_version(session, account_id, c1, result_json())
    add_observation(session, account_id, j1)
    j2 = add_job(session, account_id, vessel_ref="V2")
    add_case(session, account_id, j2, automation=AutomationStatus.AWAITING_ARRIVAL,
             business=BusinessStatus.PENDING)

    report = get_daily_report(
        session, account_id=account_id,
        period_start=P_START - timedelta(days=1), period_end=P_END + timedelta(days=1), now=NOW,
    )
    assert report.expected_jobs == 2
    assert report.awaiting_jobs == 1
    assert report.in_progress_jobs == 1
    # One vessel arrived and reconciled; one never arrived -> incomplete aggregate.
    assert report.fleet_total.complete is False
    assert report.empty_state is EmptyState.FEED_DELAYED
    groups = {(g.fuel_grade, g.unit): g for g in report.fleet_total.groups}
    assert groups[("VLSFO", "tonne")].total_delta == "1.60"


def test_daily_report_no_expected_jobs_is_empty_state(session: Session) -> None:
    account_id = make_account(session, with_authority=False)
    report = get_daily_report(
        session, account_id=account_id, period_start=P_START, period_end=P_END, now=NOW,
    )
    assert report.expected_jobs == 0
    assert report.empty_state is EmptyState.NO_EXPECTED_JOBS


# --- Service health -----------------------------------------------------------------


def test_service_health_available_with_grant_disabled_without(session: Session) -> None:
    account_id = make_account(session)  # has an active private_publish grant
    health = get_service_health(session, account_id=account_id, now=NOW)
    by_cap = {c.capability: c for c in health.capabilities}
    assert by_cap[CAP].available is True
    # A capability with no provisioned grant is reported unavailable with a reason.
    assert by_cap["operations.reconcile"].available is False
    assert by_cap["operations.reconcile"].disabled_reason == "no_active_grant"


# --- Recompute (service-only) -------------------------------------------------------


def test_recompute_is_service_only(session: Session) -> None:
    account_id = make_account(session)
    job = add_job(session, account_id)
    case = add_case(session, account_id, job, automation=AutomationStatus.CALCULATED)
    add_version(session, account_id, case, result_json())
    result = request_recompute(
        session, account_id=account_id, case_id=case.id, expected_revision=1,
        principal_subject="unknown-svc", workflow_version="wf-1", system_fingerprint=FP,
        budget_units=10, deadline_at=NOW + timedelta(hours=1), now=NOW,
    )
    assert result.status is RecomputeStatus.FORBIDDEN


def test_recompute_stale_revision_conflicts(session: Session) -> None:
    account_id = make_account(session)
    job = add_job(session, account_id)
    case = add_case(session, account_id, job, automation=AutomationStatus.CALCULATED)
    add_version(session, account_id, case, result_json())
    result = request_recompute(
        session, account_id=account_id, case_id=case.id, expected_revision=99,
        principal_subject="svc-1", workflow_version="wf-1", system_fingerprint=FP,
        budget_units=10, deadline_at=NOW + timedelta(hours=1), now=NOW,
    )
    assert result.status is RecomputeStatus.CONFLICT
    assert result.current_revision == 1


def test_recompute_accepted_enqueues_a_run(session: Session) -> None:
    account_id = make_account(session)
    job = add_job(session, account_id)
    case = add_case(session, account_id, job, automation=AutomationStatus.CALCULATED)
    add_version(session, account_id, case, result_json())
    result = request_recompute(
        session, account_id=account_id, case_id=case.id, expected_revision=1,
        principal_subject="svc-1", workflow_version="wf-1", system_fingerprint=FP,
        budget_units=10, deadline_at=NOW + timedelta(hours=1), now=NOW,
    )
    assert result.status is RecomputeStatus.ACCEPTED
    assert result.run_id is not None
    # The immutable case version is untouched (no mutable overwrite).
    count = session.scalar(
        select(func.count()).select_from(ReconCaseVersion).where(
            ReconCaseVersion.account_id == account_id, ReconCaseVersion.case_id == case.id
        )
    )
    assert count == 1


def test_recompute_cross_tenant_not_found(session: Session) -> None:
    tenant_a = make_account(session)
    tenant_b = make_account(session)
    job = add_job(session, tenant_a)
    case = add_case(session, tenant_a, job, automation=AutomationStatus.CALCULATED)
    result = request_recompute(
        session, account_id=tenant_b, case_id=case.id, expected_revision=1,
        principal_subject="svc-1", workflow_version="wf-1", system_fingerprint=FP,
        budget_units=10, deadline_at=NOW + timedelta(hours=1), now=NOW,
    )
    assert result.status is RecomputeStatus.NOT_FOUND
