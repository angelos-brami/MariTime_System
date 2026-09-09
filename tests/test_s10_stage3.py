from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
import respx
from eastmed_api.calendar import CalendarWorkflowError, publish_calendar_event
from eastmed_api.contracts import CalendarEventPublishCreate, CustomerChannelsUpdate
from eastmed_api.main import app
from eastmed_api.security import account_api_key_hash, require_data_api_key, require_data_scope
from eastmed_api.whatsapp import _apply_status, _status_events
from eastmed_pipeline.ais_cache import (
    cache_observation,
    parse_aisstream_message,
    subscription_payload,
)
from eastmed_pipeline.delivery import (
    CustomerProviders,
    Dialog360WhatsAppProvider,
    deliver_one,
)
from eastmed_schema.enums import (
    AccountTier,
    CalendarEventType,
    Corridor,
    DeliveryChannel,
    DeliveryStatus,
    SourceTier,
)
from eastmed_schema.models import (
    Account,
    AccountApiKey,
    AISPositionCache,
    Alert,
    Delivery,
    Source,
    SourceRecord,
)
from eastmed_shared import Settings
from eastmed_shared.postgres_cli import postgres_environment
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr, ValidationError
from sqlalchemy.orm import Session

from scripts.run_restore_drill import pg_restore_invocation, safe_target


def ais_message(*, latitude: float = 37.72, longitude: float = 23.54) -> dict[str, object]:
    return {
        "MessageType": "PositionReport",
        "MetaData": {
            "MMSI": 241000001,
            "ShipName": " AEGEAN TEST ",
            "time_utc": "2026-07-20T10:00:00+00:00",
        },
        "Message": {
            "PositionReport": {
                "UserID": 241000001,
                "Latitude": latitude,
                "Longitude": longitude,
                "Cog": 181.2,
                "Sog": 12.4,
                "NavigationalStatus": 0,
            }
        },
    }


def test_ais_subscription_is_corridor_filtered_and_server_authenticated() -> None:
    payload = subscription_payload(SecretStr("ais-secret"))

    assert payload["APIKey"] == "ais-secret"
    assert len(payload["BoundingBoxes"]) == 4  # type: ignore[arg-type]
    assert "PositionReport" in payload["FilterMessageTypes"]  # type: ignore[operator]


def test_ais_parser_accepts_valid_position_and_rejects_outside_corridors() -> None:
    received = datetime(2026, 7, 20, 10, 1, tzinfo=UTC)
    observation = parse_aisstream_message(ais_message(), received_at=received)

    assert observation is not None
    assert observation.corridor == Corridor.EAST_MED
    assert observation.mmsi == "241000001"
    assert observation.vessel_name == "AEGEAN TEST"
    assert len(observation.payload_hash) == 64
    assert (
        parse_aisstream_message(ais_message(latitude=0, longitude=0), received_at=received) is None
    )


def test_ais_parser_normalizes_mmsi_and_drops_non_finite_or_reserved_motion() -> None:
    received = datetime(2026, 7, 20, 10, 1, tzinfo=UTC)
    payload = ais_message()
    message = payload["Message"]
    assert isinstance(message, dict)
    body = message["PositionReport"]
    assert isinstance(body, dict)
    body["UserID"] = 1234567
    body["Cog"] = float("nan")
    body["Sog"] = 102.3

    observation = parse_aisstream_message(payload, received_at=received)

    assert observation is not None
    assert observation.mmsi == "001234567"
    assert observation.course is None
    assert observation.speed is None


def test_ais_cache_does_not_regress_to_an_older_position() -> None:
    received = datetime(2026, 7, 20, 10, 1, tzinfo=UTC)
    observation = parse_aisstream_message(ais_message(), received_at=received)
    assert observation is not None
    existing = AISPositionCache(
        id=uuid4(),
        mmsi=observation.mmsi,
        latitude=38.0,
        longitude=24.0,
        corridor=Corridor.EAST_MED,
        message_at=observation.message_at + timedelta(minutes=1),
        received_at=received,
        source="aisstream.io",
        payload_hash="f" * 64,
    )
    session = MagicMock(spec=Session)
    session.scalar.return_value = existing

    cache_observation(session, observation)

    assert existing.latitude == 38.0
    assert existing.payload_hash == "f" * 64


@respx.mock
def test_360dialog_provider_uses_template_endpoint_and_returns_wamid() -> None:
    route = respx.post("https://waba-v2.360dialog.io/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.123"}]})
    )
    provider = Dialog360WhatsAppProvider(
        api_key="dialog-secret",
        base_url="https://waba-v2.360dialog.io",
        language="en",
    )

    result = provider.send_template(
        recipient="306900000000",
        template_name="eastmed_alert_v1",
        parameters=["3", "Test alert"],
    )

    assert result == "wamid.123"
    request = route.calls.last.request
    assert request.headers["D360-API-KEY"] == "dialog-secret"
    body = json.loads(request.content)
    assert body["type"] == "template"
    assert body["template"]["language"]["policy"] == "deterministic"


def test_whatsapp_provider_acceptance_is_sent_not_delivered_and_not_retried() -> None:
    now = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
    alert = Alert(
        id=uuid4(),
        event_version_id=uuid4(),
        severity=3,
        created_at=now,
        released_by="analyst-one",
        channels_json=["whatsapp"],
        audience_snapshot_json={},
        message_json={
            "severity": 3,
            "title": "Test alert",
            "confirmed": "A closure is confirmed.",
            "reported": "",
            "unknown": "Reopening time.",
            "whats_changed": "Initial alert.",
            "event_slug": "test-alert",
        },
    )
    delivery = Delivery(
        id=uuid4(),
        alert_id=alert.id,
        account_id=uuid4(),
        user_id=uuid4(),
        channel=DeliveryChannel.WHATSAPP,
        recipient="306900000000",
        status=DeliveryStatus.QUEUED,
        queued_at=now,
        attempt_count=0,
    )
    provider = MagicMock()
    provider.send_template.return_value = "wamid.123"
    session = MagicMock(spec=Session)
    session.scalar.return_value = delivery
    session.get.return_value = alert
    settings = Settings(outbound_enabled=True, public_base_url="https://example.test")

    assert deliver_one(
        session,
        delivery_id=delivery.id,
        providers=CustomerProviders(email=None, telegram=None, whatsapp=provider),
        settings=settings,
        now=now,
    )
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.delivered_at is None
    assert delivery.provider_ref == "wamid.123"

    assert not deliver_one(
        session,
        delivery_id=delivery.id,
        providers=CustomerProviders(email=None, telegram=None, whatsapp=provider),
        settings=settings,
        now=now + timedelta(seconds=5),
    )
    assert provider.send_template.call_count == 1


def test_whatsapp_webhook_sanitizes_and_applies_delivery_receipt() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.123",
                                    "status": "delivered",
                                    "timestamp": "1784541660",
                                    "recipient_id": "306900000000",
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    events = _status_events(payload)
    assert events == [
        {
            "provider_ref": "wamid.123",
            "status": "delivered",
            "timestamp": "1784541660",
            "errors": [],
        }
    ]
    now = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
    delivery = Delivery(
        id=uuid4(),
        alert_id=uuid4(),
        account_id=uuid4(),
        user_id=uuid4(),
        channel=DeliveryChannel.WHATSAPP,
        recipient="306900000000",
        status=DeliveryStatus.SENT,
        queued_at=now,
        sent_at=now,
        attempt_count=1,
    )
    assert _apply_status(
        delivery,
        provider_status="delivered",
        event_at=now + timedelta(seconds=2),
        received_at=now + timedelta(seconds=3),
        errors=[],
    )
    assert delivery.status == DeliveryStatus.DELIVERED
    assert delivery.delivered_at == now + timedelta(seconds=2)


def test_whatsapp_channel_requires_explicit_opt_in() -> None:
    with pytest.raises(ValidationError, match="confirmed customer opt-in"):
        CustomerChannelsUpdate(
            updated_by="desk-analyst",
            whatsapp_enabled=True,
            whatsapp_phone="+30 690 000 0000",
            whatsapp_opt_in_confirmed=False,
        )


def test_data_api_key_is_hmac_verified_and_scope_enforced() -> None:
    settings = Settings(api_key_pepper=SecretStr("test-pepper"))
    token = f"em_live_a1b2c3d4e5.{uuid4().hex}"
    with patch("eastmed_api.security.get_settings", return_value=settings):
        digest = account_api_key_hash(token)
        api_key = AccountApiKey(
            id=uuid4(),
            account_id=uuid4(),
            name="Pilot",
            key_prefix="a1b2c3d4e5",
            secret_hash=digest,
            scopes_json=["events:read"],
            created_at=datetime.now(UTC),
        )
        account = Account(
            id=api_key.account_id,
            company="Data Pilot",
            tier=AccountTier.DATA,
            contract_start=date.today() - timedelta(days=1),
            contract_end=date.today() + timedelta(days=30),
        )
        session = MagicMock(spec=Session)
        session.execute.return_value.all.return_value = [(api_key, account)]
        principal = require_data_api_key(
            session,
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
        )

    assert principal.account_id == account.id
    assert principal.scopes == frozenset({"events:read"})
    with pytest.raises(HTTPException) as error:
        require_data_scope(principal, "claims:read")
    assert error.value.status_code == 403


def test_calendar_rejects_tier_e_as_publication_evidence() -> None:
    source = MagicMock(spec=Source)
    source.tier = SourceTier.E
    record = MagicMock(spec=SourceRecord)
    session = MagicMock(spec=Session)
    session.execute.return_value.all.return_value = [(record, source)]
    payload = CalendarEventPublishCreate(
        slug="test-port-closure",
        event_type=CalendarEventType.PORT_CLOSURE,
        title="Test port closure",
        corridor=Corridor.PORT_SPECIFIC,
        starts_at=datetime.now(UTC) + timedelta(days=1),
        status="announced",
        public_note="A test closure is announced.",
        source_record_ids=[uuid4()],
        published_by="desk-analyst",
    )

    with pytest.raises(CalendarWorkflowError, match="Tier E"):
        publish_calendar_event(session, payload)


def test_calendar_requires_an_active_rights_approved_source() -> None:
    source = MagicMock(spec=Source)
    source.tier = SourceTier.A
    source.name = "Unapproved source"
    source.active = True
    source.automation_approved_at = None
    source.rights_reviewed_by = None
    record = MagicMock(spec=SourceRecord)
    session = MagicMock(spec=Session)
    session.execute.return_value.all.return_value = [(record, source)]
    payload = CalendarEventPublishCreate(
        slug="unapproved-port-closure",
        event_type=CalendarEventType.PORT_CLOSURE,
        title="Unapproved port closure",
        corridor=Corridor.PORT_SPECIFIC,
        starts_at=datetime.now(UTC) + timedelta(days=1),
        status="announced",
        public_note="A closure is announced.",
        source_record_ids=[uuid4()],
        published_by="desk-analyst",
    )

    with pytest.raises(CalendarWorkflowError, match="not been approved"):
        publish_calendar_event(session, payload)


def test_openapi_exposes_scoped_read_api_and_bearer_scheme() -> None:
    schema = app.openapi()
    assert "/api/v1/data/events" in schema["paths"]
    assert "/api/v1/data/calendar" in schema["paths"]
    assert "/api/v1/data/ais" in schema["paths"]
    scheme = schema["components"]["securitySchemes"]["EastMedAccountApiKey"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"


def test_backup_database_url_becomes_secret_safe_pg_environment() -> None:
    environment = postgres_environment(
        "postgresql+psycopg://backup%40user:secret%2Fvalue@db.internal:5433/eastmed?sslmode=require"
    )

    assert environment == {
        "PGHOST": "db.internal",
        "PGPORT": "5433",
        "PGDATABASE": "eastmed",
        "PGUSER": "backup@user",
        "PGPASSWORD": "secret/value",
        "PGSSLMODE": "require",
    }


def test_restore_keeps_database_credentials_out_of_process_arguments(tmp_path: Path) -> None:
    backup = tmp_path / "eastmed.dump"
    command, environment = pg_restore_invocation(
        "pg_restore",
        "postgresql://restore_user:restore_secret@db.internal:5433/eastmed_restore?sslmode=require",
        backup,
    )

    assert command[-3:] == ["--dbname", "eastmed_restore", str(backup.resolve())]
    assert "restore_secret" not in " ".join(command)
    assert "postgresql://" not in " ".join(command)
    assert environment["PGPASSWORD"] == "restore_secret"


def test_restore_target_guard_rejects_application_or_ambiguous_database() -> None:
    application = "postgresql://app:secret@db.internal/eastmed"

    with pytest.raises(ValueError, match="must contain 'restore' or 'drill'"):
        safe_target("postgresql://operator:secret@db.internal/scratch", application)
    with pytest.raises(ValueError, match="must not be the configured application database"):
        safe_target(
            "postgresql://operator:other@db.internal/eastmed_restore",
            "postgresql://app:secret@db.internal/eastmed_restore",
        )

    assert safe_target(
        "postgresql://operator:secret@db.internal/eastmed_restore", application
    ).endswith("/eastmed_restore")
