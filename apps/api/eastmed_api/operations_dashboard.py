from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from eastmed_schema.enums import (
    ClaimExtractionProposalStatus,
    DeliveryStatus,
    DeskAlertStatus,
    EventStatus,
    TriageStatus,
)
from eastmed_schema.models import (
    AICapabilityControl,
    ClaimExtractionProposal,
    CorrectionDelivery,
    Delivery,
    DeskAlert,
    DeskApproval,
    DeskApprovalRequest,
    Event,
    Source,
    TriageItem,
)
from eastmed_shared import get_settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from eastmed_api.ai_governance import list_latest_capability_controls
from eastmed_api.ai_incidents import count_open_ai_incidents
from eastmed_api.contracts import (
    AICapabilityControlRead,
    LaunchReadinessCheckRead,
    OperationsDashboardRead,
)
from eastmed_api.poller_health import poller_health


def _count(
    session: Session, model: type[Any], *conditions: ColumnElement[bool]
) -> int:
    query = select(func.count()).select_from(model)
    for condition in conditions:
        query = query.where(condition)
    return int(session.scalar(query) or 0)


def _control_read(row: AICapabilityControl) -> AICapabilityControlRead:
    return AICapabilityControlRead(
        id=row.id,
        scope=row.scope,
        revision=row.revision,
        mode=row.mode,
        risk_tier=row.risk_tier,
        system_version_id=row.system_version_id,
        reason=row.reason,
        approval_refs=row.approval_refs_json,
        changed_by_user_id=row.changed_by_user_id,
        auth_assurance=row.auth_assurance,
        previous_control_id=row.previous_control_id,
        expires_at=row.expires_at,
        changed_at=row.changed_at,
    )


def operations_dashboard(session: Session) -> OperationsDashboardRead:
    settings = get_settings()
    now = datetime.now(UTC)
    pending_triage = _count(session, TriageItem, TriageItem.status == TriageStatus.PENDING)
    open_events = _count(session, Event, Event.status != EventStatus.CLOSED)
    high_severity_events = _count(
        session,
        Event,
        Event.status != EventStatus.CLOSED,
        Event.severity >= 3,
    )
    pending_extraction = _count(
        session,
        ClaimExtractionProposal,
        ClaimExtractionProposal.status == ClaimExtractionProposalStatus.PENDING,
    )
    open_alerts = _count(session, DeskAlert, DeskAlert.status != DeskAlertStatus.RESOLVED)
    pending_approvals = _count(
        session,
        DeskApprovalRequest,
        DeskApprovalRequest.status == "pending",
        DeskApprovalRequest.expires_at > now,
    )
    expiring_releases = _count(
        session,
        DeskApproval,
        DeskApproval.expires_at > now,
        DeskApproval.expires_at <= now + timedelta(minutes=15),
    )
    open_ai_incidents = count_open_ai_incidents(session)

    sources = list(session.scalars(select(Source).order_by(Source.name)).all())
    active_sources = sum(source.active for source in sources)
    source_health: dict[str, int] = {}
    for source in sources:
        state = poller_health(source, now=now).state
        source_health[state] = source_health.get(state, 0) + 1

    delivery_status = {
        state.value: _count(session, Delivery, Delivery.status == state)
        + _count(session, CorrectionDelivery, CorrectionDelivery.status == state)
        for state in DeliveryStatus
    }
    controls = list_latest_capability_controls(session)
    global_control = next((row for row in controls if row.scope == "all_model_calls"), None)
    global_control_active = bool(
        global_control
        and global_control.mode.value != "disabled"
        and global_control.expires_at
        and (
            global_control.expires_at.replace(tzinfo=UTC)
            if global_control.expires_at.tzinfo is None
            else global_control.expires_at
        )
        > now
    )
    unhealthy_sources = sum(
        source_health.get(state, 0) for state in ("overdue", "degraded", "blocked_rights")
    )
    failed_deliveries = delivery_status.get(DeliveryStatus.FAILED.value, 0)

    readiness = [
        LaunchReadinessCheckRead(
            key="database",
            label="Database connection",
            state="pass",
            detail="The dashboard query and canonical schema are available.",
        ),
        LaunchReadinessCheckRead(
            key="desk_identity",
            label="Production desk identity",
            state="pass" if settings.desk_auth_mode == "oidc" else "warning",
            detail=(
                "Cryptographically verified OIDC is active."
                if settings.desk_auth_mode == "oidc"
                else "Development trusted-proxy mode is active; production startup will reject it."
            ),
            href="/console/approvals",
        ),
        LaunchReadinessCheckRead(
            key="source_coverage",
            label="Approved source coverage",
            state=(
                "pass"
                if active_sources >= settings.launch_min_active_sources
                else "warning"
            ),
            detail=(
                f"{active_sources} active sources; launch target is "
                f"{settings.launch_min_active_sources}."
            ),
            href="/console/triage",
        ),
        LaunchReadinessCheckRead(
            key="source_health",
            label="Poller health",
            state="fail" if unhealthy_sources else "pass",
            detail=f"{unhealthy_sources} sources are overdue, degraded, or rights-blocked.",
        ),
        LaunchReadinessCheckRead(
            key="delivery_health",
            label="Delivery health",
            state="fail" if failed_deliveries else "pass",
            detail=f"{failed_deliveries} alert or correction deliveries are failed.",
            href="/console/alerts",
        ),
        LaunchReadinessCheckRead(
            key="outbound_switch",
            label="Outbound release switch",
            state="pass" if settings.outbound_enabled else "warning",
            detail=(
                "Outbound release is enabled."
                if settings.outbound_enabled
                else "Kill switch is active; no alert or correction can leave the platform."
            ),
        ),
        LaunchReadinessCheckRead(
            key="ai_control",
            label="AI execution control",
            state="pass" if global_control_active else "warning",
            detail=(
                "The global AI control is active and time bounded."
                if global_control_active
                else "AI execution is disabled or lacks an unexpired global control."
            ),
            href="/console/operations",
        ),
        LaunchReadinessCheckRead(
            key="ai_incidents",
            label="AI incident queue",
            state="fail" if open_ai_incidents else "pass",
            detail=f"{open_ai_incidents} AI incidents remain unresolved.",
            href="/console/operations",
        ),
        LaunchReadinessCheckRead(
            key="desk_alerts",
            label="Unresolved desk alerts",
            state="fail" if open_alerts else "pass",
            detail=f"{open_alerts} source or ingestion alerts require attention.",
        ),
    ]
    if any(check.state == "fail" for check in readiness):
        overall_status = "action_required"
    elif any(check.state == "warning" for check in readiness):
        overall_status = "degraded"
    else:
        overall_status = "ready"

    return OperationsDashboardRead(
        generated_at=now,
        environment=settings.environment,
        overall_status=cast(
            Literal["ready", "degraded", "action_required"], overall_status
        ),
        outbound_enabled=settings.outbound_enabled,
        desk_auth_mode=settings.desk_auth_mode,
        pending_triage=pending_triage,
        open_events=open_events,
        high_severity_events=high_severity_events,
        pending_extraction_proposals=pending_extraction,
        open_desk_alerts=open_alerts,
        pending_approval_requests=pending_approvals,
        approved_releases_expiring=expiring_releases,
        open_ai_incidents=open_ai_incidents,
        active_sources=active_sources,
        total_sources=len(sources),
        source_health=source_health,
        delivery_status=delivery_status,
        ai_controls=[_control_read(row) for row in controls],
        readiness=readiness,
    )
