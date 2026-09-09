from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from eastmed_schema.enums import AICapabilityMode
from eastmed_schema.models import AIIncident, AIIncidentEvent, AISystemVersion, AuditLog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from eastmed_api.ai_governance import (
    CONTROL_ROLES,
    HUMAN_ROLES,
    MFA_ASSURANCE,
    SCOPE_PATTERN,
    AIGovernanceError,
    set_capability_control,
)
from eastmed_api.contracts import (
    AICapabilityControlCreate,
    AIIncidentCreate,
    AIIncidentEventCreate,
    AIIncidentEventRead,
    AIIncidentRead,
)
from eastmed_api.security import DeskPrincipal

IncidentStatus = Literal["open", "contained", "investigating", "resolved"]
IncidentSeverity = Literal["low", "medium", "high", "critical"]
HIGH_SEVERITIES = frozenset({"high", "critical"})


def _event_read(row: AIIncidentEvent) -> AIIncidentEventRead:
    return AIIncidentEventRead(
        id=row.id,
        incident_id=row.incident_id,
        sequence=row.sequence,
        status=cast(IncidentStatus, row.status),
        severity=cast(IncidentSeverity, row.severity),
        summary=row.summary,
        containment_action=row.containment_action,
        evidence_refs=list(row.evidence_refs_json),
        changed_by_user_id=row.changed_by_user_id,
        auth_assurance=row.auth_assurance,
        at=row.at,
    )


def incident_read(session: Session, row: AIIncident) -> AIIncidentRead:
    events = list(
        session.scalars(
            select(AIIncidentEvent)
            .where(AIIncidentEvent.incident_id == row.id)
            .order_by(AIIncidentEvent.sequence)
        ).all()
    )
    if not events:
        raise AIGovernanceError("AI incident has no lifecycle event")
    latest = events[-1]
    return AIIncidentRead(
        id=row.id,
        title=row.title,
        capability_scope=row.capability_scope,
        system_version_id=row.system_version_id,
        detected_at=row.detected_at,
        reported_by_user_id=row.reported_by_user_id,
        created_at=row.created_at,
        status=cast(IncidentStatus, latest.status),
        severity=cast(IncidentSeverity, latest.severity),
        events=[_event_read(event) for event in events],
    )


def list_ai_incidents(
    session: Session, *, include_resolved: bool = True
) -> list[AIIncidentRead]:
    rows = list(session.scalars(select(AIIncident).order_by(AIIncident.created_at.desc())).all())
    incidents = [incident_read(session, row) for row in rows]
    if include_resolved:
        return incidents
    return [incident for incident in incidents if incident.status != "resolved"]


def _append_event(
    session: Session,
    *,
    incident: AIIncident,
    sequence: int,
    status: IncidentStatus,
    severity: IncidentSeverity,
    summary: str,
    containment_action: str | None,
    evidence_refs: list[str],
    principal: DeskPrincipal,
) -> AIIncidentEvent:
    row = AIIncidentEvent(
        incident_id=incident.id,
        sequence=sequence,
        status=status,
        severity=severity,
        summary=summary,
        containment_action=containment_action,
        evidence_refs_json=evidence_refs,
        changed_by_user_id=principal.user_id,
        auth_assurance=principal.assurance,
        at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    session.add(
        AuditLog(
            actor=principal.actor,
            action="ai_incident.lifecycle_event",
            entity="ai_incident",
            entity_id=incident.id,
            payload_json={
                "event_id": str(row.id),
                "sequence": sequence,
                "status": status,
                "severity": severity,
                "summary": summary,
                "containment_action": containment_action,
                "evidence_refs": evidence_refs,
                "auth_assurance": principal.assurance.value,
            },
        )
    )
    return row


def create_ai_incident(
    session: Session, *, payload: AIIncidentCreate, principal: DeskPrincipal
) -> AIIncidentRead:
    if principal.role not in HUMAN_ROLES:
        raise AIGovernanceError("A service identity cannot report an AI incident")
    scope = payload.capability_scope.casefold()
    if not SCOPE_PATTERN.fullmatch(scope):
        raise AIGovernanceError("Invalid AI capability scope")
    if payload.system_version_id is not None and session.get(
        AISystemVersion, payload.system_version_id
    ) is None:
        raise AIGovernanceError("AI system version not found")
    containment_action = payload.containment_action
    initial_status: IncidentStatus = "open"
    if payload.severity in HIGH_SEVERITIES:
        containment_action = containment_action or (
            f"Automatically disabled AI capability scope {scope} at incident creation."
        )
        set_capability_control(
            session,
            scope=scope,
            payload=AICapabilityControlCreate(
                mode=AICapabilityMode.DISABLED,
                risk_tier=4 if payload.severity == "critical" else 3,
                reason=f"Automatic incident containment: {payload.title}. {payload.summary}",
            ),
            principal=principal,
            commit=False,
        )
        initial_status = "contained"
    incident = AIIncident(
        title=payload.title,
        capability_scope=scope,
        system_version_id=payload.system_version_id,
        detected_at=payload.detected_at,
        reported_by_user_id=principal.user_id,
        created_at=datetime.now(UTC),
    )
    session.add(incident)
    try:
        session.flush()
        _append_event(
            session,
            incident=incident,
            sequence=1,
            status=initial_status,
            severity=payload.severity,
            summary=payload.summary,
            containment_action=containment_action,
            evidence_refs=payload.evidence_refs,
            principal=principal,
        )
        session.commit()
        session.refresh(incident)
    except IntegrityError as exc:
        session.rollback()
        raise AIGovernanceError("AI incident changed concurrently; retry") from exc
    return incident_read(session, incident)


def append_ai_incident_event(
    session: Session,
    *,
    incident_id: UUID,
    payload: AIIncidentEventCreate,
    principal: DeskPrincipal,
) -> AIIncidentRead:
    if principal.role not in HUMAN_ROLES:
        raise AIGovernanceError("A service identity cannot update an AI incident")
    incident = session.get(AIIncident, incident_id)
    if incident is None:
        raise AIGovernanceError("AI incident not found")
    existing = list(
        session.scalars(
            select(AIIncidentEvent)
            .where(AIIncidentEvent.incident_id == incident.id)
            .order_by(AIIncidentEvent.sequence)
        ).all()
    )
    if not existing:
        raise AIGovernanceError("AI incident has no lifecycle event")
    latest = existing[-1]
    if latest.status == "resolved":
        raise AIGovernanceError("A resolved AI incident is immutable")
    if payload.status == "contained" and not payload.containment_action:
        raise AIGovernanceError("Containment requires an explicit containment action")
    if payload.status == "resolved":
        if principal.role not in CONTROL_ROLES or principal.assurance not in MFA_ASSURANCE:
            raise AIGovernanceError(
                "Resolving an AI incident requires an MFA-authenticated control owner"
            )
        if payload.severity in HIGH_SEVERITIES and not any(
            event.status == "contained" and event.containment_action for event in existing
        ):
            raise AIGovernanceError(
                "A high-severity AI incident must be contained before resolution"
            )
    try:
        _append_event(
            session,
            incident=incident,
            sequence=latest.sequence + 1,
            status=payload.status,
            severity=payload.severity,
            summary=payload.summary,
            containment_action=payload.containment_action,
            evidence_refs=payload.evidence_refs,
            principal=principal,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AIGovernanceError("AI incident changed concurrently; retry") from exc
    return incident_read(session, incident)


def count_open_ai_incidents(session: Session) -> int:
    latest_sequences = (
        select(
            AIIncidentEvent.incident_id,
            func.max(AIIncidentEvent.sequence).label("sequence"),
        )
        .group_by(AIIncidentEvent.incident_id)
        .subquery()
    )
    return int(
        session.scalar(
            select(func.count())
            .select_from(AIIncidentEvent)
            .join(
                latest_sequences,
                (AIIncidentEvent.incident_id == latest_sequences.c.incident_id)
                & (AIIncidentEvent.sequence == latest_sequences.c.sequence),
            )
            .where(AIIncidentEvent.status != "resolved")
        )
        or 0
    )
