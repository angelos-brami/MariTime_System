"""Deterministic policy evaluation and action attestations (blueprint 12, 17, 22).

Policy is deterministic application code over immutable authority records. An agent
proposes an action; this service decides whether that exact proposal is permitted now
and records a machine-readable reason for both allows and denials. It never contacts an
external endpoint and never mints its own authority — grants come only from controlled
provisioning.

An allow binds an attestation to the tenant, case revision, evidence hash, exact
canonical payload hash, destination, policy revision, system fingerprint, issuance and
expiry. Any semantically meaningful change to the payload changes the hash, so a stale
or altered proposal cannot pass the executor's later recheck.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

# The only scopes the first release recognizes (blueprint 29, phase 3). An unrecognized
# capability denies execution.
KNOWN_CAPABILITIES = frozenset(
    {"operations.reconcile", "operations.private_publish", "operations.authorized_source_read"}
)


class DenyReason(StrEnum):
    OK = "ok"
    MISSING_POLICY = "missing_policy"
    UNRECOGNIZED_ACTION = "unrecognized_action"
    CROSS_TENANT_PRINCIPAL = "cross_tenant_principal"
    PRINCIPAL_INACTIVE = "principal_inactive"
    SCOPE_NOT_GRANTED = "scope_not_granted"
    CAPABILITY_STOPPED = "capability_stopped"
    GRANT_INACTIVE = "grant_inactive"
    GRANT_EXPIRED = "grant_expired"
    CAPABILITY_MISMATCH = "capability_mismatch"
    UNKNOWN_FINGERPRINT = "unknown_fingerprint"
    CASE_TYPE_NOT_ALLOWED = "case_type_not_allowed"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    DESTINATION_NOT_ALLOWED = "destination_not_allowed"
    EXTERNAL_NOT_PERMITTED = "external_not_permitted"
    FINANCIAL_NOT_PERMITTED = "financial_not_permitted"
    # Executor recheck reasons.
    DENIED_AUTHORITY = "denied_authority"
    GRANT_REVOKED = "grant_revoked"
    POLICY_REVISION_CHANGED = "policy_revision_changed"
    ATTESTATION_EXPIRED = "attestation_expired"
    CASE_REVISION_CHANGED = "case_revision_changed"
    PAYLOAD_HASH_MISMATCH = "payload_hash_mismatch"
    DUPLICATE_ACTION = "duplicate_action"


@dataclass(frozen=True)
class GrantView:
    policy_id: str
    policy_revision: int
    capability: str
    allowed_case_types: frozenset[str]
    allowed_destinations: frozenset[str]
    external_messages: bool
    financial_commitments: bool
    required_evidence: frozenset[str]
    qualified_fingerprints: frozenset[str]
    attestation_ttl_seconds: int
    signing_key_id: str
    valid_from: datetime
    valid_to: datetime
    active: bool


@dataclass(frozen=True)
class PrincipalView:
    subject: str
    tenant_id: str
    allowed_scopes: frozenset[str]
    deployment_fingerprint: str
    active: bool


@dataclass(frozen=True)
class ActionRequest:
    tenant_id: str
    principal_subject: str
    capability: str
    case_id: str
    case_revision: int
    action_key: str
    destination: str
    case_type: str
    evidence_hash: str
    provided_evidence: frozenset[str]
    system_fingerprint: str
    payload: Mapping[str, Any]
    requires_external: bool = False
    requires_financial: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: DenyReason
    payload_hash: str
    expires_at: datetime | None = None
    signing_key_id: str | None = None


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def evaluate_policy(
    request: ActionRequest,
    grant: GrantView,
    principal: PrincipalView,
    *,
    now: datetime,
    stop_active: bool = False,
) -> PolicyDecision:
    """Evaluate the proposal in the blueprint-22 order and bind an attestation on allow.

    Every path computes the canonical payload hash so a denial receipt still records the
    exact proposal it refused."""
    payload_hash = canonical_payload_hash(request.payload)

    def deny(reason: DenyReason) -> PolicyDecision:
        return PolicyDecision(allowed=False, reason=reason, payload_hash=payload_hash)

    if request.capability not in KNOWN_CAPABILITIES:
        return deny(DenyReason.UNRECOGNIZED_ACTION)

    # 1. Tenant contract and service principal.
    if principal.subject != request.principal_subject or not principal.active:
        return deny(DenyReason.PRINCIPAL_INACTIVE)
    if principal.tenant_id != request.tenant_id:
        return deny(DenyReason.CROSS_TENANT_PRINCIPAL)
    if request.capability not in principal.allowed_scopes:
        return deny(DenyReason.SCOPE_NOT_GRANTED)

    # 2. Global/tenant/capability stops.
    if stop_active:
        return deny(DenyReason.CAPABILITY_STOPPED)

    # 3. Active grant and its validity interval.
    if not grant.active:
        return deny(DenyReason.GRANT_INACTIVE)
    if grant.capability != request.capability:
        return deny(DenyReason.CAPABILITY_MISMATCH)
    moment = _as_utc(now)
    if not _as_utc(grant.valid_from) <= moment < _as_utc(grant.valid_to):
        return deny(DenyReason.GRANT_EXPIRED)

    # 4. Qualified system fingerprint (grant and principal must agree).
    if (
        request.system_fingerprint not in grant.qualified_fingerprints
        or request.system_fingerprint != principal.deployment_fingerprint
    ):
        return deny(DenyReason.UNKNOWN_FINGERPRINT)

    # 5. Case type and risk class.
    if request.case_type not in grant.allowed_case_types:
        return deny(DenyReason.CASE_TYPE_NOT_ALLOWED)

    # 6. Evidence freshness/completeness.
    if not request.evidence_hash or not grant.required_evidence <= request.provided_evidence:
        return deny(DenyReason.EVIDENCE_INCOMPLETE)

    # 7. Resource/recipient limits.
    if request.destination not in grant.allowed_destinations:
        return deny(DenyReason.DESTINATION_NOT_ALLOWED)
    if request.requires_external and not grant.external_messages:
        return deny(DenyReason.EXTERNAL_NOT_PERMITTED)
    if request.requires_financial and not grant.financial_commitments:
        return deny(DenyReason.FINANCIAL_NOT_PERMITTED)

    expires_at = moment + timedelta(seconds=grant.attestation_ttl_seconds)
    return PolicyDecision(
        allowed=True,
        reason=DenyReason.OK,
        payload_hash=payload_hash,
        expires_at=expires_at,
        signing_key_id=grant.signing_key_id,
    )


__all__ = [
    "KNOWN_CAPABILITIES",
    "ActionRequest",
    "DenyReason",
    "GrantView",
    "PolicyDecision",
    "PrincipalView",
    "canonical_payload_hash",
    "evaluate_policy",
]
