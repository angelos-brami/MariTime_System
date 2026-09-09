"""Policy service and executor recheck (blueprint work order 6, sections 12, 17, 22).

``attest_action`` resolves the tenant's service principal and standing grant, evaluates
the proposal deterministically, and records an attestation — an allow that binds the
exact payload hash, or a denial receipt. No external endpoint is ever contacted.

``authorize_dispatch`` is the separate executor recheck that closes the
authority-to-execution race: it reloads the current grant and stop state, re-verifies
the binding (payload hash, case revision, policy revision, expiry, fingerprint), rejects
a duplicate, and only then atomically claims the intent. Denied or stale authority is
rejected here, so it never reaches the executor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from eastmed_schema.enums import AttestationDecision
from eastmed_schema.models import (
    ReconActionAttestation,
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
from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class DispatchResult:
    authorized: bool
    reason: DenyReason


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def resolve_principal(
    session: Session, *, account_id: UUID, subject: str
) -> ReconServicePrincipal | None:
    return session.scalars(
        select(ReconServicePrincipal).where(
            ReconServicePrincipal.account_id == account_id,
            ReconServicePrincipal.subject == subject,
        )
    ).one_or_none()


def resolve_grant(
    session: Session, *, account_id: UUID, capability: str
) -> ReconStandingGrant | None:
    """The active grant for this capability at its highest policy revision. Validity in
    time is judged by the evaluator, so an expired grant yields GRANT_EXPIRED rather
    than MISSING_POLICY."""
    return session.scalars(
        select(ReconStandingGrant)
        .where(
            ReconStandingGrant.account_id == account_id,
            ReconStandingGrant.capability == capability,
            ReconStandingGrant.active.is_(True),
        )
        .order_by(ReconStandingGrant.policy_revision.desc())
    ).first()


def _grant_view(grant: ReconStandingGrant) -> GrantView:
    return GrantView(
        policy_id=grant.policy_id,
        policy_revision=grant.policy_revision,
        capability=grant.capability,
        allowed_case_types=frozenset(grant.allowed_case_types),
        allowed_destinations=frozenset(grant.allowed_destinations),
        external_messages=grant.external_messages,
        financial_commitments=grant.financial_commitments,
        required_evidence=frozenset(grant.required_evidence),
        qualified_fingerprints=frozenset(grant.qualified_fingerprints),
        attestation_ttl_seconds=grant.attestation_ttl_seconds,
        signing_key_id=grant.signing_key_id,
        valid_from=grant.valid_from,
        valid_to=grant.valid_to,
        active=grant.active,
    )


def _principal_view(principal: ReconServicePrincipal) -> PrincipalView:
    return PrincipalView(
        subject=principal.subject,
        tenant_id=str(principal.account_id),
        allowed_scopes=frozenset(principal.allowed_scopes),
        deployment_fingerprint=principal.deployment_fingerprint,
        active=principal.active,
    )


def _record(
    session: Session,
    *,
    account_id: UUID,
    request: ActionRequest,
    decision: AttestationDecision,
    reason: DenyReason,
    payload_hash: str,
    grant: ReconStandingGrant | None,
    principal_id: UUID | None,
    expires_at: datetime | None,
    now: datetime,
) -> ReconActionAttestation:
    attestation = ReconActionAttestation(
        account_id=account_id,
        grant_id=None if grant is None else grant.id,
        principal_id=principal_id,
        action_key=request.action_key,
        capability=request.capability,
        case_id=UUID(request.case_id),
        case_revision=request.case_revision,
        destination=request.destination,
        evidence_hash=request.evidence_hash or "",
        payload_hash=payload_hash,
        policy_id="" if grant is None else grant.policy_id,
        policy_revision=0 if grant is None else grant.policy_revision,
        system_fingerprint=request.system_fingerprint,
        signing_key_id="" if grant is None else grant.signing_key_id,
        decision=decision,
        reason=reason.value,
        expires_at=expires_at,
    )
    session.add(attestation)
    session.flush()
    return attestation


def attest_action(
    session: Session,
    *,
    account_id: UUID,
    request: ActionRequest,
    now: datetime | None = None,
    stop_active: bool = False,
) -> ReconActionAttestation:
    """Evaluate a proposed action and persist an attestation (allow or denial receipt)."""
    moment = _now(now)
    payload_hash = canonical_payload_hash(request.payload)
    principal = resolve_principal(session, account_id=account_id, subject=request.principal_subject)
    grant = resolve_grant(session, account_id=account_id, capability=request.capability)

    if grant is None:
        return _record(
            session, account_id=account_id, request=request, decision=AttestationDecision.DENY,
            reason=DenyReason.MISSING_POLICY, payload_hash=payload_hash, grant=None,
            principal_id=None if principal is None else principal.id, expires_at=None, now=moment,
        )
    if principal is None:
        return _record(
            session, account_id=account_id, request=request, decision=AttestationDecision.DENY,
            reason=DenyReason.PRINCIPAL_INACTIVE, payload_hash=payload_hash, grant=grant,
            principal_id=None, expires_at=None, now=moment,
        )

    decision = evaluate_policy(
        request, _grant_view(grant), _principal_view(principal), now=moment, stop_active=stop_active
    )
    return _record(
        session,
        account_id=account_id,
        request=request,
        decision=AttestationDecision.ALLOW if decision.allowed else AttestationDecision.DENY,
        reason=decision.reason,
        payload_hash=decision.payload_hash,
        grant=grant,
        principal_id=principal.id,
        expires_at=decision.expires_at,
        now=moment,
    )


def _reject(reason: DenyReason) -> DispatchResult:
    return DispatchResult(authorized=False, reason=reason)


def authorize_dispatch(
    session: Session,
    *,
    account_id: UUID,
    attestation_id: UUID,
    current_payload: Mapping[str, Any],
    current_case_revision: int,
    now: datetime | None = None,
    stop_active: bool = False,
) -> DispatchResult:
    """Re-verify authority immediately before dispatch and atomically claim the intent.

    Anything denied, revoked, superseded, expired, altered, re-revised or already
    dispatched is rejected here; only a still-valid, unclaimed allow is authorized."""
    moment = _now(now)
    attestation = session.scalars(
        select(ReconActionAttestation).where(
            ReconActionAttestation.account_id == account_id,
            ReconActionAttestation.id == attestation_id,
        )
    ).one_or_none()
    if attestation is None or attestation.decision is not AttestationDecision.ALLOW:
        return _reject(DenyReason.DENIED_AUTHORITY)
    if attestation.dispatched_at is not None:
        return _reject(DenyReason.DUPLICATE_ACTION)
    if stop_active:
        return _reject(DenyReason.CAPABILITY_STOPPED)

    grant = session.get(ReconStandingGrant, attestation.grant_id) if attestation.grant_id else None
    if grant is None or grant.account_id != account_id or not grant.active:
        return _reject(DenyReason.GRANT_REVOKED)
    if grant.policy_revision != attestation.policy_revision:
        return _reject(DenyReason.POLICY_REVISION_CHANGED)
    if attestation.expires_at is None or moment > _as_utc(attestation.expires_at):
        return _reject(DenyReason.ATTESTATION_EXPIRED)
    if current_case_revision != attestation.case_revision:
        return _reject(DenyReason.CASE_REVISION_CHANGED)
    if canonical_payload_hash(current_payload) != attestation.payload_hash:
        return _reject(DenyReason.PAYLOAD_HASH_MISMATCH)
    if attestation.system_fingerprint not in set(grant.qualified_fingerprints):
        return _reject(DenyReason.UNKNOWN_FINGERPRINT)

    # Deduplicate across the logical action: any other dispatched attestation wins.
    already = session.scalars(
        select(ReconActionAttestation).where(
            ReconActionAttestation.account_id == account_id,
            ReconActionAttestation.action_key == attestation.action_key,
            ReconActionAttestation.dispatched_at.is_not(None),
        )
    ).first()
    if already is not None:
        return _reject(DenyReason.DUPLICATE_ACTION)

    attestation.dispatched_at = moment
    session.flush()
    return DispatchResult(authorized=True, reason=DenyReason.OK)
