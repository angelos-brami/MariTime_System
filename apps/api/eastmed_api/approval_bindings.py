from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from eastmed_schema.enums import AuthAssurance, DeskRole
from eastmed_schema.models import Claim, DeskApproval, DeskUser, Evidence
from sqlalchemy import select
from sqlalchemy.orm import Session

CLAIM_SECOND_REVIEW_APPROVAL = "claim_second_review"
SECOND_REVIEWER_ROLES = frozenset({DeskRole.SENIOR_ANALYST, DeskRole.ADMINISTRATOR})
PRIMARY_REVIEWER_ROLES = frozenset(
    {DeskRole.ANALYST, DeskRole.SENIOR_ANALYST, DeskRole.ADMINISTRATOR}
)
STRONG_ASSURANCE = frozenset({AuthAssurance.MFA, AuthAssurance.PHISHING_RESISTANT})


def desk_actor_user_id(actor: str | None) -> UUID | None:
    if actor is None or not actor.startswith("desk:"):
        return None
    try:
        return UUID(actor.removeprefix("desk:"))
    except ValueError:
        return None


def claim_review_snapshot(session: Session, *, claim: Claim) -> dict[str, Any]:
    evidence = list(
        session.scalars(
            select(Evidence).where(Evidence.claim_id == claim.id).order_by(Evidence.id)
        ).all()
    )
    return {
        "claim_id": str(claim.id),
        "event_id": str(claim.event_id),
        "text": claim.text,
        "claimant": claim.claimant,
        "claim_state": claim.claim_state.value,
        "occurred_at": claim.occurred_at.isoformat() if claim.occurred_at else None,
        "quantity": claim.quantity_json,
        "proposed_by": claim.proposed_by,
        "reviewed_by": claim.reviewed_by,
        "reviewed_at": claim.reviewed_at.isoformat() if claim.reviewed_at else None,
        "sensitivity_flags": claim.sensitivity_flags,
        "evidence": [
            {
                "id": str(row.id),
                "source_record_id": str(row.source_record_id),
                "directness": row.directness.value,
                "lineage_root_id": str(row.lineage_root_id),
                "excerpt": row.excerpt,
                "capture_snapshot_r2": row.capture_snapshot_r2,
                "rights_decision": row.rights_decision,
            }
            for row in evidence
        ],
    }


def binding_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def claim_second_review_is_valid(session: Session, *, claim: Claim) -> bool:
    if claim.second_review_approval_id is None:
        return False
    approval = session.get(DeskApproval, claim.second_review_approval_id)
    if approval is None:
        return False
    primary_id = desk_actor_user_id(claim.reviewed_by)
    approver_id = desk_actor_user_id(claim.second_reviewed_by)
    if primary_id is None or approver_id is None:
        return False
    primary = session.get(DeskUser, primary_id)
    approver = session.get(DeskUser, approver_id)
    expires_at = approval.expires_at
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        approval.approval_type != CLAIM_SECOND_REVIEW_APPROVAL
        or approval.target_id != claim.id
        or approval.primary_user_id != primary_id
        or approval.approved_by_user_id != approver_id
        or primary_id == approver_id
        or approval.auth_assurance not in STRONG_ASSURANCE
        or expires_at <= datetime.now(UTC)
        or primary is None
        or not primary.active
        or primary.role not in PRIMARY_REVIEWER_ROLES
        or approver is None
        or not approver.active
        or approver.role not in SECOND_REVIEWER_ROLES
    ):
        return False
    return approval.binding_hash == binding_hash(claim_review_snapshot(session, claim=claim))
