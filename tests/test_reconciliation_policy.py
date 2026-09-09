from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from eastmed_pipeline.reconciliation_policy import attest_action, authorize_dispatch
from eastmed_schema.base import Base
from eastmed_schema.enums import AccountTier, AttestationDecision
from eastmed_schema.models import (
    Account,
    ReconServicePrincipal,
    ReconStandingGrant,
)
from eastmed_shared.reconciliation.policy import (
    ActionRequest,
    DenyReason,
    GrantView,
    PrincipalView,
    canonical_payload_hash,
    evaluate_policy,
)
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

NOW = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
CAP = "operations.private_publish"


# --- Pure policy evaluation --------------------------------------------------------


def grant_view(**kw: Any) -> GrantView:
    base: dict[str, Any] = dict(
        policy_id="tenant-routine-operations-v1",
        policy_revision=1,
        capability=CAP,
        allowed_case_types=frozenset({"daily_reconciliation"}),
        allowed_destinations=frozenset({"tenant_private_portal"}),
        external_messages=False,
        financial_commitments=False,
        required_evidence=frozenset({"required_inputs_complete", "accepted_facts"}),
        qualified_fingerprints=frozenset({"fp1"}),
        attestation_ttl_seconds=120,
        signing_key_id="key-1",
        valid_from=NOW - timedelta(hours=1),
        valid_to=NOW + timedelta(hours=1),
        active=True,
    )
    base.update(kw)
    return GrantView(**base)


def principal_view(**kw: Any) -> PrincipalView:
    base: dict[str, Any] = dict(
        subject="svc-1",
        tenant_id="t1",
        allowed_scopes=frozenset({CAP}),
        deployment_fingerprint="fp1",
        active=True,
    )
    base.update(kw)
    return PrincipalView(**base)


def action(**kw: Any) -> ActionRequest:
    base: dict[str, Any] = dict(
        tenant_id="t1",
        principal_subject="svc-1",
        capability=CAP,
        case_id=str(uuid4()),
        case_revision=1,
        action_key="action-1",
        destination="tenant_private_portal",
        case_type="daily_reconciliation",
        evidence_hash="ev-hash",
        provided_evidence=frozenset({"required_inputs_complete", "accepted_facts"}),
        system_fingerprint="fp1",
        payload={"case": "c1", "content_hash": "h1"},
        requires_external=False,
        requires_financial=False,
    )
    base.update(kw)
    return ActionRequest(**base)


def test_policy_allows_a_valid_private_publish() -> None:
    decision = evaluate_policy(action(), grant_view(), principal_view(), now=NOW)
    assert decision.allowed is True
    assert decision.reason is DenyReason.OK
    assert decision.expires_at == NOW + timedelta(seconds=120)
    assert decision.signing_key_id == "key-1"


@pytest.mark.parametrize(
    ("req", "grant", "principal", "stop", "reason"),
    [
        (action(capability="operations.frobnicate"), grant_view(), principal_view(), False,
         DenyReason.UNRECOGNIZED_ACTION),
        (action(), grant_view(), principal_view(active=False), False,
         DenyReason.PRINCIPAL_INACTIVE),
        (action(), grant_view(), principal_view(tenant_id="t2"), False,
         DenyReason.CROSS_TENANT_PRINCIPAL),
        (action(), grant_view(), principal_view(allowed_scopes=frozenset()), False,
         DenyReason.SCOPE_NOT_GRANTED),
        (action(), grant_view(), principal_view(), True, DenyReason.CAPABILITY_STOPPED),
        (action(), grant_view(active=False), principal_view(), False, DenyReason.GRANT_INACTIVE),
        (action(system_fingerprint="fpX"), grant_view(), principal_view(), False,
         DenyReason.UNKNOWN_FINGERPRINT),
        (action(case_type="disruption"), grant_view(), principal_view(), False,
         DenyReason.CASE_TYPE_NOT_ALLOWED),
        (action(provided_evidence=frozenset()), grant_view(), principal_view(), False,
         DenyReason.EVIDENCE_INCOMPLETE),
        (action(destination="email"), grant_view(), principal_view(), False,
         DenyReason.DESTINATION_NOT_ALLOWED),
        (action(requires_external=True), grant_view(), principal_view(), False,
         DenyReason.EXTERNAL_NOT_PERMITTED),
        (action(requires_financial=True), grant_view(), principal_view(), False,
         DenyReason.FINANCIAL_NOT_PERMITTED),
    ],
)
def test_policy_denials(
    req: ActionRequest, grant: GrantView, principal: PrincipalView, stop: bool, reason: DenyReason
) -> None:
    decision = evaluate_policy(req, grant, principal, now=NOW, stop_active=stop)
    assert decision.allowed is False
    assert decision.reason is reason


def test_expired_grant_is_denied() -> None:
    later = NOW + timedelta(days=1)
    decision = evaluate_policy(action(), grant_view(), principal_view(), now=later)
    assert decision.reason is DenyReason.GRANT_EXPIRED


def test_payload_hash_changes_with_meaningful_difference() -> None:
    a = canonical_payload_hash({"a": 1, "b": 2})
    assert a == canonical_payload_hash({"b": 2, "a": 1})  # key order irrelevant
    assert a != canonical_payload_hash({"a": 1, "b": 3})  # value change matters


# --- DB-backed service and executor recheck ----------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection: Any, _: Any) -> None:  # pragma: no cover - setup
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    names = [
        "accounts",
        "recon_service_principals",
        "recon_standing_grants",
        "recon_action_attestations",
    ]
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[n] for n in names])
    with Session(engine) as active:
        yield active


def seed(session: Session) -> tuple[UUID, ReconStandingGrant]:
    account = Account(
        company="Design Partner",
        tier=AccountTier.DATA,
        contract_start=date(2026, 1, 1),
        contract_end=date(2027, 1, 1),
    )
    session.add(account)
    session.flush()
    session.add(
        ReconServicePrincipal(
            account_id=account.id,
            subject="svc-1",
            issuer="provisioning",
            audience="operations",
            allowed_scopes=[CAP],
            deployment_fingerprint="fp1",
            active=True,
        )
    )
    grant = ReconStandingGrant(
        account_id=account.id,
        policy_id="tenant-routine-operations-v1",
        policy_revision=1,
        capability=CAP,
        allowed_case_types=["daily_reconciliation"],
        allowed_destinations=["tenant_private_portal"],
        external_messages=False,
        financial_commitments=False,
        required_evidence=["required_inputs_complete", "accepted_facts"],
        required_artifact="reproducible_calculation_receipt",
        qualified_fingerprints=["fp1"],
        attestation_ttl_seconds=120,
        signing_key_id="key-1",
        valid_from=NOW - timedelta(hours=1),
        valid_to=NOW + timedelta(hours=1),
        active=True,
    )
    session.add(grant)
    session.flush()
    return account.id, grant


def db_action(account_id: UUID, **kw: Any) -> ActionRequest:
    return action(tenant_id=str(account_id), **kw)


PAYLOAD = {"case": "c1", "content_hash": "h1"}


def test_attest_allow_then_dispatch_is_authorized_once(session: Session) -> None:
    account_id, _ = seed(session)
    att = attest_action(
        session, account_id=account_id, request=db_action(account_id, payload=PAYLOAD), now=NOW
    )
    assert att.decision is AttestationDecision.ALLOW
    first = authorize_dispatch(
        session, account_id=account_id, attestation_id=att.id,
        current_payload=PAYLOAD, current_case_revision=1, now=NOW,
    )
    assert first.authorized is True
    assert session.get(type(att), att.id).dispatched_at is not None  # type: ignore[union-attr]
    # Repeated intent is rejected (idempotency).
    again = authorize_dispatch(
        session, account_id=account_id, attestation_id=att.id,
        current_payload=PAYLOAD, current_case_revision=1, now=NOW,
    )
    assert again.authorized is False
    assert again.reason is DenyReason.DUPLICATE_ACTION


def test_denied_authority_never_dispatches(session: Session) -> None:
    account_id, _ = seed(session)
    att = attest_action(
        session, account_id=account_id,
        request=db_action(account_id, destination="email", payload=PAYLOAD), now=NOW,
    )
    assert att.decision is AttestationDecision.DENY
    assert att.reason == DenyReason.DESTINATION_NOT_ALLOWED.value
    result = authorize_dispatch(
        session, account_id=account_id, attestation_id=att.id,
        current_payload=PAYLOAD, current_case_revision=1, now=NOW,
    )
    assert result.authorized is False
    assert result.reason is DenyReason.DENIED_AUTHORITY


def test_missing_policy_records_a_denial_receipt(session: Session) -> None:
    account_id, grant = seed(session)
    grant.active = False  # no active grant for the capability
    session.flush()
    att = attest_action(
        session, account_id=account_id, request=db_action(account_id, payload=PAYLOAD), now=NOW
    )
    assert att.decision is AttestationDecision.DENY
    assert att.reason == DenyReason.MISSING_POLICY.value
    assert att.grant_id is None


def _allow(session: Session, account_id: UUID) -> UUID:
    att = attest_action(
        session, account_id=account_id, request=db_action(account_id, payload=PAYLOAD), now=NOW
    )
    assert att.decision is AttestationDecision.ALLOW
    return att.id


@pytest.mark.parametrize(
    ("mutate", "kwargs", "reason"),
    [
        ("payload", {"current_payload": {"case": "c1", "content_hash": "TAMPERED"}},
         DenyReason.PAYLOAD_HASH_MISMATCH),
        ("case_revision", {"current_case_revision": 2}, DenyReason.CASE_REVISION_CHANGED),
        ("expired", {"now": NOW + timedelta(seconds=300)}, DenyReason.ATTESTATION_EXPIRED),
        ("stop", {"stop_active": True}, DenyReason.CAPABILITY_STOPPED),
    ],
)
def test_executor_recheck_rejects_stale_authority(
    session: Session, mutate: str, kwargs: dict[str, Any], reason: DenyReason
) -> None:
    account_id, _ = seed(session)
    attestation_id = _allow(session, account_id)
    call: dict[str, Any] = dict(
        current_payload=PAYLOAD, current_case_revision=1, now=NOW
    )
    call.update(kwargs)
    result = authorize_dispatch(
        session, account_id=account_id, attestation_id=attestation_id, **call
    )
    assert result.authorized is False
    assert result.reason is reason


def test_revoked_grant_is_rejected_at_dispatch(session: Session) -> None:
    account_id, grant = seed(session)
    attestation_id = _allow(session, account_id)
    grant.active = False
    session.flush()
    result = authorize_dispatch(
        session, account_id=account_id, attestation_id=attestation_id,
        current_payload=PAYLOAD, current_case_revision=1, now=NOW,
    )
    assert result.reason is DenyReason.GRANT_REVOKED


def test_policy_revision_change_invalidates_attestation(session: Session) -> None:
    account_id, grant = seed(session)
    attestation_id = _allow(session, account_id)
    grant.policy_revision = 2
    session.flush()
    result = authorize_dispatch(
        session, account_id=account_id, attestation_id=attestation_id,
        current_payload=PAYLOAD, current_case_revision=1, now=NOW,
    )
    assert result.reason is DenyReason.POLICY_REVISION_CHANGED


def test_unknown_fingerprint_at_dispatch_is_rejected(session: Session) -> None:
    account_id, grant = seed(session)
    attestation_id = _allow(session, account_id)
    grant.qualified_fingerprints = ["fp-rotated"]
    session.flush()
    result = authorize_dispatch(
        session, account_id=account_id, attestation_id=attestation_id,
        current_payload=PAYLOAD, current_case_revision=1, now=NOW,
    )
    assert result.reason is DenyReason.UNKNOWN_FINGERPRINT
