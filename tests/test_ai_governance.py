from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from eastmed_api.ai_governance import (
    CLAIM_EXTRACTION_SCOPE,
    GLOBAL_AI_SCOPE,
    AIGovernanceError,
    bootstrap_first_desk_user,
    capability_decision,
    fingerprint_manifest,
    register_ai_system_version,
    set_capability_control,
)
from eastmed_api.ai_incidents import append_ai_incident_event, create_ai_incident
from eastmed_api.contracts import (
    AICapabilityControlCreate,
    AIIncidentCreate,
    AIIncidentEventCreate,
    AISystemManifest,
)
from eastmed_api.security import (
    DeskPrincipal,
    _decode_desk_oidc_token,
    _oidc_assurance,
    require_desk_principal,
)
from eastmed_pipeline import jobs
from eastmed_schema import Base
from eastmed_schema.enums import AICapabilityMode, AuthAssurance, DeskRole
from eastmed_schema.models import (
    AICapabilityControl,
    AIIncident,
    AIIncidentEvent,
    AISystemVersion,
    AuditLog,
    DeskUser,
)
from eastmed_shared import Settings
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

TABLES = (
    "desk_users",
    "ai_system_versions",
    "ai_capability_controls",
    "ai_incidents",
    "ai_incident_events",
    "audit_log",
)
DESK_TOKEN = "change-me-with-at-least-32-characters"  # noqa: S105 - test default


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in TABLES])
    with Session(engine) as db:
        yield db


def manifest() -> AISystemManifest:
    return AISystemManifest(
        provider="anthropic",
        model_family="claude-sonnet",
        model_snapshot="claude-sonnet-5",
        inference_settings={"temperature": 0, "max_output_tokens": 4096},
        prompt_hash="1" * 64,
        output_schema_hash="2" * 64,
        tokenizer_version="provider-managed:2025-09",
        context_policy_version="claim-extraction-context-v1",
        preprocessing_versions={"normalizer": "v1", "segmenter": "legacy-v1"},
        security_versions={"injection_scanner": "v1"},
        retrieval_versions={"status": "not_applicable"},
        registry_versions={"source_policy": "v1", "gazetteer": "v1"},
        verifier_versions={"status": "not_implemented"},
        adjudication_versions={"claim_state_policy": "v1"},
        composer_versions={"status": "not_applicable"},
        calibration_versions={"shadow_rules": "v1"},
    )


def make_user(session: Session, *, role: DeskRole = DeskRole.ADMINISTRATOR) -> DeskUser:
    user = DeskUser(
        auth_issuer="eastmed-console",
        auth_subject=f"subject-{uuid4()}",
        email=f"{uuid4()}@example.test",
        display_name="Test Administrator",
        role=role,
        active=True,
        created_by="test",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def principal(
    user: DeskUser,
    *,
    role: DeskRole | None = None,
    assurance: AuthAssurance = AuthAssurance.MFA,
) -> DeskPrincipal:
    return DeskPrincipal(
        user_id=user.id,
        issuer=user.auth_issuer,
        subject=user.auth_subject,
        email=user.email,
        display_name=user.display_name,
        role=role or user.role,
        assurance=assurance,
    )


def register_system(session: Session, user: DeskUser) -> AISystemVersion:
    return register_ai_system_version(
        session,
        name="claim-extraction-v1",
        purpose="Evidence-bound shadow extraction of source-record claims.",
        manifest=manifest(),
        principal=principal(user),
    )


def control_expiry(*, days: int = 7) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def test_manifest_fingerprint_is_canonical_and_registration_is_idempotent(
    session: Session,
) -> None:
    user = make_user(session)
    first = register_system(session, user)
    second = register_system(session, user)

    assert first.id == second.id
    assert first.fingerprint == fingerprint_manifest(manifest())
    assert session.scalar(select(func.count()).select_from(AISystemVersion)) == 1
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "ai_system_version.registered")
        )
        == 1
    )


def test_capabilities_fail_closed_until_global_and_scoped_controls_match(
    session: Session,
) -> None:
    user = make_user(session)
    system = register_system(session, user)
    missing = capability_decision(
        session,
        scope=CLAIM_EXTRACTION_SCOPE,
        expected_fingerprint=system.fingerprint,
    )
    assert not missing.allowed
    assert missing.reason == "global-control-missing"

    global_control = set_capability_control(
        session,
        scope=GLOBAL_AI_SCOPE,
        payload=AICapabilityControlCreate(
            mode=AICapabilityMode.SHADOW,
            risk_tier=0,
            reason="Open the global gate for controlled shadow evaluation.",
            expires_at=control_expiry(),
        ),
        principal=principal(user),
    )
    scoped_control = set_capability_control(
        session,
        scope=CLAIM_EXTRACTION_SCOPE,
        payload=AICapabilityControlCreate(
            mode=AICapabilityMode.SHADOW,
            risk_tier=0,
            system_version_id=system.id,
            reason="Run the registered extractor in non-publishing shadow mode.",
            expires_at=control_expiry(),
        ),
        principal=principal(user),
    )

    allowed = capability_decision(
        session,
        scope=CLAIM_EXTRACTION_SCOPE,
        expected_fingerprint=system.fingerprint,
    )
    mismatch = capability_decision(
        session,
        scope=CLAIM_EXTRACTION_SCOPE,
        expected_fingerprint="f" * 64,
    )
    assert allowed.allowed
    assert allowed.mode == AICapabilityMode.SHADOW
    assert allowed.system_version_id == system.id
    assert not mismatch.allowed
    assert mismatch.reason == "system-fingerprint-mismatch"
    assert global_control.revision == 1
    assert scoped_control.revision == 1


def test_kill_switch_is_append_only_and_can_be_used_without_mfa(session: Session) -> None:
    user = make_user(session)
    system = register_system(session, user)
    set_capability_control(
        session,
        scope=GLOBAL_AI_SCOPE,
        payload=AICapabilityControlCreate(
            mode=AICapabilityMode.SHADOW,
            risk_tier=0,
            reason="Open the global gate for controlled shadow evaluation.",
            expires_at=control_expiry(),
        ),
        principal=principal(user),
    )
    first = set_capability_control(
        session,
        scope=CLAIM_EXTRACTION_SCOPE,
        payload=AICapabilityControlCreate(
            mode=AICapabilityMode.SHADOW,
            risk_tier=0,
            system_version_id=system.id,
            reason="Start the registered system in shadow mode only.",
            expires_at=control_expiry(),
        ),
        principal=principal(user),
    )
    disabled = set_capability_control(
        session,
        scope=CLAIM_EXTRACTION_SCOPE,
        payload=AICapabilityControlCreate(
            mode=AICapabilityMode.DISABLED,
            risk_tier=0,
            reason="Emergency stop after a verifier health alarm.",
        ),
        principal=principal(
            user,
            role=DeskRole.ANALYST,
            assurance=AuthAssurance.PASSWORD,
        ),
    )

    decision = capability_decision(
        session,
        scope=CLAIM_EXTRACTION_SCOPE,
        expected_fingerprint=system.fingerprint,
    )
    assert not decision.allowed
    assert decision.reason == "scope-kill-switch-active"
    assert disabled.revision == 2
    assert disabled.previous_control_id == first.id
    assert session.scalar(select(func.count()).select_from(AICapabilityControl)) == 3


def test_enabled_controls_require_short_lived_authorization(session: Session) -> None:
    user = make_user(session)
    with pytest.raises(ValueError, match="require an expiry"):
        AICapabilityControlCreate(
            mode=AICapabilityMode.SHADOW,
            risk_tier=0,
            reason="Attempt to create an indefinite global authorization.",
        )

    with pytest.raises(AIGovernanceError, match="cannot exceed 30 days"):
        set_capability_control(
            session,
            scope=GLOBAL_AI_SCOPE,
            payload=AICapabilityControlCreate(
                mode=AICapabilityMode.SHADOW,
                risk_tier=0,
                reason="Attempt an authorization beyond the maximum revalidation window.",
                expires_at=control_expiry(days=31),
            ),
            principal=principal(user),
        )


def test_capability_decision_fails_closed_after_control_expiry(session: Session) -> None:
    user = make_user(session)
    system = register_system(session, user)
    global_control = set_capability_control(
        session,
        scope=GLOBAL_AI_SCOPE,
        payload=AICapabilityControlCreate(
            mode=AICapabilityMode.SHADOW,
            risk_tier=0,
            reason="Open the global gate for a bounded shadow evaluation.",
            expires_at=control_expiry(),
        ),
        principal=principal(user),
    )
    scoped_control = set_capability_control(
        session,
        scope=CLAIM_EXTRACTION_SCOPE,
        payload=AICapabilityControlCreate(
            mode=AICapabilityMode.SHADOW,
            risk_tier=0,
            system_version_id=system.id,
            reason="Run the registered system for a bounded evaluation window.",
            expires_at=control_expiry(),
        ),
        principal=principal(user),
    )

    scoped_control.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    scoped_expired = capability_decision(
        session,
        scope=CLAIM_EXTRACTION_SCOPE,
        expected_fingerprint=system.fingerprint,
    )
    assert not scoped_expired.allowed
    assert scoped_expired.reason == "scope-control-expired"

    scoped_control.expires_at = control_expiry()
    global_control.expires_at = None
    session.commit()
    migrated_indefinite = capability_decision(
        session,
        scope=CLAIM_EXTRACTION_SCOPE,
        expected_fingerprint=system.fingerprint,
    )
    assert not migrated_indefinite.allowed
    assert migrated_indefinite.reason == "global-control-expired"


def test_automation_remains_locked_until_signed_approval_workflow_exists(
    session: Session,
) -> None:
    user = make_user(session)
    system = register_system(session, user)
    payload = AICapabilityControlCreate(
        mode=AICapabilityMode.AUTOMATED,
        risk_tier=1,
        system_version_id=system.id,
        reason="Enable one evaluated official administrative notice slice.",
        expires_at=control_expiry(),
        approval_refs={
            "product": "approval-product-1",
            "maritime_editorial": "approval-editorial-1",
            "security": "approval-security-1",
            "compliance_legal": "approval-legal-1",
            "engineering": "approval-engineering-1",
        },
    )
    with pytest.raises(AIGovernanceError, match="locked"):
        set_capability_control(
            session,
            scope="official_port_notice.en.html.v1",
            payload=payload,
            principal=principal(user, assurance=AuthAssurance.PHISHING_RESISTANT),
        )


def test_high_severity_ai_incident_atomically_stops_capability(
    session: Session,
) -> None:
    user = make_user(session)
    system = register_system(session, user)
    set_capability_control(
        session,
        scope=CLAIM_EXTRACTION_SCOPE,
        payload=AICapabilityControlCreate(
            mode=AICapabilityMode.SHADOW,
            risk_tier=0,
            system_version_id=system.id,
            reason="Run the registered extractor in controlled shadow evaluation.",
            expires_at=control_expiry(),
        ),
        principal=principal(user),
    )

    incident = create_ai_incident(
        session,
        payload=AIIncidentCreate(
            title="Grounding verifier regression",
            capability_scope=CLAIM_EXTRACTION_SCOPE,
            system_version_id=system.id,
            detected_at=datetime.now(UTC),
            severity="high",
            summary="A canary output omitted a material negation during shadow evaluation.",
            evidence_refs=["run:canary-101"],
        ),
        principal=principal(
            user,
            role=DeskRole.ANALYST,
            assurance=AuthAssurance.PASSWORD,
        ),
    )

    assert incident.status == "contained"
    assert incident.events[0].containment_action is not None
    latest_control = session.scalar(
        select(AICapabilityControl)
        .where(AICapabilityControl.scope == CLAIM_EXTRACTION_SCOPE)
        .order_by(AICapabilityControl.revision.desc())
    )
    assert latest_control is not None
    assert latest_control.mode == AICapabilityMode.DISABLED
    assert session.scalar(select(func.count()).select_from(AIIncident)) == 1
    assert session.scalar(select(func.count()).select_from(AIIncidentEvent)) == 1


def test_ai_incident_resolution_requires_mfa_control_owner(session: Session) -> None:
    user = make_user(session)
    incident = create_ai_incident(
        session,
        payload=AIIncidentCreate(
            title="Unexpected language drift",
            capability_scope=CLAIM_EXTRACTION_SCOPE,
            detected_at=datetime.now(UTC),
            severity="medium",
            summary="The Turkish canary set exceeded its registered edit-distance budget.",
        ),
        principal=principal(user),
    )
    resolution = AIIncidentEventCreate(
        status="resolved",
        severity="medium",
        summary="The affected prompt version was retired and the complete canary set passed.",
        evidence_refs=["evaluation:tr-canary-20260721"],
    )

    with pytest.raises(AIGovernanceError, match="MFA-authenticated"):
        append_ai_incident_event(
            session,
            incident_id=incident.id,
            payload=resolution,
            principal=principal(
                user,
                role=DeskRole.ANALYST,
                assurance=AuthAssurance.PASSWORD,
            ),
        )

    resolved = append_ai_incident_event(
        session,
        incident_id=incident.id,
        payload=resolution,
        principal=principal(user),
    )
    assert resolved.status == "resolved"
    assert len(resolved.events) == 2


def test_bootstrap_is_idempotent_but_cannot_create_a_second_identity(session: Session) -> None:
    user = bootstrap_first_desk_user(
        session,
        auth_issuer="eastmed-console",
        auth_subject="admin-one",
        email="admin@example.test",
        display_name="Admin One",
        role=DeskRole.ADMINISTRATOR,
    )
    repeated = bootstrap_first_desk_user(
        session,
        auth_issuer="eastmed-console",
        auth_subject="admin-one",
        email="admin@example.test",
        display_name="Admin One",
        role=DeskRole.ADMINISTRATOR,
    )
    assert repeated.id == user.id
    with pytest.raises(AIGovernanceError, match="disabled"):
        bootstrap_first_desk_user(
            session,
            auth_issuer="eastmed-console",
            auth_subject="admin-two",
            email="admin2@example.test",
            display_name="Admin Two",
            role=DeskRole.ADMINISTRATOR,
        )


def test_desk_principal_requires_registered_active_identity(session: Session) -> None:
    user = make_user(session)
    resolved = require_desk_principal(
        db=session,
        x_desk_token=DESK_TOKEN,
        x_desk_subject=user.auth_subject,
        x_desk_auth_issuer=user.auth_issuer,
        x_desk_auth_assurance="mfa",
    )
    assert resolved.user_id == user.id
    assert resolved.role == DeskRole.ADMINISTRATOR

    user.active = False
    session.commit()
    with pytest.raises(HTTPException) as exc_info:
        require_desk_principal(
            db=session,
            x_desk_token=DESK_TOKEN,
            x_desk_subject=user.auth_subject,
            x_desk_auth_issuer=user.auth_issuer,
            x_desk_auth_assurance="mfa",
        )
    assert exc_info.value.status_code == 403


def test_oidc_mode_uses_verified_claims_and_ignores_spoofed_identity_headers(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(session)
    user.auth_issuer = "https://identity.example"
    user.auth_subject = "oidc-subject"
    session.commit()
    oidc_settings = Settings(
        desk_auth_mode="oidc",
        desk_oidc_issuer="https://identity.example",
        desk_oidc_audience="eastmed-desk",
        desk_oidc_jwks_url="https://identity.example/.well-known/jwks.json",
        desk_oidc_mfa_acr_values="urn:eastmed:aal2",
    )
    monkeypatch.setattr("eastmed_api.security.get_settings", lambda: oidc_settings)
    monkeypatch.setattr(
        "eastmed_api.security._decode_desk_oidc_token",
        lambda _token: {
            "iss": "https://identity.example",
            "sub": "oidc-subject",
            "acr": "urn:eastmed:aal2",
        },
    )

    resolved = require_desk_principal(
        db=session,
        x_desk_token=DESK_TOKEN,
        x_desk_subject="spoofed-subject",
        x_desk_auth_issuer="spoofed-issuer",
        x_desk_auth_assurance="phishing_resistant",
        authorization="Bearer signed-token",
    )

    assert resolved.user_id == user.id
    assert resolved.subject == "oidc-subject"
    assert resolved.assurance == AuthAssurance.MFA


def test_oidc_decoder_rejects_algorithm_confusion(monkeypatch: pytest.MonkeyPatch) -> None:
    oidc_settings = Settings(
        desk_auth_mode="oidc",
        desk_oidc_issuer="https://identity.example",
        desk_oidc_audience="eastmed-desk",
        desk_oidc_jwks_url="https://identity.example/.well-known/jwks.json",
    )
    monkeypatch.setattr("eastmed_api.security.get_settings", lambda: oidc_settings)
    token = jwt.encode(
        {
            "iss": "https://identity.example",
            "sub": "subject",
            "aud": "eastmed-desk",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        "attacker-controlled-secret-at-least-32-bytes",
        algorithm="HS256",
    )

    with pytest.raises(ValueError, match="unauthorized signing algorithm"):
        _decode_desk_oidc_token(token)


def test_oidc_decoder_verifies_signature_issuer_audience_and_token_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    public_key = private_key.public_key()
    oidc_settings = Settings(
        desk_auth_mode="oidc",
        desk_oidc_issuer="https://identity.example",
        desk_oidc_audience="eastmed-desk",
        desk_oidc_jwks_url="https://identity.example/.well-known/jwks.json",
        desk_oidc_authorized_parties="https://desk.example",
        desk_oidc_max_token_age_seconds=3_600,
    )
    monkeypatch.setattr("eastmed_api.security.get_settings", lambda: oidc_settings)
    monkeypatch.setattr(
        "eastmed_api.security._oidc_jwks_client",
        lambda _url: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public_key)
        ),
    )
    now = datetime.now(UTC)

    def signed_token(*, audience: str, issued_at: datetime) -> str:
        return jwt.encode(
            {
                "iss": "https://identity.example",
                "sub": "subject",
                "aud": audience,
                "azp": "https://desk.example",
                "iat": int(issued_at.timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "test-key", "typ": "JWT"},
        )

    decoded = _decode_desk_oidc_token(
        signed_token(audience="eastmed-desk", issued_at=now)
    )
    assert decoded["sub"] == "subject"

    with pytest.raises(jwt.InvalidAudienceError):
        _decode_desk_oidc_token(signed_token(audience="other-service", issued_at=now))
    with pytest.raises(ValueError, match="too old"):
        _decode_desk_oidc_token(
            signed_token(audience="eastmed-desk", issued_at=now - timedelta(hours=2))
        )


def test_oidc_clerk_factor_age_maps_only_recent_second_factor_to_mfa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(desk_oidc_mfa_max_age_minutes=10)
    monkeypatch.setattr("eastmed_api.security.get_settings", lambda: settings)

    assert _oidc_assurance({"fva": [0, 2]}) == AuthAssurance.MFA
    assert _oidc_assurance({"fva": [0, -1]}) == AuthAssurance.PASSWORD
    assert _oidc_assurance({"fva": [0, 11]}) == AuthAssurance.PASSWORD


def test_claim_extraction_worker_rechecks_governance_before_provider(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_settings = Settings(
        claim_extraction_enabled=True,
        claim_extraction_system_fingerprint="b" * 64,
        anthropic_api_key=SecretStr("test-provider-key"),
    )
    local_sessions = sessionmaker(bind=session.get_bind())
    monkeypatch.setattr(jobs, "SessionLocal", local_sessions)
    monkeypatch.setattr(jobs, "get_settings", lambda: test_settings)

    def provider_must_not_be_built(*_: object, **__: object) -> None:
        raise AssertionError("Provider was constructed before the governance gate")

    monkeypatch.setattr(jobs, "build_claim_extraction_provider", provider_must_not_be_built)
    result = jobs.claim_extraction_job(str(uuid4()))

    assert result == "governance-disabled:global-control-missing"
