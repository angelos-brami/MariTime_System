from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pdfplumber
import pytest
from eastmed_api.contracts import TTVCreate
from eastmed_api.corrections import (
    CorrectionPlan,
    CorrectionWorkflowError,
    build_correction_plan,
    issue_correction,
)
from eastmed_api.quality import QualityWorkflowError, create_ttv_log, quality_scoreboard
from eastmed_api.reports import render_state_knowledge_pdf
from eastmed_schema.enums import (
    CorrectionImpact,
    CorrectionType,
    DeliveryChannel,
    DeliveryStatus,
    EventStatus,
    EventType,
    SourceTier,
)
from eastmed_schema.models import (
    Correction,
    Delivery,
    Event,
    EventVersion,
    Source,
    SourceRecord,
    StateKnowledgeReport,
    TTVLog,
)
from sqlalchemy.orm import Session


def event_and_versions() -> tuple[Event, EventVersion, EventVersion]:
    event = Event(
        id=uuid4(),
        slug="suez-quality-test",
        event_type=EventType.PORT_DISRUPTION,
        corridor="red_sea_bem_suez",
        status=EventStatus.DEVELOPING,
        severity=3,
    )
    now = datetime.now(UTC)
    base = {
        "event_id": event.id,
        "title": "Suez convoy disruption",
        "summary_confirmed": "The authority confirmed a northbound convoy delay.",
        "summary_reported": "Agents report queues above six hours.",
        "summary_unknown": "The recovery time remains unknown.",
        "sentence_claim_map": {},
        "event_snapshot_json": {
            "id": str(event.id),
            "slug": event.slug,
            "status": "developing",
            "corridor": "red_sea_bem_suez",
        },
        "claim_snapshot_json": [],
        "evidence_snapshot_json": [],
        "published_by": "analyst-one",
        "signed_off_by": "senior-analyst",
        "policy_version": "publication-policy-v1",
        "model_versions": {},
    }
    first = EventVersion(
        id=uuid4(),
        version_no=1,
        whats_changed="Initial publication.",
        published_at=now - timedelta(hours=1),
        content_hash="a" * 64,
        **base,
    )
    second = EventVersion(
        id=uuid4(),
        version_no=2,
        whats_changed="The authority corrected the convoy start time.",
        published_at=now,
        content_hash="b" * 64,
        **base,
    )
    return event, first, second


def test_operational_correction_requires_distinct_signoff() -> None:
    event, first, second = event_and_versions()
    session = MagicMock(spec=Session)
    session.get.side_effect = [event, first, second]

    with pytest.raises(CorrectionWorkflowError, match="distinct human"):
        build_correction_plan(
            session,
            event_id=event.id,
            version_from_id=first.id,
            version_to_id=second.id,
            correction_type=CorrectionType.CORRECTION,
            impact=CorrectionImpact.OPERATIONALLY_RELEVANT,
            note="The original convoy time was wrong.",
            root_cause="The desk transposed the source timezone.",
            corrective_action="A second analyst now checks all timezone conversions.",
            detected_at=datetime.now(UTC),
            drafted_by="analyst-one",
            signed_off_by="analyst-one",
        )


def test_issue_service_requires_bound_approval_for_operational_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event, first, second = event_and_versions()
    preview_hash = "f" * 64
    monkeypatch.setattr(
        "eastmed_api.corrections.build_correction_plan",
        lambda *_args, **_kwargs: CorrectionPlan(
            event=event,
            version_from=first,
            version_to=second,
            recipients=(),
            channels=(),
            preview_hash=preview_hash,
            message={},
        ),
    )

    with pytest.raises(CorrectionWorkflowError, match="content-bound approval record"):
        issue_correction(
            MagicMock(spec=Session),
            preview_hash=preview_hash,
            event_id=event.id,
            version_from_id=first.id,
            version_to_id=second.id,
            correction_type=CorrectionType.CORRECTION,
            impact=CorrectionImpact.OPERATIONALLY_RELEVANT,
            note="The original convoy time was wrong.",
            root_cause="The desk transposed the source timezone.",
            corrective_action="A second analyst now checks all timezone conversions.",
            detected_at=datetime.now(UTC),
            drafted_by="desk:00000000-0000-0000-0000-000000000001",
            signed_off_by="desk:00000000-0000-0000-0000-000000000002",
        )


def test_correction_preview_pins_exact_original_recipients_and_hashes() -> None:
    event, first, second = event_and_versions()
    delivery = Delivery(
        id=uuid4(),
        alert_id=uuid4(),
        account_id=uuid4(),
        user_id=uuid4(),
        channel=DeliveryChannel.EMAIL,
        recipient="ops@example.test",
        status=DeliveryStatus.DELIVERED,
        queued_at=datetime.now(UTC),
        attempt_count=1,
    )
    session = MagicMock(spec=Session)
    session.get.side_effect = [event, first, second]
    session.execute.return_value.scalars.return_value = [delivery]
    kwargs: dict[str, Any] = {
        "event_id": event.id,
        "version_from_id": first.id,
        "version_to_id": second.id,
        "correction_type": CorrectionType.CORRECTION,
        "impact": CorrectionImpact.OPERATIONALLY_RELEVANT,
        "note": "The original convoy time was wrong.",
        "root_cause": "The desk transposed the source timezone.",
        "corrective_action": "A second analyst now checks all timezone conversions.",
        "detected_at": datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
        "drafted_by": "analyst-one",
        "signed_off_by": "analyst-two",
    }

    first_plan = build_correction_plan(session, **kwargs)
    session.get.side_effect = [event, first, second]
    second_plan = build_correction_plan(session, **kwargs)

    assert first_plan.preview_hash == second_plan.preview_hash
    assert first_plan.message["affected_version"]["content_hash"] == "a" * 64
    assert first_plan.message["corrected_version"]["content_hash"] == "b" * 64
    assert first_plan.channels == (DeliveryChannel.EMAIL,)
    assert first_plan.recipients[0].recipient == "ops@example.test"


def test_ttv_rejects_uncorroborated_tier_e_source() -> None:
    event, _, _ = event_and_versions()
    record = MagicMock(spec=SourceRecord)
    source = MagicMock(spec=Source)
    source.tier = SourceTier.E
    session = MagicMock(spec=Session)
    session.get.return_value = event
    session.execute.return_value.one_or_none.return_value = (record, source)
    payload = TTVCreate(
        event_id=event.id,
        first_credible_signal_at=datetime.now(UTC) - timedelta(minutes=5),
        signal_source_record_id=uuid4(),
        recorded_by="desk-analyst",
    )

    with pytest.raises(QualityWorkflowError, match="explicit corroboration"):
        create_ttv_log(session, payload)


def test_scoreboard_uses_honest_denominators_for_missing_milestones() -> None:
    event, first, second = event_and_versions()
    detected = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
    correction = Correction(
        id=uuid4(),
        event_id=event.id,
        version_from_id=first.id,
        version_to_id=second.id,
        version_from=1,
        version_to=2,
        correction_type=CorrectionType.CORRECTION,
        note="Corrected the convoy time.",
        propagated_channels_json={},
        detected_at=detected,
        issued_at=detected + timedelta(minutes=30),
        issued_by="analyst-two",
    )
    complete = TTVLog(
        id=uuid4(),
        event_id=event.id,
        first_credible_signal_at=detected,
        holding_line_at=detected + timedelta(minutes=10),
        verified_update_at=detected + timedelta(minutes=40),
        coverage_window=True,
    )
    incomplete = TTVLog(
        id=uuid4(),
        event_id=uuid4(),
        first_credible_signal_at=detected,
        holding_line_at=None,
        verified_update_at=None,
        coverage_window=True,
    )
    session = MagicMock(spec=Session)
    session.scalar.return_value = 2
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [correction]),
        SimpleNamespace(all=lambda: [event]),
        SimpleNamespace(all=lambda: [first, second]),
        SimpleNamespace(all=lambda: [complete, incomplete]),
    ]

    result = quality_scoreboard(session)

    assert result.correction_rate_percent == 50.0
    assert result.corrections_within_60_minutes_percent == 100.0
    assert result.holding_line_median_minutes == 10.0
    assert result.holding_line_within_15_minutes_percent == 50.0
    assert result.verified_update_within_45_minutes_percent == 50.0


def test_state_knowledge_pdf_contains_hashes_and_disclaimer() -> None:
    event, _, version = event_and_versions()
    report = StateKnowledgeReport(
        id=uuid4(),
        event_id=event.id,
        event_version_id=version.id,
        requested_timestamp=version.published_at,
        requested_by="desk-analyst",
        generated_at=datetime.now(UTC),
        version_content_hash=version.content_hash,
        content_hash="c" * 64,
        snapshot_json={
            "event": version.event_snapshot_json,
            "requested_timestamp": version.published_at.isoformat(),
            "version": {
                "id": str(version.id),
                "number": version.version_no,
                "title": version.title,
                "published_at": version.published_at.isoformat(),
                "content_hash": version.content_hash,
            },
            "sections": {
                "confirmed": version.summary_confirmed,
                "reported": version.summary_reported,
                "unknown": version.summary_unknown,
                "changed": version.whats_changed,
            },
            "claims": [],
            "evidence": [],
            "sentence_claim_map": {},
        },
    )

    pdf = render_state_knowledge_pdf(report)

    assert pdf.startswith(b"%PDF-")
    with pdfplumber.open(BytesIO(pdf)) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)
    assert "STATE OF KNOWLEDGE" in text
    assert version.content_hash in text
    assert "information support" in text
