from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from eastmed_api.alerts import (
    PlannedDelivery,
    _alert_preview_hash,
    daily_cap_throttles,
    percentile_95,
    user_channel_enabled,
    watch_profile_matches,
)
from eastmed_api.contracts import (
    AlertAudienceAccountRead,
    AlertReleaseDraft,
    WatchProfileCreate,
)
from eastmed_pipeline.delivery import (
    CustomerProviders,
    RenderedAlert,
    deliver_one,
    render_alert,
)
from eastmed_schema.enums import (
    AccountTier,
    Corridor,
    DeliveryChannel,
    DeliveryStatus,
    EventStatus,
    EventType,
)
from eastmed_schema.models import Alert, Delivery, Event, EventVersion, User, WatchProfile
from eastmed_shared.alert_rules import load_alert_rules
from eastmed_shared.config import Settings
from pydantic import ValidationError
from sqlalchemy.orm import Session


def make_event(*, severity: int = 3) -> Event:
    return Event(
        id=uuid4(),
        slug="suez-restriction",
        event_type=EventType.NAVIGATION_WARNING,
        corridor=Corridor.RED_SEA_BEM_SUEZ,
        status=EventStatus.DEVELOPING,
        severity=severity,
    )


def make_profile() -> WatchProfile:
    return WatchProfile(
        id=uuid4(),
        account_id=uuid4(),
        name="Suez operations",
        corridors=[Corridor.RED_SEA_BEM_SUEZ.value],
        event_types=[EventType.NAVIGATION_WARNING.value],
        min_severity=2,
        ports=["EGPSD"],
        custom_geo=None,
        active=True,
        configured_by="analyst:one",
        configured_with_customer_at=datetime(2026, 7, 19, tzinfo=UTC),
    )


def test_alert_rules_apply_tier_caps_and_floors() -> None:
    rules = load_alert_rules()

    assert rules.for_tier(AccountTier.WATCH).max_alerts_day == 3
    assert rules.for_tier(AccountTier.WATCH).min_severity == 3
    assert rules.for_tier(AccountTier.DESK).max_alerts_day == 8
    assert rules.severity_4_bypasses_daily_cap is True


def test_watch_profile_matches_all_configured_dimensions() -> None:
    assert watch_profile_matches(
        profile=make_profile(),
        event=make_event(),
        event_ports={"PORT SAID", "EGPSD"},
        geo_intersects=True,
        tier_min_severity=2,
    )


def test_watch_profile_rejects_tier_floor_port_and_geo_misses() -> None:
    profile = make_profile()
    assert not watch_profile_matches(
        profile=profile,
        event=make_event(severity=2),
        event_ports={"EGPSD"},
        geo_intersects=True,
        tier_min_severity=3,
    )
    assert not watch_profile_matches(
        profile=profile,
        event=make_event(),
        event_ports={"GRPIR"},
        geo_intersects=True,
        tier_min_severity=2,
    )
    profile.ports = []
    profile.custom_geo = "POLYGON"
    assert not watch_profile_matches(
        profile=profile,
        event=make_event(),
        event_ports=set(),
        geo_intersects=False,
        tier_min_severity=2,
    )


def test_user_channel_preferences_are_explicit() -> None:
    user = User(
        id=uuid4(),
        account_id=uuid4(),
        email="ops@example.com",
        role="operator",
        channels_json={
            "email": {"enabled": True},
            "telegram": {"enabled": True, "chat_id": "-100123"},
        },
    )

    assert user_channel_enabled(user, DeliveryChannel.EMAIL)
    assert user_channel_enabled(user, DeliveryChannel.TELEGRAM)
    assert not user_channel_enabled(user, DeliveryChannel.WHATSAPP)


def test_alert_release_contract_is_human_and_channel_bounded() -> None:
    with pytest.raises(ValidationError, match="model cannot release"):
        AlertReleaseDraft(
            channels=[DeliveryChannel.EMAIL],
            released_by="model:alert-v1",
        )
    draft = AlertReleaseDraft(
        channels=[DeliveryChannel.WHATSAPP],
        released_by="analyst:one",
    )
    assert draft.channels == [DeliveryChannel.WHATSAPP]


def test_active_watch_profile_requires_completed_customer_onboarding() -> None:
    with pytest.raises(ValidationError, match="active profile requires"):
        WatchProfileCreate(
            account_id=uuid4(),
            name="Unconfigured profile",
            min_severity=2,
            active=True,
        )


def test_nearest_rank_p95_is_deterministic() -> None:
    assert percentile_95([]) is None
    assert percentile_95([1.0, 2.0, 3.0, 100.0]) == 100.0
    assert percentile_95([float(value) for value in range(1, 101)]) == 95.0


def test_daily_tier_cap_throttles_but_severity_four_bypasses() -> None:
    assert daily_cap_throttles(
        alerts_today=3,
        max_alerts_day=3,
        severity=3,
        severity_4_bypasses_daily_cap=True,
    )
    assert not daily_cap_throttles(
        alerts_today=3,
        max_alerts_day=3,
        severity=4,
        severity_4_bypasses_daily_cap=True,
    )
    assert daily_cap_throttles(
        alerts_today=3,
        max_alerts_day=3,
        severity=4,
        severity_4_bypasses_daily_cap=False,
    )


def test_alert_template_escapes_untrusted_text_and_disables_inference() -> None:
    alert = Alert(
        id=uuid4(),
        event_version_id=uuid4(),
        severity=3,
        released_by="analyst:one",
        channels_json=["email"],
        audience_snapshot_json={},
        message_json={
            "severity": 3,
            "title": "Port <closure>",
            "confirmed": "Traffic is suspended.",
            "reported": "",
            "unknown": "Reopening time is unknown.",
            "whats_changed": "",
            "event_slug": "port-closure",
            "note": None,
        },
    )

    rendered = render_alert(alert, public_base_url="https://desk.example")

    assert isinstance(rendered, RenderedAlert)
    assert "Port &lt;closure&gt;" in rendered.html
    assert "Reopening time is unknown." in rendered.text
    assert "https://desk.example/portal/events/port-closure" in rendered.text


def test_telegram_template_is_bounded_and_keeps_full_record_link() -> None:
    alert = Alert(
        id=uuid4(),
        event_version_id=uuid4(),
        severity=3,
        released_by="analyst:one",
        channels_json=["telegram"],
        audience_snapshot_json={},
        message_json={
            "severity": 3,
            "title": "Extended navigation restriction",
            "confirmed": "x" * 10_000,
            "reported": "",
            "unknown": "",
            "whats_changed": "",
            "event_slug": "extended-restriction",
            "note": None,
        },
    )

    rendered = render_alert(alert, public_base_url="https://desk.example")

    assert len(rendered.telegram_text) <= 4096
    assert "Message shortened" in rendered.telegram_text
    assert rendered.telegram_text.endswith(
        "https://desk.example/portal/events/extended-restriction"
    )


def test_alert_preview_hash_binds_note_and_recipient_destination() -> None:
    version = EventVersion(
        id=uuid4(),
        event_id=uuid4(),
        version_no=1,
        title="Restriction",
        summary_confirmed="Confirmed.",
        summary_reported="",
        summary_unknown="",
        whats_changed="",
        sentence_claim_map={},
        event_snapshot_json={},
        claim_snapshot_json=[],
        evidence_snapshot_json=[],
        published_by="analyst:one",
        policy_version="publication-policy-v1",
        model_versions={},
        content_hash="b" * 64,
    )
    account_id = uuid4()
    user_id = uuid4()
    account = AlertAudienceAccountRead(
        account_id=account_id,
        company="BlueWave",
        tier=AccountTier.DESK,
        matched_profile_ids=[uuid4()],
        user_count=1,
        delivery_count=1,
        alerts_today=0,
        daily_cap=8,
        throttled=False,
        reason=None,
    )
    draft = AlertReleaseDraft(
        channels=[DeliveryChannel.EMAIL],
        released_by="analyst:one",
        note=None,
    )

    def preview_hash(*, recipient: str, note: str | None = None) -> str:
        return _alert_preview_hash(
            version=version,
            payload=draft.model_copy(update={"note": note}),
            rules_version="alert-rules-v1",
            accounts=[account],
            deliveries=(
                PlannedDelivery(
                    account_id=account_id,
                    user_id=user_id,
                    channel=DeliveryChannel.EMAIL,
                    recipient=recipient,
                ),
            ),
        )

    assert preview_hash(recipient="ops@example.test") != preview_hash(recipient="new@example.test")
    assert preview_hash(recipient="ops@example.test") != preview_hash(
        recipient="ops@example.test", note="Desk note"
    )


class SuccessfulEmail:
    def send(self, *, recipient: str, subject: str, text: str, html_body: str) -> str:
        assert recipient == "ops@example.com"
        assert subject.startswith("[S3]")
        assert text and html_body
        return "postmark-message-id"


class FailedEmail:
    def send(self, *, recipient: str, subject: str, text: str, html_body: str) -> str:
        del recipient, subject, text, html_body
        raise RuntimeError("provider unavailable")


def delivery_fixture() -> tuple[Delivery, Alert, User]:
    alert = Alert(
        id=uuid4(),
        event_version_id=uuid4(),
        severity=3,
        released_by="analyst:one",
        channels_json=["email"],
        audience_snapshot_json={},
        message_json={
            "severity": 3,
            "title": "Navigation restriction",
            "confirmed": "Traffic is suspended.",
            "reported": "",
            "unknown": "",
            "whats_changed": "",
            "event_slug": "navigation-restriction",
            "note": None,
        },
    )
    user = User(
        id=uuid4(),
        account_id=uuid4(),
        email="ops@example.com",
        role="operator",
        channels_json={"email": {"enabled": True}},
    )
    delivery = Delivery(
        id=uuid4(),
        alert_id=alert.id,
        account_id=user.account_id,
        user_id=user.id,
        channel=DeliveryChannel.EMAIL,
        recipient=user.email,
        status=DeliveryStatus.QUEUED,
        queued_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        attempt_count=0,
    )
    return delivery, alert, user


def test_successful_delivery_records_provider_acceptance_timestamp() -> None:
    delivery, alert, user = delivery_fixture()
    session = MagicMock(spec=Session)
    session.scalar.return_value = delivery
    session.get.return_value = alert
    sent_at = datetime(2026, 7, 19, 12, 0, 20, tzinfo=UTC)
    settings = Settings(outbound_enabled=True, public_base_url="https://desk.example")

    delivered = deliver_one(
        session,
        delivery_id=delivery.id,
        providers=CustomerProviders(email=SuccessfulEmail(), telegram=None),
        settings=settings,
        now=sent_at,
    )

    assert delivered is True
    assert delivery.status == DeliveryStatus.DELIVERED
    assert delivery.sent_at == sent_at
    assert delivery.delivered_at == sent_at
    assert delivery.provider_ref == "postmark-message-id"
    session.commit.assert_called_once()


def test_failed_delivery_persists_exponential_retry_state() -> None:
    delivery, alert, user = delivery_fixture()
    session = MagicMock(spec=Session)
    session.scalar.return_value = delivery
    session.get.return_value = alert
    attempted_at = datetime(2026, 7, 19, 12, 0, 20, tzinfo=UTC)
    settings = Settings(
        outbound_enabled=True,
        public_base_url="https://desk.example",
        delivery_max_attempts=5,
    )

    delivered = deliver_one(
        session,
        delivery_id=delivery.id,
        providers=CustomerProviders(email=FailedEmail(), telegram=None),
        settings=settings,
        now=attempted_at,
    )

    assert delivered is False
    assert delivery.status == DeliveryStatus.QUEUED
    assert delivery.attempt_count == 1
    assert delivery.next_attempt_at == datetime(2026, 7, 19, 12, 1, 20, tzinfo=UTC)
    assert "provider unavailable" in (delivery.last_error or "")
    session.commit.assert_called_once()
