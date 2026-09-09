from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from eastmed_pipeline.reconciliation_publication import propose_publication
from eastmed_pipeline.reconciliation_reliability import (
    RunbookExecStatus,
    dispatch_operator_runbook,
    gather_telemetry,
    run_quarantine_source,
    run_rebuild_wakeups,
    run_recover_expired_task,
    run_renew_task_lease,
)
from eastmed_pipeline.reconciliation_runtime import (
    CommitStatus,
    claim_task,
    commit_stage,
    create_run,
    enqueue_task,
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
    ReconActionIntent,
    ReconCaseVersion,
    ReconExpectedJob,
    ReconOperationalCase,
    ReconOutbox,
    ReconServicePrincipal,
    ReconStandingGrant,
    ReconVerificationVerdict,
    ReconWorkflowTask,
)
from eastmed_shared.reconciliation.policy import canonical_payload_hash
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
CAP = "operations.private_publish"
FP = "fp1"
LATER = NOW + timedelta(minutes=10)

TABLES = [
    "accounts",
    "recon_expected_jobs",
    "recon_operational_cases",
    "recon_case_versions",
    "recon_verification_verdicts",
    "recon_source_captures",
    "recon_service_principals",
    "recon_standing_grants",
    "recon_action_attestations",
    "recon_action_intents",
    "recon_outbox",
    "recon_case_publications",
    "recon_workflow_runs",
    "recon_workflow_tasks",
    "recon_task_attempts",
    "recon_budget_ledgers",
    "recon_budget_reservations",
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
        company="Design Partner", tier=AccountTier.DATA,
        contract_start=date(2026, 1, 1), contract_end=date(2027, 1, 1),
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


def add_case(session: Session, account_id: UUID, *, vessel_ref: str = "V3") -> ReconOperationalCase:
    job = ReconExpectedJob(
        account_id=account_id, vessel_ref=vessel_ref, voyage_ref="P4",
        period_start=datetime(2026, 9, 7, tzinfo=UTC), period_end=datetime(2026, 9, 8, tzinfo=UTC),
        due_at=NOW, contract_version="fleet_reconciliation_v1",
        result_contract_version="result_contract_v1", required_record_kind=RecordKind.DAILY_REPORT,
    )
    session.add(job)
    session.flush()
    case = ReconOperationalCase(
        account_id=account_id, expected_job_id=job.id,
        automation_status=AutomationStatus.CALCULATED,
        business_status=BusinessStatus.VARIANCE_PRESENT,
        revision=1, deadline_at=NOW + timedelta(hours=2),
    )
    session.add(case)
    session.flush()
    return case


def add_task(session: Session, account_id: UUID, case: ReconOperationalCase) -> UUID:
    run = create_run(
        session, account_id=account_id, case=case, workflow_version="wf-1",
        system_fingerprint=FP, budget_units=100, deadline_at=NOW + timedelta(hours=1),
    )
    task = enqueue_task(
        session, account_id=account_id, run=run, stage=WorkflowStage.RECONCILE, now=NOW
    )
    return task.id


# --- recover_expired_task: durable recovery, stale commit rejected ------------------


def test_recover_expired_task_bumps_epoch_and_fences_old_worker(session: Session) -> None:
    account_id = make_account(session, with_authority=False)
    case = add_case(session, account_id)
    task_id = add_task(session, account_id, case)

    old_claim = claim_task(session, account_id=account_id, now=NOW)
    assert old_claim is not None and old_claim.lease_epoch == 1  # worker then "crashes"

    # Lease has expired by LATER; the reliability runbook recovers the task.
    outcome, new_claim = run_recover_expired_task(session, account_id=account_id, now=LATER)
    assert outcome.status is RunbookExecStatus.EXECUTED
    assert new_claim is not None and new_claim.lease_epoch == 2
    task = session.get(ReconWorkflowTask, task_id)
    assert task is not None and task.lease_epoch == 2

    # The crashed worker's late commit is fenced; the case never advances twice.
    fenced = commit_stage(session, account_id=account_id, claim=old_claim, now=LATER)
    assert fenced.status is CommitStatus.FENCED


def test_recover_expired_task_noop_when_nothing_due(session: Session) -> None:
    account_id = make_account(session, with_authority=False)
    outcome, claim = run_recover_expired_task(session, account_id=account_id, now=NOW)
    assert outcome.status is RunbookExecStatus.NOOP
    assert claim is None


# --- renew_task_lease ---------------------------------------------------------------


def test_renew_task_lease_renews_current_and_rejects_stale(session: Session) -> None:
    account_id = make_account(session, with_authority=False)
    case = add_case(session, account_id)
    add_task(session, account_id, case)
    claim = claim_task(session, account_id=account_id, now=NOW)
    assert claim is not None

    renewed = run_renew_task_lease(session, account_id=account_id, claim=claim, now=NOW)
    assert renewed.status is RunbookExecStatus.EXECUTED and renewed.probe_passed is True

    # A superseded claim (older epoch) cannot renew.
    stale = claim.__class__(
        task_id=claim.task_id, run_id=claim.run_id, attempt_id=claim.attempt_id,
        attempt_no=claim.attempt_no, lease_epoch=claim.lease_epoch - 1, stage=claim.stage,
    )
    outcome = run_renew_task_lease(session, account_id=account_id, claim=stale, now=NOW)
    assert outcome.status is RunbookExecStatus.NOOP and outcome.probe_passed is False


# --- rebuild_wakeups ----------------------------------------------------------------


def test_rebuild_wakeups_reconstructs_due_coverage(session: Session) -> None:
    account_id = make_account(session, with_authority=False)
    for index in range(3):
        case = add_case(session, account_id, vessel_ref=f"V{index}")
        add_task(session, account_id, case)
    outcome = run_rebuild_wakeups(session, account_id=account_id, now=NOW)
    assert outcome.status is RunbookExecStatus.EXECUTED
    assert outcome.affected == 3


# --- quarantine_source --------------------------------------------------------------


def test_quarantine_source_invalidates_dependent_case(session: Session) -> None:
    account_id = make_account(session)  # needs authority to propose a publication
    case = add_case(session, account_id)
    content = {"result_contract_version": "result_contract_v1", "variances": []}
    version = ReconCaseVersion(
        account_id=account_id, case_id=case.id, revision=1, evidence_hash="ev-1",
        content_hash=canonical_payload_hash(content), result_json=content,
        supersedes_version_id=None,
    )
    session.add(version)
    session.flush()
    session.add(
        ReconVerificationVerdict(
            account_id=account_id, case_id=case.id, case_version_id=version.id,
            expected_job_id=case.expected_job_id, case_revision=version.revision,
            content_hash=version.content_hash, evidence_hash=version.evidence_hash,
            calculator_version=str(content.get("calculator_version", "")),
            verifier_version="recon_verifier_v1", status="verified",
            completion_status="completed", checked=1, has_unresolved=False,
            completes=True, findings_json=[],
        )
    )
    session.flush()
    proposed = propose_publication(
        session, account_id=account_id, case=case, case_version=version,
        principal_subject="svc-1", system_fingerprint=FP, now=NOW,
    )
    intent = session.get(ReconActionIntent, proposed.intent_id)
    assert intent is not None

    outcome = run_quarantine_source(
        session, account_id=account_id, case_ids=[case.id], current_revision=2, now=NOW
    )
    assert outcome.status is RunbookExecStatus.EXECUTED
    assert outcome.affected == 1
    outbox = session.scalars(
        select(ReconOutbox).where(
            ReconOutbox.account_id == account_id, ReconOutbox.intent_id == intent.id
        )
    ).one()
    assert outbox.status.value == "invalidated"
    refreshed = session.get(ReconOperationalCase, case.id)
    assert refreshed is not None and refreshed.automation_status is AutomationStatus.DISABLED


# --- operator-surface runbooks + off-registry rejection -----------------------------


def test_operator_runbook_is_admitted_and_handed_off() -> None:
    outcome = dispatch_operator_runbook("rollback_canary")
    assert outcome.status is RunbookExecStatus.REQUIRES_OPERATOR


def test_off_registry_runbook_is_rejected() -> None:
    outcome = dispatch_operator_runbook("rm_rf_everything")
    assert outcome.status is RunbookExecStatus.REJECTED


# --- telemetry gather ---------------------------------------------------------------


def test_gather_telemetry_reports_aggregates(session: Session) -> None:
    account_id = make_account(session)
    case = add_case(session, account_id)
    add_task(session, account_id, case)
    metrics = gather_telemetry(session, account_id=account_id, now=LATER)
    assert metrics.expected_jobs == 1
    assert metrics.completed_cases == 0
    # operations.reconcile has no grant here -> reported disabled.
    assert metrics.disabled_capabilities >= 1
    rendered = metrics.render()
    assert "expected_jobs" in rendered and "case_id" not in rendered
    assert metrics.manual_touch_events is None
    assert metrics.prep_latency_p95 is None
    assert metrics.correction_propagation_p95 is None
