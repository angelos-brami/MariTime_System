from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from eastmed_api.contracts import TriageActionCreate
from eastmed_pipeline.triage import (
    TriageActionInput,
    TriageWorkflowError,
    apply_triage_action,
)
from eastmed_schema.enums import TriageAction, TriageStatus
from eastmed_schema.models import AuditLog, SourceRecord, TriageDecision, TriageItem
from eastmed_shared.gazetteer import detect_gazetteer_signals
from pydantic import ValidationError
from sqlalchemy.orm import Session


def test_gazetteer_detects_greek_port_corridor_and_weather() -> None:
    signals = detect_gazetteer_signals(
        "Θυελλώδεις άνεμοι στον Πειραιά",
        "Η Ανατολική Μεσόγειος επηρεάζεται από καταιγίδα.",
    )

    assert {corridor.value for corridor in signals.corridors} == {
        "east_med",
        "port_specific",
    }
    assert signals.ports == (
        {
            "name": "Piraeus",
            "unlocode": "GRPIR",
            "corridor": "east_med",
            "matched_alias": "Πειραιά",
        },
    )
    assert [event_type.value for event_type in signals.event_types] == ["weather_hazard"]


def test_gazetteer_uses_word_boundaries() -> None:
    signals = detect_gazetteer_signals(None, "The hormones report covers a portside office.")

    assert signals.corridors == ()
    assert signals.ports == ()


def test_triage_action_contract_enforces_human_review_requirements() -> None:
    with pytest.raises(ValidationError, match="dismiss requires a reason"):
        TriageActionCreate(action=TriageAction.DISMISS, reviewer="analyst")
    with pytest.raises(ValidationError, match="model cannot"):
        TriageActionCreate(
            action=TriageAction.ATTACH,
            reviewer="model:triage-v1",
            event_id=uuid4(),
        )


def test_dismiss_writes_immutable_decision_and_audit_log() -> None:
    item = TriageItem(
        id=uuid4(),
        source_record_id=uuid4(),
        status=TriageStatus.PENDING,
        detected_corridors=[],
        detected_ports=[],
        suggested_event_types=[],
    )
    session = MagicMock(spec=Session)
    record = SourceRecord(
        id=item.source_record_id,
        source_id=uuid4(),
        url="https://example.test/item",
        canonical_url="https://example.test/item",
        content_hash="a" * 64,
        extracted_text="Advisory",
        parser_version="test-v1",
        security_scan={},
    )
    session.get.side_effect = lambda model, *args, **kwargs: item if model is TriageItem else record
    reviewed_at = datetime(2026, 7, 19, 18, 0, tzinfo=UTC)

    result = apply_triage_action(
        session,
        item_id=item.id,
        request=TriageActionInput(
            action=TriageAction.DISMISS,
            reviewer="desk-analyst",
            reason="Duplicate advisory",
        ),
        now=reviewed_at,
    )

    added = [call.args[0] for call in session.add.call_args_list]
    assert item.status == TriageStatus.DISMISSED
    assert item.reviewed_by == "desk-analyst"
    assert item.dismissal_reason == "Duplicate advisory"
    assert result.decision.at == reviewed_at
    assert any(isinstance(value, TriageDecision) for value in added)
    assert any(isinstance(value, AuditLog) for value in added)
    session.commit.assert_called_once()


@pytest.mark.parametrize(
    "security_scan",
    [{"quarantined": True}, {"injection_suspected": True}],
)
def test_quarantined_record_cannot_be_attached_to_an_event(
    security_scan: dict[str, bool],
) -> None:
    item = TriageItem(
        id=uuid4(),
        source_record_id=uuid4(),
        status=TriageStatus.PENDING,
        detected_corridors=[],
        detected_ports=[],
        suggested_event_types=[],
    )
    record = SourceRecord(
        id=item.source_record_id,
        source_id=uuid4(),
        url="postmark://quarantine",
        canonical_url="postmark://quarantine",
        content_hash="c" * 64,
        extracted_text="",
        parser_version="test-v1",
        security_scan=security_scan,
    )
    session = MagicMock(spec=Session)
    session.get.side_effect = lambda model, *args, **kwargs: item if model is TriageItem else record

    with pytest.raises(TriageWorkflowError, match="Quarantined"):
        apply_triage_action(
            session,
            item_id=item.id,
            request=TriageActionInput(
                action=TriageAction.ATTACH,
                reviewer="desk-analyst",
                event_id=uuid4(),
            ),
        )
