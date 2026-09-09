from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from eastmed_pipeline.reconciliation_publication import (
    PublicationReason,
    VerdictGateReason,
    find_due_outbox,
    invalidate_pending_publications,
    propose_publication,
    publication_action_key,
    publish_case,
    reconcile_publication,
)
from eastmed_schema.base import Base
from eastmed_schema.enums import (
    AccountTier,
    AttestationDecision,
    AutomationStatus,
    BusinessStatus,
    OutboxResponseClass,
    OutboxStatus,
    RecordKind,
)
from eastmed_schema.models import (
    Account,
    ReconActionAttestation,
    ReconCasePublication,
    ReconCaseVersion,
    ReconExpectedJob,
    ReconOperationalCase,
    ReconOutbox,
    ReconServicePrincipal,
    ReconStandingGrant,
    ReconVerificationVerdict,
)
from eastmed_shared.reconciliation.policy import DenyReason, canonical_payload_hash
from eastmed_shared.reconciliation.verifier import (
    VERIFIER_VERSION,
    CaseCompletionStatus,
    VerificationStatus,
)
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
CAP = "operations.private_publish"
FP = "fp1"

RESULT_JSON: dict[str, Any] = {
    "case_type": "daily_reconciliation",
    "vessel_ref": "V3",
    "period_start": "2026-09-07T00:00:00+00:00",
    "period_end": "2026-09-08T00:00:00+00:00",
    "variances": [
        {"fuel_grade": "VLSFO", "planned": "23.00", "actual": "24.60",
         "delta": "1.60", "delta_percent": "6.96"}
    ],
}

TABLES = [
    "accounts",
    "recon_expected_jobs",
    "recon_operational_cases",
    "recon_case_versions",
    "recon_verification_verdicts",
    "recon_service_principals",
    "recon_standing_grants",
    "recon_action_attestations",
    "recon_action_intents",
    "recon_outbox",
    "recon_case_publications",
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


def _grant(account_id: UUID) -> ReconStandingGrant:
    return ReconStandingGrant(
        account_id=account_id,
        policy_id="tenant-routine-operations-v1",
        policy_revision=1,
        capability=CAP,
        allowed_case_types=["daily_reconciliation"],
        allowed_destinations=["tenant_private_portal"],
        external_messages=False,
        financial_commitments=False,
        required_evidence=["required_inputs_complete", "accepted_facts"],
        required_artifact="reproducible_calculation_receipt",
        qualified_fingerprints=[FP],
        attestation_ttl_seconds=120,
        signing_key_id="key-1",
        valid_from=NOW - timedelta(hours=1),
        valid_to=NOW + timedelta(hours=1),
        active=True,
    )


def seed(
    session: Session, *, content: dict[str, Any] | None = None, with_verdict: bool = True
) -> tuple[UUID, ReconOperationalCase, ReconCaseVersion, ReconStandingGrant]:
    account = Account(
        company="Design Partner",
        tier=AccountTier.DATA,
        contract_start=date(2026, 1, 1),
        contract_end=date(2027, 1, 1),
    )
    session.add(account)
    session.flush()
    account_id = account.id

    session.add(
        ReconServicePrincipal(
            account_id=account_id,
            subject="svc-1",
            issuer="provisioning",
            audience="operations",
            allowed_scopes=[CAP],
            deployment_fingerprint=FP,
            active=True,
        )
    )
    grant = _grant(account_id)
    session.add(grant)

    job = ReconExpectedJob(
        account_id=account_id,
        vessel_ref="V3",
        voyage_ref="P4",
        period_start=datetime(2026, 9, 7, tzinfo=UTC),
        period_end=datetime(2026, 9, 8, tzinfo=UTC),
        due_at=NOW,
        contract_version="fleet_reconciliation_v1",
        result_contract_version="result_contract_v1",
        required_record_kind=RecordKind.DAILY_REPORT,
    )
    session.add(job)
    session.flush()

    case = ReconOperationalCase(
        account_id=account_id,
        expected_job_id=job.id,
        automation_status=AutomationStatus.CALCULATED,
        business_status=BusinessStatus.VARIANCE_PRESENT,
        revision=1,
        deadline_at=NOW + timedelta(hours=2),
    )
    session.add(case)
    session.flush()

    version = _add_version(session, account_id, case, content or RESULT_JSON)
    if with_verdict:
        _add_verdict(session, account_id, case, version)
    return account_id, case, version, grant


def _add_verdict(
    session: Session,
    account_id: UUID,
    case: ReconOperationalCase,
    version: ReconCaseVersion,
    *,
    status: str = VerificationStatus.VERIFIED.value,
    checked: int = 1,
    has_unresolved: bool = False,
    completes: bool = True,
    content_hash: str | None = None,
) -> ReconVerificationVerdict:
    """Insert a persisted verdict bound to the version. The publication gate loads this;
    the verifier itself is exercised in its own test, so here we bind directly."""
    row = ReconVerificationVerdict(
        account_id=account_id,
        case_id=case.id,
        case_version_id=version.id,
        expected_job_id=case.expected_job_id,
        case_revision=version.revision,
        content_hash=content_hash if content_hash is not None else version.content_hash,
        evidence_hash=version.evidence_hash,
        calculator_version=str(version.result_json.get("calculator_version", "")),
        verifier_version=VERIFIER_VERSION,
        status=status,
        completion_status=(
            CaseCompletionStatus.COMPLETED.value
            if completes
            else CaseCompletionStatus.UNVERIFIABLE.value
        ),
        checked=checked,
        has_unresolved=has_unresolved,
        completes=completes,
        findings_json=[],
    )
    session.add(row)
    session.flush()
    return row


def _add_version(
    session: Session, account_id: UUID, case: ReconOperationalCase, content: dict[str, Any]
) -> ReconCaseVersion:
    prior = session.scalars(
        select(ReconCaseVersion)
        .where(ReconCaseVersion.account_id == account_id, ReconCaseVersion.case_id == case.id)
        .order_by(ReconCaseVersion.revision.desc())
    ).first()
    revision = 1 if prior is None else prior.revision + 1
    version = ReconCaseVersion(
        account_id=account_id,
        case_id=case.id,
        revision=revision,
        evidence_hash="ev-" + str(revision),
        content_hash=canonical_payload_hash(content),
        result_json=content,
        supersedes_version_id=None if prior is None else prior.id,
    )
    session.add(version)
    session.flush()
    return version


def _propose(
    session: Session, account_id: UUID, case: ReconOperationalCase, version: ReconCaseVersion,
    **kw: Any,
) -> Any:
    return propose_publication(
        session,
        account_id=account_id,
        case=case,
        case_version=version,
        principal_subject="svc-1",
        system_fingerprint=FP,
        now=NOW,
        **kw,
    )


def _publications(session: Session, account_id: UUID, case_id: UUID) -> int:
    return session.scalar(
        select(func.count())
        .select_from(ReconCasePublication)
        .where(
            ReconCasePublication.account_id == account_id,
            ReconCasePublication.case_id == case_id,
        )
    ) or 0


def _outbox(session: Session, account_id: UUID, intent_id: UUID) -> ReconOutbox:
    row = session.scalars(
        select(ReconOutbox).where(
            ReconOutbox.account_id == account_id, ReconOutbox.intent_id == intent_id
        )
    ).one()
    return row


def _load_intent(session: Session, intent_id: UUID | None) -> Any:
    from eastmed_schema.models import ReconActionIntent

    assert intent_id is not None
    return session.get(ReconActionIntent, intent_id)


# --- Pure ---------------------------------------------------------------------------


def test_action_key_is_bound_to_content() -> None:
    account_id = UUID(int=1)
    case_id = UUID(int=2)
    a = publication_action_key(account_id, case_id, "hash-A")
    assert a == publication_action_key(account_id, case_id, "hash-A")  # stable across restarts
    assert a != publication_action_key(account_id, case_id, "hash-B")  # content-bound


# --- Worked example: propose -> publish -> read-back --------------------------------


def test_full_private_publication_reaches_verified_complete(session: Session) -> None:
    account_id, case, version, _ = seed(session)

    proposed = _propose(session, account_id, case, version)
    assert proposed.ready is True
    assert proposed.decision is AttestationDecision.ALLOW
    assert case.automation_status is AutomationStatus.PUBLICATION_READY
    intent = _load_intent(session, proposed.intent_id)
    assert _outbox(session, account_id, intent.id).status is OutboxStatus.PENDING

    published = publish_case(
        session, account_id=account_id, intent=intent, current_payload=RESULT_JSON, now=NOW
    )
    assert published.published is True
    assert _publications(session, account_id, case.id) == 1
    assert case.automation_status is AutomationStatus.PUBLISHED
    # Authority was consumed in the same transaction as the commit.
    attestation = session.get(ReconActionAttestation, intent.attestation_id)
    assert attestation is not None and attestation.dispatched_at is not None
    ob = _outbox(session, account_id, intent.id)
    assert ob.status is OutboxStatus.DISPATCHED
    assert ob.response_class is OutboxResponseClass.ACCEPTED
    assert ob.provider_reference == str(published.publication_id)

    verdict = reconcile_publication(session, account_id=account_id, intent=intent, now=NOW)
    assert verdict.confirmed is True
    assert case.automation_status is AutomationStatus.VERIFIED_COMPLETE
    ob = _outbox(session, account_id, intent.id)
    assert ob.status is OutboxStatus.RECONCILED
    assert ob.response_class is OutboxResponseClass.EFFECT_CONFIRMED


# --- Duplicate ----------------------------------------------------------------------


def test_duplicate_publish_produces_exactly_one_effect(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    proposed = _propose(session, account_id, case, version)
    intent = _load_intent(session, proposed.intent_id)

    first = publish_case(
        session, account_id=account_id, intent=intent, current_payload=RESULT_JSON, now=NOW
    )
    assert first.published is True
    again = publish_case(
        session, account_id=account_id, intent=intent, current_payload=RESULT_JSON, now=NOW
    )
    assert again.published is False
    assert again.reason is DenyReason.DUPLICATE_ACTION
    assert _publications(session, account_id, case.id) == 1


def test_proposing_same_content_twice_is_idempotent(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    first = _propose(session, account_id, case, version)
    second = _propose(session, account_id, case, version)
    assert second.ready is True
    assert second.intent_id == first.intent_id
    count = session.scalar(
        select(func.count()).select_from(ReconOutbox).where(ReconOutbox.account_id == account_id)
    )
    assert count == 1


# --- Crash / replay -----------------------------------------------------------------


def test_crash_before_readback_replays_idempotently(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    intent = _load_intent(session, _propose(session, account_id, case, version).intent_id)
    assert publish_case(
        session, account_id=account_id, intent=intent, current_payload=RESULT_JSON, now=NOW
    ).published is True

    # Worker "crashes" before reconciling; recovery replays the read-back.
    first = reconcile_publication(session, account_id=account_id, intent=intent, now=NOW)
    second = reconcile_publication(session, account_id=account_id, intent=intent, now=NOW)
    assert first.confirmed is True and second.confirmed is True
    assert _publications(session, account_id, case.id) == 1
    assert case.automation_status is AutomationStatus.VERIFIED_COMPLETE


# --- Stale authority ----------------------------------------------------------------


def test_revoked_grant_blocks_publish(session: Session) -> None:
    account_id, case, version, grant = seed(session)
    intent = _load_intent(session, _propose(session, account_id, case, version).intent_id)
    grant.active = False
    session.flush()

    result = publish_case(
        session, account_id=account_id, intent=intent, current_payload=RESULT_JSON, now=NOW
    )
    assert result.published is False
    assert result.reason is DenyReason.GRANT_REVOKED
    assert _publications(session, account_id, case.id) == 0
    assert _outbox(session, account_id, intent.id).status is OutboxStatus.INVALIDATED
    assert case.automation_status is AutomationStatus.PUBLICATION_READY


def test_expired_attestation_blocks_publish(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    intent = _load_intent(session, _propose(session, account_id, case, version).intent_id)
    result = publish_case(
        session, account_id=account_id, intent=intent, current_payload=RESULT_JSON,
        now=NOW + timedelta(seconds=300),
    )
    assert result.published is False
    assert result.reason is DenyReason.ATTESTATION_EXPIRED
    assert _publications(session, account_id, case.id) == 0


def test_case_revision_changed_blocks_publish(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    intent = _load_intent(session, _propose(session, account_id, case, version).intent_id)
    # A correction commits a newer case version after the attestation was bound.
    _add_version(session, account_id, case, {**RESULT_JSON, "corrected": True})
    result = publish_case(
        session, account_id=account_id, intent=intent, current_payload=RESULT_JSON, now=NOW
    )
    assert result.published is False
    assert result.reason is DenyReason.CASE_REVISION_CHANGED
    assert _publications(session, account_id, case.id) == 0


def test_tampered_payload_blocks_publish(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    intent = _load_intent(session, _propose(session, account_id, case, version).intent_id)
    result = publish_case(
        session, account_id=account_id, intent=intent,
        current_payload={**RESULT_JSON, "variances": []}, now=NOW,
    )
    assert result.published is False
    assert result.reason is DenyReason.PAYLOAD_HASH_MISMATCH
    assert _publications(session, account_id, case.id) == 0


def test_capability_stop_blocks_publish(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    intent = _load_intent(session, _propose(session, account_id, case, version).intent_id)
    result = publish_case(
        session, account_id=account_id, intent=intent, current_payload=RESULT_JSON,
        now=NOW, stop_active=True,
    )
    assert result.published is False
    assert result.reason is DenyReason.CAPABILITY_STOPPED
    assert _publications(session, account_id, case.id) == 0


# --- Denials record a receipt without any external contact --------------------------


def test_disallowed_destination_denies_and_records_receipt(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    proposed = _propose(session, account_id, case, version, destination="email")
    assert proposed.ready is False
    assert proposed.decision is AttestationDecision.DENY
    assert proposed.reason == DenyReason.DESTINATION_NOT_ALLOWED.value
    assert proposed.intent_id is None
    # The denial produced a receipt (attestation) but no outbox/publication.
    receipt = session.get(ReconActionAttestation, proposed.attestation_id)
    assert receipt is not None and receipt.decision is AttestationDecision.DENY
    assert session.scalar(select(func.count()).select_from(ReconOutbox)) == 0
    assert _publications(session, account_id, case.id) == 0


# --- Correction propagation ---------------------------------------------------------


def test_correction_invalidates_pending_publication(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    intent = _load_intent(session, _propose(session, account_id, case, version).intent_id)
    # Correction supersedes revision 1 before it was ever published.
    _add_version(session, account_id, case, {**RESULT_JSON, "corrected": True})
    invalidated = invalidate_pending_publications(
        session, account_id=account_id, case_id=case.id, current_revision=2, now=NOW
    )
    assert invalidated == 1
    assert _outbox(session, account_id, intent.id).status is OutboxStatus.INVALIDATED
    result = publish_case(
        session, account_id=account_id, intent=intent, current_payload=RESULT_JSON, now=NOW
    )
    assert result.published is False
    assert _publications(session, account_id, case.id) == 0


def test_readback_rejects_a_superseded_publication(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    intent = _load_intent(session, _propose(session, account_id, case, version).intent_id)
    assert publish_case(
        session, account_id=account_id, intent=intent, current_payload=RESULT_JSON, now=NOW
    ).published is True
    # A correction lands after publish but before the independent read-back.
    _add_version(session, account_id, case, {**RESULT_JSON, "corrected": True})
    verdict = reconcile_publication(session, account_id=account_id, intent=intent, now=NOW)
    assert verdict.confirmed is False
    assert verdict.reason is PublicationReason.SUPERSEDED
    assert case.automation_status is not AutomationStatus.VERIFIED_COMPLETE
    assert _outbox(session, account_id, intent.id).status is OutboxStatus.INVALIDATED


# --- Scheduler / queue loss ---------------------------------------------------------


def test_find_due_outbox_reconstructs_then_clears(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    intent = _load_intent(session, _propose(session, account_id, case, version).intent_id)
    due = find_due_outbox(session, account_id=account_id, now=NOW)
    assert [row.intent_id for row in due] == [intent.id]
    publish_case(
        session, account_id=account_id, intent=intent, current_payload=RESULT_JSON, now=NOW
    )
    assert find_due_outbox(session, account_id=account_id, now=NOW) == []


def test_readback_before_publish_reports_not_published(session: Session) -> None:
    account_id, case, version, _ = seed(session)
    intent = _load_intent(session, _propose(session, account_id, case, version).intent_id)
    verdict = reconcile_publication(session, account_id=account_id, intent=intent, now=NOW)
    assert verdict.confirmed is False
    assert verdict.reason is PublicationReason.NOT_PUBLISHED


# --- Publication gate: only a persisted, matching completion verdict entitles publish ----


def _assert_no_authority(session: Session, account_id: UUID, case_id: UUID) -> None:
    """A gate refusal mints no attestation receipt and no outbox row: the entitlement was
    never in reach, so nothing was requested of the authority."""
    assert session.scalar(select(func.count()).select_from(ReconActionAttestation)) == 0
    assert session.scalar(select(func.count()).select_from(ReconOutbox)) == 0
    assert _publications(session, account_id, case_id) == 0


def test_missing_verdict_blocks_proposal(session: Session) -> None:
    account_id, case, version, _ = seed(session, with_verdict=False)
    proposed = _propose(session, account_id, case, version)
    assert proposed.ready is False
    assert proposed.decision is AttestationDecision.DENY
    assert proposed.reason == VerdictGateReason.VERIFICATION_MISSING.value
    assert proposed.attestation_id is None and proposed.intent_id is None
    assert case.automation_status is AutomationStatus.CALCULATED  # not advanced
    _assert_no_authority(session, account_id, case.id)


def test_unverified_result_blocks_proposal(session: Session) -> None:
    account_id, case, version, _ = seed(session, with_verdict=False)
    _add_verdict(
        session, account_id, case, version,
        status=VerificationStatus.UNVERIFIABLE.value, completes=False,
    )
    proposed = _propose(session, account_id, case, version)
    assert proposed.ready is False
    assert proposed.reason == VerdictGateReason.NOT_VERIFIED.value
    assert proposed.attestation_id is None
    _assert_no_authority(session, account_id, case.id)


def test_unresolved_result_blocks_proposal(session: Session) -> None:
    account_id, case, version, _ = seed(session, with_verdict=False)
    _add_verdict(session, account_id, case, version, has_unresolved=True, completes=False)
    proposed = _propose(session, account_id, case, version)
    assert proposed.ready is False
    assert proposed.reason == VerdictGateReason.RESULT_UNRESOLVED.value
    _assert_no_authority(session, account_id, case.id)


def test_empty_result_blocks_proposal(session: Session) -> None:
    account_id, case, version, _ = seed(session, with_verdict=False)
    _add_verdict(session, account_id, case, version, checked=0, completes=False)
    proposed = _propose(session, account_id, case, version)
    assert proposed.ready is False
    assert proposed.reason == VerdictGateReason.NO_CHECKED_CALCULATIONS.value
    _assert_no_authority(session, account_id, case.id)


def test_verdict_bound_to_other_content_blocks_proposal(session: Session) -> None:
    account_id, case, version, _ = seed(session, with_verdict=False)
    # A verdict that judged a different result must not entitle publishing this one.
    _add_verdict(session, account_id, case, version, content_hash="sha256-of-something-else")
    proposed = _propose(session, account_id, case, version)
    assert proposed.ready is False
    assert proposed.reason == VerdictGateReason.VERDICT_BINDING_MISMATCH.value
    _assert_no_authority(session, account_id, case.id)
