from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from eastmed_api.operations_dashboard import operations_dashboard
from eastmed_schema.enums import (
    AccessMethod,
    AICapabilityMode,
    AuthAssurance,
    RightsBasis,
    SourceTier,
    SourceType,
)
from eastmed_schema.models import AICapabilityControl, DeskAlert, Source
from eastmed_shared import Settings
from sqlalchemy.orm import Session


def source(name: str) -> Source:
    return Source(
        id=uuid4(),
        name=name,
        legal_entity="Authority",
        source_type=SourceType.OFFICIAL,
        tier=SourceTier.A,
        language="en",
        country="GR",
        access_method=AccessMethod.RSS,
        rights_basis=RightsBasis.PUBLIC_ADVISORY,
        last_reviewed_at=datetime.now(UTC),
        active=True,
    )


def global_control() -> AICapabilityControl:
    return AICapabilityControl(
        id=uuid4(),
        scope="all_model_calls",
        revision=1,
        mode=AICapabilityMode.SHADOW,
        risk_tier=0,
        system_version_id=None,
        reason="Controlled launch shadow evaluation.",
        approval_refs_json={},
        changed_by_user_id=uuid4(),
        auth_assurance=AuthAssurance.MFA,
        previous_control_id=None,
        expires_at=datetime.now(UTC) + timedelta(hours=8),
        changed_at=datetime.now(UTC),
    )


def test_launch_dashboard_reports_ready_only_when_all_code_gates_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    session.scalars.return_value.all.return_value = [source("Alpha"), source("Bravo")]
    monkeypatch.setattr("eastmed_api.operations_dashboard._count", lambda *_args: 0)
    monkeypatch.setattr(
        "eastmed_api.operations_dashboard.count_open_ai_incidents", lambda _session: 0
    )
    monkeypatch.setattr(
        "eastmed_api.operations_dashboard.list_latest_capability_controls",
        lambda _session: [global_control()],
    )
    monkeypatch.setattr(
        "eastmed_api.operations_dashboard.poller_health",
        lambda _source, now: SimpleNamespace(state="healthy"),
    )
    monkeypatch.setattr(
        "eastmed_api.operations_dashboard.get_settings",
        lambda: Settings(
            environment="staging",
            desk_auth_mode="oidc",
            desk_oidc_issuer="https://identity.example",
            desk_oidc_audience="eastmed",
            desk_oidc_jwks_url="https://identity.example/jwks",
            outbound_enabled=True,
            launch_min_active_sources=2,
        ),
    )

    result = operations_dashboard(session)

    assert result.overall_status == "ready"
    assert result.open_ai_incidents == 0
    assert all(check.state == "pass" for check in result.readiness)


def test_launch_dashboard_fails_on_unhealthy_source_or_open_ai_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    session.scalars.return_value.all.return_value = [source("Alpha")]

    def count(_session: Session, model: type[object], *_conditions: object) -> int:
        return 1 if model is DeskAlert else 0

    monkeypatch.setattr("eastmed_api.operations_dashboard._count", count)
    monkeypatch.setattr(
        "eastmed_api.operations_dashboard.count_open_ai_incidents", lambda _session: 1
    )
    monkeypatch.setattr(
        "eastmed_api.operations_dashboard.list_latest_capability_controls",
        lambda _session: [global_control()],
    )
    monkeypatch.setattr(
        "eastmed_api.operations_dashboard.poller_health",
        lambda _source, now: SimpleNamespace(state="overdue"),
    )
    monkeypatch.setattr(
        "eastmed_api.operations_dashboard.get_settings",
        lambda: Settings(
            environment="staging",
            desk_auth_mode="oidc",
            desk_oidc_issuer="https://identity.example",
            desk_oidc_audience="eastmed",
            desk_oidc_jwks_url="https://identity.example/jwks",
            outbound_enabled=True,
            launch_min_active_sources=1,
        ),
    )

    result = operations_dashboard(session)

    assert result.overall_status == "action_required"
    failed = {check.key for check in result.readiness if check.state == "fail"}
    assert {"source_health", "ai_incidents", "desk_alerts"} <= failed
