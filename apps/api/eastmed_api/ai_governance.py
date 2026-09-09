from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from eastmed_schema.enums import AICapabilityMode, AuthAssurance, DeskRole
from eastmed_schema.models import (
    AICapabilityControl,
    AISystemVersion,
    AuditLog,
    DeskUser,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from eastmed_api.contracts import AICapabilityControlCreate, AISystemManifest, DeskUserCreate
from eastmed_api.security import DeskPrincipal

GLOBAL_AI_SCOPE = "all_model_calls"
CLAIM_EXTRACTION_SCOPE = "claim_extraction"
SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
HUMAN_ROLES = frozenset(
    {
        DeskRole.ANALYST,
        DeskRole.SENIOR_ANALYST,
        DeskRole.ADMINISTRATOR,
        DeskRole.SECURITY,
        DeskRole.COMPLIANCE,
    }
)
CONTROL_ROLES = frozenset({DeskRole.SENIOR_ANALYST, DeskRole.ADMINISTRATOR, DeskRole.SECURITY})
PRIVILEGED_CONTROL_ROLES = frozenset({DeskRole.ADMINISTRATOR, DeskRole.SECURITY})
MFA_ASSURANCE = frozenset({AuthAssurance.MFA, AuthAssurance.PHISHING_RESISTANT})
MIN_CONTROL_VALIDITY = timedelta(minutes=5)
MAX_CONTROL_VALIDITY = timedelta(days=30)


class AIGovernanceError(ValueError):
    pass


@dataclass(frozen=True)
class AICapabilityDecision:
    allowed: bool
    reason: str
    mode: AICapabilityMode
    control_id: UUID | None = None
    system_version_id: UUID | None = None


def canonical_manifest_json(manifest: AISystemManifest) -> str:
    return json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def fingerprint_manifest(manifest: AISystemManifest) -> str:
    return hashlib.sha256(canonical_manifest_json(manifest).encode("utf-8")).hexdigest()


def bootstrap_first_desk_user(
    session: Session,
    *,
    auth_issuer: str,
    auth_subject: str,
    email: str,
    display_name: str,
    role: DeskRole,
) -> DeskUser:
    identity = DeskUserCreate(
        auth_issuer=auth_issuer,
        auth_subject=auth_subject,
        email=email,
        display_name=display_name,
        role=role,
    )
    auth_issuer = identity.auth_issuer
    auth_subject = identity.auth_subject
    email = identity.email.casefold()
    display_name = identity.display_name
    existing_count = session.scalar(select(func.count()).select_from(DeskUser)) or 0
    existing = session.scalar(
        select(DeskUser).where(
            DeskUser.auth_issuer == auth_issuer,
            DeskUser.auth_subject == auth_subject,
        )
    )
    if existing is not None:
        if (
            existing.email == email
            and existing.display_name == display_name
            and existing.role == role
            and existing.active
        ):
            return existing
        raise AIGovernanceError("Bootstrap identity already exists with different attributes")
    if existing_count:
        raise AIGovernanceError("Bootstrap is disabled after the first desk identity exists")
    user = DeskUser(
        auth_issuer=auth_issuer,
        auth_subject=auth_subject,
        email=email,
        display_name=display_name,
        role=role,
        active=True,
        created_by="bootstrap",
    )
    session.add(user)
    session.flush()
    session.add(
        AuditLog(
            actor="bootstrap",
            action="desk_user.bootstrapped",
            entity="desk_user",
            entity_id=user.id,
            payload_json={
                "auth_issuer": auth_issuer,
                "auth_subject": auth_subject,
                "email": email,
                "role": role.value,
            },
        )
    )
    session.commit()
    session.refresh(user)
    return user


def create_desk_user(
    session: Session,
    *,
    auth_issuer: str,
    auth_subject: str,
    email: str,
    display_name: str,
    role: DeskRole,
    principal: DeskPrincipal,
) -> DeskUser:
    if principal.role != DeskRole.ADMINISTRATOR:
        raise AIGovernanceError("Only an administrator can create a desk identity")
    if principal.assurance != AuthAssurance.PHISHING_RESISTANT:
        raise AIGovernanceError("Desk identity creation requires phishing-resistant authentication")
    identity = DeskUserCreate(
        auth_issuer=auth_issuer,
        auth_subject=auth_subject,
        email=email,
        display_name=display_name,
        role=role,
    )
    user = DeskUser(
        auth_issuer=identity.auth_issuer,
        auth_subject=identity.auth_subject,
        email=identity.email.casefold(),
        display_name=identity.display_name,
        role=identity.role,
        active=True,
        created_by=principal.actor,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise AIGovernanceError("Desk identity subject or email already exists") from exc
    session.add(
        AuditLog(
            actor=principal.actor,
            action="desk_user.created",
            entity="desk_user",
            entity_id=user.id,
            payload_json={
                "auth_issuer": auth_issuer,
                "auth_subject": auth_subject,
                "email": email,
                "role": role.value,
                "auth_assurance": principal.assurance.value,
            },
        )
    )
    session.commit()
    session.refresh(user)
    return user


def register_ai_system_version(
    session: Session,
    *,
    name: str,
    purpose: str,
    manifest: AISystemManifest,
    principal: DeskPrincipal,
) -> AISystemVersion:
    if principal.role not in PRIVILEGED_CONTROL_ROLES:
        raise AIGovernanceError("Only administrators or security can register AI systems")
    if principal.assurance not in MFA_ASSURANCE:
        raise AIGovernanceError("AI system registration requires MFA")
    fingerprint = fingerprint_manifest(manifest)
    existing = session.scalar(
        select(AISystemVersion).where(AISystemVersion.fingerprint == fingerprint)
    )
    if existing is not None:
        if existing.name != name or existing.purpose != purpose:
            raise AIGovernanceError(
                "AI system fingerprint already exists with different immutable metadata"
            )
        return existing
    manifest_json = manifest.model_dump(mode="json")
    system_version = AISystemVersion(
        name=name,
        purpose=purpose,
        manifest_schema_version=manifest.schema_version,
        manifest_json=manifest_json,
        fingerprint=fingerprint,
        created_by_user_id=principal.user_id,
        created_at=datetime.now(UTC),
    )
    session.add(system_version)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raced = session.scalar(
            select(AISystemVersion).where(AISystemVersion.fingerprint == fingerprint)
        )
        if raced is not None:
            if raced.name != name or raced.purpose != purpose:
                raise AIGovernanceError(
                    "AI system fingerprint already exists with different immutable metadata"
                ) from None
            return raced
        raise
    session.add(
        AuditLog(
            actor=principal.actor,
            action="ai_system_version.registered",
            entity="ai_system_version",
            entity_id=system_version.id,
            payload_json={
                "name": name,
                "fingerprint": fingerprint,
                "manifest_schema_version": manifest.schema_version,
                "auth_assurance": principal.assurance.value,
            },
        )
    )
    session.commit()
    session.refresh(system_version)
    return system_version


def list_ai_system_versions(session: Session) -> list[AISystemVersion]:
    return list(
        session.scalars(
            select(AISystemVersion).order_by(
                AISystemVersion.created_at.desc(), AISystemVersion.id.desc()
            )
        ).all()
    )


def latest_capability_control(session: Session, *, scope: str) -> AICapabilityControl | None:
    return session.scalar(
        select(AICapabilityControl)
        .where(AICapabilityControl.scope == scope)
        .order_by(AICapabilityControl.revision.desc())
        .limit(1)
    )


def list_latest_capability_controls(session: Session) -> list[AICapabilityControl]:
    revisions = (
        select(
            AICapabilityControl.scope,
            func.max(AICapabilityControl.revision).label("revision"),
        )
        .group_by(AICapabilityControl.scope)
        .subquery()
    )
    return list(
        session.scalars(
            select(AICapabilityControl)
            .join(
                revisions,
                (AICapabilityControl.scope == revisions.c.scope)
                & (AICapabilityControl.revision == revisions.c.revision),
            )
            .order_by(AICapabilityControl.scope)
        ).all()
    )


def _validate_control_transition(
    *,
    scope: str,
    payload: AICapabilityControlCreate,
    principal: DeskPrincipal,
) -> None:
    if not SCOPE_PATTERN.fullmatch(scope):
        raise AIGovernanceError("Invalid AI capability scope")
    if payload.mode == AICapabilityMode.DISABLED:
        if principal.role not in HUMAN_ROLES:
            raise AIGovernanceError("A service identity cannot change a kill switch")
        return
    if payload.mode == AICapabilityMode.AUTOMATED:
        raise AIGovernanceError(
            "Automated mode is locked until signed approvals and graduation evidence "
            "are implemented"
        )
    if principal.role not in CONTROL_ROLES:
        raise AIGovernanceError("Desk role cannot enable an AI capability")
    if principal.assurance not in MFA_ASSURANCE:
        raise AIGovernanceError("Enabling an AI capability requires MFA")
    if payload.expires_at is None:
        raise AIGovernanceError("An enabled AI capability requires an expiry")
    now = datetime.now(UTC)
    if payload.expires_at < now + MIN_CONTROL_VALIDITY:
        raise AIGovernanceError("AI capability authorization must remain valid for five minutes")
    if payload.expires_at > now + MAX_CONTROL_VALIDITY:
        raise AIGovernanceError("AI capability authorization cannot exceed 30 days")
    if scope == GLOBAL_AI_SCOPE:
        if payload.system_version_id is not None:
            raise AIGovernanceError("The global AI switch cannot bind one system version")
        if payload.risk_tier != 0:
            raise AIGovernanceError("The global AI switch must use risk tier 0")
        return
    if payload.system_version_id is None:
        raise AIGovernanceError("An enabled capability requires an AI system version")


def set_capability_control(
    session: Session,
    *,
    scope: str,
    payload: AICapabilityControlCreate,
    principal: DeskPrincipal,
    commit: bool = True,
) -> AICapabilityControl:
    normalized_scope = scope.strip().casefold()
    _validate_control_transition(scope=normalized_scope, payload=payload, principal=principal)
    if payload.system_version_id is not None:
        system_version = session.get(AISystemVersion, payload.system_version_id)
        if system_version is None:
            raise AIGovernanceError("AI system version not found")
    previous = latest_capability_control(session, scope=normalized_scope)
    control = AICapabilityControl(
        scope=normalized_scope,
        revision=(previous.revision + 1 if previous else 1),
        mode=payload.mode,
        risk_tier=payload.risk_tier,
        system_version_id=payload.system_version_id,
        reason=payload.reason,
        approval_refs_json=dict(payload.approval_refs),
        changed_by_user_id=principal.user_id,
        auth_assurance=principal.assurance,
        previous_control_id=previous.id if previous else None,
        expires_at=payload.expires_at,
        changed_at=datetime.now(UTC),
    )
    session.add(control)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise AIGovernanceError("AI capability control changed concurrently; retry") from exc
    session.add(
        AuditLog(
            actor=principal.actor,
            action="ai_capability_control.changed",
            entity="ai_capability_control",
            entity_id=control.id,
            payload_json={
                "scope": normalized_scope,
                "revision": control.revision,
                "mode": control.mode.value,
                "risk_tier": control.risk_tier,
                "system_version_id": (
                    str(control.system_version_id) if control.system_version_id else None
                ),
                "previous_control_id": (
                    str(control.previous_control_id) if control.previous_control_id else None
                ),
                "reason": control.reason,
                "auth_assurance": principal.assurance.value,
                "expires_at": (
                    control.expires_at.isoformat() if control.expires_at is not None else None
                ),
            },
        )
    )
    if commit:
        session.commit()
        session.refresh(control)
    return control


def capability_decision(
    session: Session,
    *,
    scope: str,
    expected_fingerprint: str | None,
) -> AICapabilityDecision:
    normalized_scope = scope.strip().casefold()
    if not SCOPE_PATTERN.fullmatch(normalized_scope):
        return AICapabilityDecision(False, "invalid-scope", AICapabilityMode.DISABLED)
    global_control = latest_capability_control(session, scope=GLOBAL_AI_SCOPE)
    if global_control is None:
        return AICapabilityDecision(False, "global-control-missing", AICapabilityMode.DISABLED)
    if global_control.mode == AICapabilityMode.DISABLED:
        return AICapabilityDecision(
            False,
            "global-kill-switch-active",
            AICapabilityMode.DISABLED,
            control_id=global_control.id,
        )
    if _control_is_expired(global_control):
        return AICapabilityDecision(
            False,
            "global-control-expired",
            AICapabilityMode.DISABLED,
            control_id=global_control.id,
        )
    if normalized_scope == GLOBAL_AI_SCOPE:
        return AICapabilityDecision(
            True,
            "global-enabled",
            global_control.mode,
            control_id=global_control.id,
        )
    control = latest_capability_control(session, scope=normalized_scope)
    if control is None:
        return AICapabilityDecision(False, "scope-control-missing", AICapabilityMode.DISABLED)
    if control.mode == AICapabilityMode.DISABLED:
        return AICapabilityDecision(
            False,
            "scope-kill-switch-active",
            AICapabilityMode.DISABLED,
            control_id=control.id,
        )
    if _control_is_expired(control):
        return AICapabilityDecision(
            False,
            "scope-control-expired",
            AICapabilityMode.DISABLED,
            control_id=control.id,
        )
    if control.system_version_id is None or expected_fingerprint is None:
        return AICapabilityDecision(
            False,
            "system-fingerprint-missing",
            AICapabilityMode.DISABLED,
            control_id=control.id,
        )
    system_version = session.get(AISystemVersion, control.system_version_id)
    if system_version is None or system_version.fingerprint != expected_fingerprint:
        return AICapabilityDecision(
            False,
            "system-fingerprint-mismatch",
            AICapabilityMode.DISABLED,
            control_id=control.id,
            system_version_id=control.system_version_id,
        )
    return AICapabilityDecision(
        True,
        "enabled-and-fingerprint-matched",
        control.mode,
        control_id=control.id,
        system_version_id=control.system_version_id,
    )


def _control_is_expired(control: AICapabilityControl) -> bool:
    expires_at = control.expires_at
    if expires_at is None:
        return True
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)
