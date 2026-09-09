"""Private publication: outbox, atomic commit and independent read-back (work order 7).

The first release publishes only private analytical results, by transaction commit plus
an independent content-hash read-back. External dispatch is a preserved later-module
design and stays disabled here (blueprint 23).

Three records track one logical effect, and they are never collapsed into an agent
saying "sent":

* ``propose_publication`` validates the proposal, records a policy attestation (work
  order 6) and, on an allow, commits an immutable ``ReconActionIntent`` together with a
  ``ReconOutbox`` row and the case's ``publication_ready`` transition. The ``action_key``
  is stable across restarts (tenant + logical action + immutable content hash), so a
  replay cannot mint a second logical effect.
* ``publish_case`` claims the outbox row with a lease, rechecks authority through the
  executor recheck (which atomically consumes the attestation), and — in the *same*
  database transaction — writes the immutable ``ReconCasePublication`` and advances the
  case to ``published``. Denied or stale authority writes no publication.
* ``reconcile_publication`` is the independent read-back: it reloads the persisted
  publication, recomputes the content hash from the stored payload with its own
  implementation, checks the tenant, destination and that the version has not been
  superseded, and only then advances the case to ``verified_complete``. It does not
  trust any agent's narrative.

No external endpoint is ever contacted; the "provider" for a private publication is the
tenant's own committed row, read back independently.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from eastmed_schema.enums import (
    AttestationDecision,
    AutomationStatus,
    OutboxResponseClass,
    OutboxStatus,
)
from eastmed_schema.models import (
    ReconActionIntent,
    ReconCasePublication,
    ReconCaseVersion,
    ReconOperationalCase,
    ReconOutbox,
    ReconVerificationVerdict,
)
from eastmed_shared.reconciliation.policy import ActionRequest, DenyReason
from eastmed_shared.reconciliation.verifier import VerificationStatus
from sqlalchemy import select
from sqlalchemy.orm import Session

from eastmed_pipeline.reconciliation_policy import attest_action, authorize_dispatch

CAPABILITY = "operations.private_publish"
PRIVATE_PORTAL = "tenant_private_portal"
DEFAULT_CASE_TYPE = "daily_reconciliation"
DEFAULT_LEASE = timedelta(minutes=5)

# Evidence labels are no longer supplied by the caller; they are earned by the persisted
# independent verdict and only then presented to the policy attestation.
EVIDENCE_INPUTS_COMPLETE = "required_inputs_complete"
EVIDENCE_ACCEPTED_FACTS = "accepted_facts"

# Authority that is no longer valid for this exact proposal must not be retried under the
# same attestation; a fresh proposal requires a fresh attestation.
_STALE_REASONS = frozenset(
    {
        DenyReason.DENIED_AUTHORITY,
        DenyReason.GRANT_REVOKED,
        DenyReason.POLICY_REVISION_CHANGED,
        DenyReason.ATTESTATION_EXPIRED,
        DenyReason.CASE_REVISION_CHANGED,
        DenyReason.PAYLOAD_HASH_MISMATCH,
        DenyReason.UNKNOWN_FINGERPRINT,
        DenyReason.CAPABILITY_STOPPED,
        DenyReason.DUPLICATE_ACTION,
    }
)


class PublicationReason(StrEnum):
    OK = "ok"
    NOT_PUBLISHED = "not_published"
    HASH_MISMATCH = "hash_mismatch"
    WRONG_TENANT = "wrong_tenant"
    SUPERSEDED = "superseded"


class VerdictGateReason(StrEnum):
    """Why a proposal was refused before any authority was requested.

    An empty, unverified, unresolved or non-matching result cannot acquire a
    completion/publication entitlement (blueprint 19, 23). No attestation is minted for a
    gate failure — a status-only unresolved notice is a separate contract, not this one."""

    VERIFICATION_MISSING = "verification_missing"
    VERDICT_BINDING_MISMATCH = "verdict_binding_mismatch"
    NO_CHECKED_CALCULATIONS = "no_checked_calculations"
    RESULT_UNRESOLVED = "result_unresolved"
    NOT_VERIFIED = "not_verified"


@dataclass(frozen=True)
class ProposeResult:
    ready: bool
    decision: AttestationDecision
    reason: str
    attestation_id: UUID | None
    intent_id: UUID | None


@dataclass(frozen=True)
class PublishResult:
    published: bool
    reason: DenyReason
    publication_id: UUID | None


@dataclass(frozen=True)
class PublicationVerdict:
    confirmed: bool
    reason: PublicationReason


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(UTC)


def _content_hash(payload: Any) -> str:
    """Independent recompute of a case content hash from its stored payload.

    Deliberately re-implemented here (not imported from the case builder) so the
    read-back confirms the effect without depending on the publisher's own code."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def publication_action_key(account_id: UUID, case_id: UUID, content_hash: str) -> str:
    """A stable business identity for one logical publication. It is bound to the
    immutable content, so a network or process failure that replays the same content
    reuses the key rather than creating a second effect (blueprint 23)."""
    return f"{account_id}:private_publish:{case_id}:{content_hash}"


def _current_revision(session: Session, *, account_id: UUID, case_id: UUID) -> int | None:
    return session.scalars(
        select(ReconCaseVersion.revision)
        .where(
            ReconCaseVersion.account_id == account_id,
            ReconCaseVersion.case_id == case_id,
        )
        .order_by(ReconCaseVersion.revision.desc())
    ).first()


def _existing_intent(
    session: Session, *, account_id: UUID, action_key: str
) -> ReconActionIntent | None:
    return session.scalars(
        select(ReconActionIntent).where(
            ReconActionIntent.account_id == account_id,
            ReconActionIntent.action_key == action_key,
        )
    ).one_or_none()


def _load_verdict(
    session: Session, *, account_id: UUID, case_version_id: UUID
) -> ReconVerificationVerdict | None:
    return session.scalars(
        select(ReconVerificationVerdict).where(
            ReconVerificationVerdict.account_id == account_id,
            ReconVerificationVerdict.case_version_id == case_version_id,
        )
    ).one_or_none()


def _verdict_gate_reason(
    verdict: ReconVerificationVerdict,
    *,
    case: ReconOperationalCase,
    case_version: ReconCaseVersion,
) -> VerdictGateReason | None:
    """Return the reason a persisted verdict does not entitle publication, or None.

    The verdict must bind to the *exact* source, expected job, result and formula it
    judged (blueprint 19); a binding mismatch is rejected before any completion detail is
    considered. Then an empty, unresolved or unverified result is refused: only a matching
    completion verdict acquires the entitlement."""
    calc_version = str(case_version.result_json.get("calculator_version", ""))
    if (
        verdict.content_hash != case_version.content_hash
        or verdict.evidence_hash != case_version.evidence_hash
        or verdict.case_revision != case_version.revision
        or verdict.expected_job_id != case.expected_job_id
        or verdict.calculator_version != calc_version
    ):
        return VerdictGateReason.VERDICT_BINDING_MISMATCH
    if verdict.checked == 0:
        return VerdictGateReason.NO_CHECKED_CALCULATIONS
    if verdict.has_unresolved:
        return VerdictGateReason.RESULT_UNRESOLVED
    if verdict.status != VerificationStatus.VERIFIED.value or not verdict.completes:
        return VerdictGateReason.NOT_VERIFIED
    return None


def _evidence_from_verdict(verdict: ReconVerificationVerdict) -> frozenset[str]:
    """Derive the evidence labels the verdict has actually earned. The policy attestation
    only sees what the independent verifier established, never a caller-supplied label."""
    labels: set[str] = set()
    if verdict.checked > 0 and not verdict.has_unresolved:
        labels.add(EVIDENCE_INPUTS_COMPLETE)
    if verdict.status == VerificationStatus.VERIFIED.value:
        labels.add(EVIDENCE_ACCEPTED_FACTS)
    return frozenset(labels)


def _gate_denied(reason: VerdictGateReason) -> ProposeResult:
    return ProposeResult(
        ready=False,
        decision=AttestationDecision.DENY,
        reason=reason.value,
        attestation_id=None,
        intent_id=None,
    )


def propose_publication(
    session: Session,
    *,
    account_id: UUID,
    case: ReconOperationalCase,
    case_version: ReconCaseVersion,
    principal_subject: str,
    system_fingerprint: str,
    destination: str = PRIVATE_PORTAL,
    case_type: str = DEFAULT_CASE_TYPE,
    now: datetime | None = None,
    stop_active: bool = False,
) -> ProposeResult:
    """Gate on a persisted verdict, then attest and commit the intent + outbox row.

    Publication authority is never requested for a result that was not independently
    verified. This first loads the persisted verification verdict bound to this exact case
    version (blueprint 19, 23); a missing, mismatched, empty, unresolved or unverified
    verdict is refused here — no attestation receipt is minted, because an entitlement was
    never in reach. Only a matching completion verdict proceeds, and the evidence labels the
    policy sees are derived from that verdict, not from the caller.

    Idempotent on the stable ``action_key``: proposing the same immutable content twice
    returns the existing intent instead of creating a second one."""
    moment = _now(now)
    payload: Mapping[str, Any] = case_version.result_json
    action_key = publication_action_key(account_id, case.id, case_version.content_hash)

    existing = _existing_intent(session, account_id=account_id, action_key=action_key)
    if existing is not None:
        return ProposeResult(
            ready=True,
            decision=AttestationDecision.ALLOW,
            reason=DenyReason.OK.value,
            attestation_id=existing.attestation_id,
            intent_id=existing.id,
        )

    verdict = _load_verdict(session, account_id=account_id, case_version_id=case_version.id)
    if verdict is None:
        return _gate_denied(VerdictGateReason.VERIFICATION_MISSING)
    gate_reason = _verdict_gate_reason(verdict, case=case, case_version=case_version)
    if gate_reason is not None:
        return _gate_denied(gate_reason)
    provided_evidence = _evidence_from_verdict(verdict)

    request = ActionRequest(
        tenant_id=str(account_id),
        principal_subject=principal_subject,
        capability=CAPABILITY,
        case_id=str(case.id),
        case_revision=case_version.revision,
        action_key=action_key,
        destination=destination,
        case_type=case_type,
        evidence_hash=case_version.evidence_hash,
        provided_evidence=provided_evidence,
        system_fingerprint=system_fingerprint,
        payload=payload,
        requires_external=False,
        requires_financial=False,
    )
    attestation = attest_action(
        session, account_id=account_id, request=request, now=moment, stop_active=stop_active
    )
    if attestation.decision is not AttestationDecision.ALLOW:
        return ProposeResult(
            ready=False,
            decision=attestation.decision,
            reason=attestation.reason,
            attestation_id=attestation.id,
            intent_id=None,
        )

    intent = ReconActionIntent(
        account_id=account_id,
        case_id=case.id,
        case_version_id=case_version.id,
        attestation_id=attestation.id,
        action_key=action_key,
        capability=CAPABILITY,
        destination=destination,
        case_revision=case_version.revision,
        content_hash=case_version.content_hash,
        payload_hash=attestation.payload_hash,
    )
    session.add(intent)
    session.flush()
    session.add(
        ReconOutbox(
            account_id=account_id,
            intent_id=intent.id,
            action_key=action_key,
            status=OutboxStatus.PENDING,
            response_class=None,
            content_hash=case_version.content_hash,
            lease_epoch=0,
            next_attempt_at=moment,
            dispatch_attempts=0,
        )
    )
    case.automation_status = AutomationStatus.PUBLICATION_READY
    session.flush()
    return ProposeResult(
        ready=True,
        decision=AttestationDecision.ALLOW,
        reason=DenyReason.OK.value,
        attestation_id=attestation.id,
        intent_id=intent.id,
    )


def find_due_outbox(
    session: Session, *, account_id: UUID, now: datetime | None = None
) -> list[ReconOutbox]:
    """Scheduler query: pending outbox rows due for dispatch. PostgreSQL is the durable
    record, so this reconstructs wake-ups after a queue loss without duplicating a logical
    effect (blueprint 21)."""
    moment = _now(now)
    return list(
        session.scalars(
            select(ReconOutbox)
            .where(
                ReconOutbox.account_id == account_id,
                ReconOutbox.status == OutboxStatus.PENDING,
                ReconOutbox.next_attempt_at <= moment,
            )
            .order_by(ReconOutbox.next_attempt_at)
        ).all()
    )


def _load_outbox(session: Session, *, account_id: UUID, intent_id: UUID) -> ReconOutbox | None:
    stmt = select(ReconOutbox).where(
        ReconOutbox.account_id == account_id,
        ReconOutbox.intent_id == intent_id,
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    return session.scalars(stmt).one_or_none()


def _existing_publication(
    session: Session, *, account_id: UUID, intent_id: UUID
) -> ReconCasePublication | None:
    return session.scalars(
        select(ReconCasePublication).where(
            ReconCasePublication.account_id == account_id,
            ReconCasePublication.intent_id == intent_id,
        )
    ).one_or_none()


def publish_case(
    session: Session,
    *,
    account_id: UUID,
    intent: ReconActionIntent,
    current_payload: Mapping[str, Any],
    now: datetime | None = None,
    stop_active: bool = False,
) -> PublishResult:
    """Claim the outbox row, recheck authority and — atomically with consuming that
    authority — commit the private publication and advance the case.

    A crash before commit leaves nothing persisted; a replay re-claims the still-PENDING
    row and produces exactly one effect. A second call after dispatch is rejected as a
    duplicate. Denied or stale authority writes no publication."""
    moment = _now(now)
    denied = PublishResult(published=False, reason=DenyReason.DENIED_AUTHORITY, publication_id=None)
    outbox = _load_outbox(session, account_id=account_id, intent_id=intent.id)
    if outbox is None:
        return denied
    if outbox.status in (OutboxStatus.DISPATCHED, OutboxStatus.RECONCILED):
        existing = _existing_publication(session, account_id=account_id, intent_id=intent.id)
        return PublishResult(
            published=False,
            reason=DenyReason.DUPLICATE_ACTION,
            publication_id=None if existing is None else existing.id,
        )
    if outbox.status in (OutboxStatus.INVALIDATED, OutboxStatus.EFFECT_UNKNOWN):
        return denied

    # Claim with a lease and persist the dispatch attempt before "calling the provider".
    outbox.status = OutboxStatus.CLAIMED
    outbox.lease_epoch += 1
    outbox.lease_until = moment + DEFAULT_LEASE
    outbox.dispatch_attempts += 1
    session.flush()

    # Executor recheck against *current* state; this atomically consumes the attestation
    # (stamps dispatched_at). The current case revision is reloaded from the durable record
    # so a correction committed after the attestation is detected as a revision change.
    current_revision = _current_revision(session, account_id=account_id, case_id=intent.case_id)
    dispatch = authorize_dispatch(
        session,
        account_id=account_id,
        attestation_id=intent.attestation_id,
        current_payload=current_payload,
        current_case_revision=(
            intent.case_revision if current_revision is None else current_revision
        ),
        now=moment,
        stop_active=stop_active,
    )
    if not dispatch.authorized:
        outbox.terminal_reason = dispatch.reason.value
        outbox.status = (
            OutboxStatus.INVALIDATED if dispatch.reason in _STALE_REASONS else OutboxStatus.PENDING
        )
        session.flush()
        return PublishResult(published=False, reason=dispatch.reason, publication_id=None)

    # Same transaction as the authority consumption: write the private effect and advance.
    publication = ReconCasePublication(
        account_id=account_id,
        intent_id=intent.id,
        case_id=intent.case_id,
        case_revision=intent.case_revision,
        destination=intent.destination,
        content_hash=intent.content_hash,
        result_json=dict(current_payload),
        published_at=moment,
    )
    session.add(publication)
    session.flush()
    outbox.status = OutboxStatus.DISPATCHED
    outbox.response_class = OutboxResponseClass.ACCEPTED
    outbox.provider_reference = str(publication.id)
    outbox.terminal_reason = None

    case = session.get(ReconOperationalCase, intent.case_id)
    if case is not None and case.account_id == account_id:
        case.automation_status = AutomationStatus.PUBLISHED
    session.flush()
    return PublishResult(published=True, reason=DenyReason.OK, publication_id=publication.id)


def reconcile_publication(
    session: Session,
    *,
    account_id: UUID,
    intent: ReconActionIntent,
    now: datetime | None = None,
) -> PublicationVerdict:
    """Independently confirm the private effect and advance the case to verified-complete.

    Recomputes the content hash from the stored payload, checks the tenant and that the
    published revision is still current (not superseded), and only then confirms. Idempotent
    across a crash: re-running simply re-confirms the same committed publication."""
    _ = now  # read-back is time-independent; kept for a uniform signature
    publication = _existing_publication(session, account_id=account_id, intent_id=intent.id)
    if publication is None:
        return PublicationVerdict(confirmed=False, reason=PublicationReason.NOT_PUBLISHED)
    if publication.account_id != account_id:
        return PublicationVerdict(confirmed=False, reason=PublicationReason.WRONG_TENANT)

    recomputed = _content_hash(publication.result_json)
    if recomputed != publication.content_hash or publication.content_hash != intent.content_hash:
        return PublicationVerdict(confirmed=False, reason=PublicationReason.HASH_MISMATCH)

    current = _current_revision(session, account_id=account_id, case_id=publication.case_id)
    outbox = _load_outbox(session, account_id=account_id, intent_id=intent.id)
    if current is not None and current != publication.case_revision:
        if outbox is not None and outbox.status not in (
            OutboxStatus.RECONCILED,
            OutboxStatus.EFFECT_UNKNOWN,
        ):
            outbox.status = OutboxStatus.INVALIDATED
            outbox.terminal_reason = PublicationReason.SUPERSEDED.value
            session.flush()
        return PublicationVerdict(confirmed=False, reason=PublicationReason.SUPERSEDED)

    if outbox is not None:
        outbox.status = OutboxStatus.RECONCILED
        outbox.response_class = OutboxResponseClass.EFFECT_CONFIRMED
        outbox.terminal_reason = None
    case = session.get(ReconOperationalCase, publication.case_id)
    if case is not None and case.account_id == account_id:
        case.automation_status = AutomationStatus.VERIFIED_COMPLETE
    session.flush()
    return PublicationVerdict(confirmed=True, reason=PublicationReason.OK)


def invalidate_pending_publications(
    session: Session,
    *,
    account_id: UUID,
    case_id: UUID,
    current_revision: int,
    now: datetime | None = None,
) -> int:
    """Invalidate any not-yet-dispatched publication of a superseded case revision.

    When a correction commits a newer case version, a pending publication that still
    refers to the old version must not be dispatched (blueprint 32)."""
    _ = now
    rows = session.scalars(
        select(ReconOutbox)
        .join(ReconActionIntent, ReconActionIntent.id == ReconOutbox.intent_id)
        .where(
            ReconOutbox.account_id == account_id,
            ReconActionIntent.case_id == case_id,
            ReconActionIntent.case_revision < current_revision,
            ReconOutbox.status.in_((OutboxStatus.PENDING, OutboxStatus.CLAIMED)),
        )
    ).all()
    for row in rows:
        row.status = OutboxStatus.INVALIDATED
        row.terminal_reason = PublicationReason.SUPERSEDED.value
    if rows:
        session.flush()
    return len(rows)
