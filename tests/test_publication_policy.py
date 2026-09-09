from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from eastmed_api.contracts import EventVersionCreate, PublicationSentence
from eastmed_api.publication import (
    ClaimPolicyView,
    PublicationContext,
    PublicationPolicyError,
    _draft_artifacts,
    publish_event_version,
    validate_publication,
)
from eastmed_api.reliability import reliability_receipt_for_version
from eastmed_schema.enums import ClaimState, Corridor, EventStatus, EventType
from eastmed_schema.models import Claim, Event, EventVersion
from sqlalchemy.orm import Session


def make_event(*, severity: int = 2) -> Event:
    return Event(
        id=uuid4(),
        slug="test-event",
        event_type=EventType.SECURITY_INCIDENT,
        corridor=Corridor.HORMUZ_GULF,
        status=EventStatus.DEVELOPING,
        severity=severity,
    )


def make_claim(
    event_id: UUID,
    *,
    state: ClaimState = ClaimState.CONFIRMED,
    proposed_by: str = "analyst:one",
    reviewed_by: str | None = "analyst:one",
    second_reviewed_by: str | None = None,
    second_review_valid: bool | None = None,
    sensitivity_flags: tuple[str, ...] = (),
    evidence_count: int = 1,
) -> ClaimPolicyView:
    return ClaimPolicyView(
        id=uuid4(),
        event_id=event_id,
        state=state,
        proposed_by=proposed_by,
        reviewed_by=reviewed_by,
        second_reviewed_by=second_reviewed_by,
        sensitivity_flags=sensitivity_flags,
        evidence_count=evidence_count,
        second_review_valid=(
            bool(second_reviewed_by) if second_review_valid is None else second_review_valid
        ),
    )


def make_payload(claim: ClaimPolicyView, *, signed_off_by: str | None = None) -> EventVersionCreate:
    return EventVersionCreate(
        title="Merchant vessel incident",
        sentences=[
            PublicationSentence(
                section="confirmed", text="An incident occurred.", claim_ids=[claim.id]
            )
        ],
        evidence_ids=[],
        published_by="analyst:one",
        signed_off_by=signed_off_by,
        preview_hash="0" * 64,
    )


def test_valid_human_reviewed_claim_passes() -> None:
    event = make_event()
    claim = make_claim(event.id)
    validate_publication(event=event, payload=make_payload(claim), claims=[claim])


def test_high_severity_publication_requires_human_signoff() -> None:
    event = make_event(severity=4)
    claim = make_claim(event.id)
    with pytest.raises(PublicationPolicyError, match="require human sign-off"):
        validate_publication(event=event, payload=make_payload(claim), claims=[claim])
    validate_publication(
        event=event,
        payload=make_payload(claim, signed_off_by="analyst:two"),
        claims=[claim],
    )


def test_model_cannot_sign_off() -> None:
    event = make_event(severity=3)
    claim = make_claim(event.id)
    with pytest.raises(PublicationPolicyError, match="model cannot sign off"):
        validate_publication(
            event=event,
            payload=make_payload(claim, signed_off_by="model:frontier"),
            claims=[claim],
        )


def test_publisher_cannot_supply_their_own_second_signoff() -> None:
    event = make_event(severity=3)
    claim = make_claim(event.id)

    with pytest.raises(PublicationPolicyError, match="distinct human"):
        validate_publication(
            event=event,
            payload=make_payload(claim, signed_off_by="analyst:one"),
            claims=[claim],
        )


def test_model_proposed_claim_requires_human_review() -> None:
    event = make_event()
    claim = make_claim(event.id, proposed_by="model:extract:v1", reviewed_by=None)
    with pytest.raises(PublicationPolicyError, match="lacks human review"):
        validate_publication(event=event, payload=make_payload(claim), claims=[claim])


def test_sensitive_claim_requires_second_analyst() -> None:
    event = make_event()
    claim = make_claim(event.id, sensitivity_flags=("casualties",))
    with pytest.raises(PublicationPolicyError, match="second analyst review"):
        validate_publication(event=event, payload=make_payload(claim), claims=[claim])


def test_sensitive_claim_requires_a_human_distinct_second_analyst() -> None:
    event = make_event()
    same = make_claim(
        event.id,
        sensitivity_flags=("casualties",),
        second_reviewed_by="analyst:one",
    )
    with pytest.raises(PublicationPolicyError, match="distinct second analyst"):
        validate_publication(event=event, payload=make_payload(same), claims=[same])

    model = make_claim(
        event.id,
        sensitivity_flags=("casualties",),
        second_reviewed_by="model:review-v1",
    )
    with pytest.raises(PublicationPolicyError, match="non-human second reviewer"):
        validate_publication(event=event, payload=make_payload(model), claims=[model])


def test_sensitive_claim_requires_content_bound_second_review_record() -> None:
    event = make_event()
    claim = make_claim(
        event.id,
        sensitivity_flags=("casualties",),
        second_reviewed_by="desk:00000000-0000-0000-0000-000000000002",
        second_review_valid=False,
    )

    with pytest.raises(PublicationPolicyError, match="content-bound"):
        validate_publication(event=event, payload=make_payload(claim), claims=[claim])


def test_publication_sentence_rejects_embedded_line_breaks() -> None:
    with pytest.raises(ValueError, match="cannot contain line breaks"):
        PublicationSentence(
            section="reported",
            text="First line\nSecond line",
            claim_ids=[uuid4()],
        )


def test_confirmed_claim_requires_evidence() -> None:
    event = make_event()
    claim = make_claim(event.id, evidence_count=0)
    with pytest.raises(PublicationPolicyError, match="has no safe selected evidence"):
        validate_publication(event=event, payload=make_payload(claim), claims=[claim])


def test_claim_state_must_match_public_section() -> None:
    event = make_event()
    claim = make_claim(event.id, state=ClaimState.UNVERIFIED)
    with pytest.raises(PublicationPolicyError, match="conflicts with section"):
        validate_publication(event=event, payload=make_payload(claim), claims=[claim])


def test_reported_and_unverified_claims_may_publish_without_evidence() -> None:
    event = make_event()
    reported = make_claim(event.id, state=ClaimState.REPORTED, evidence_count=0)
    unknown = make_claim(event.id, state=ClaimState.UNVERIFIED, evidence_count=0)
    payload = EventVersionCreate(
        title="Reported vessel incident",
        sentences=[
            PublicationSentence(
                section="reported",
                text="An operator reported an incident.",
                claim_ids=[reported.id],
            ),
            PublicationSentence(
                section="unknown",
                text="The extent of damage remains unverified.",
                claim_ids=[unknown.id],
            ),
        ],
        evidence_ids=[],
        published_by="analyst:one",
        preview_hash="0" * 64,
    )

    validate_publication(event=event, payload=payload, claims=[reported, unknown])


def test_model_cannot_publish_event_version() -> None:
    event = make_event()
    claim = make_claim(event.id)
    payload = make_payload(claim).model_copy(update={"published_by": "model:writer-v1"})

    with pytest.raises(PublicationPolicyError, match="model cannot publish"):
        validate_publication(event=event, payload=payload, claims=[claim])


def test_sentence_mapping_uses_stable_indexes_for_duplicate_text() -> None:
    claim_a = uuid4()
    claim_b = uuid4()
    payload = EventVersionCreate(
        title="Duplicated wording",
        sentences=[
            PublicationSentence(
                section="reported", text="Operations are restricted.", claim_ids=[claim_a]
            ),
            PublicationSentence(
                section="reported", text="Operations are restricted.", claim_ids=[claim_b]
            ),
        ],
        evidence_ids=[],
        published_by="analyst:one",
        preview_hash="0" * 64,
    )

    _, sentence_map, snapshots = _draft_artifacts(payload)

    assert sentence_map == {
        "reported:0": [str(claim_a)],
        "reported:1": [str(claim_b)],
    }
    assert [snapshot["key"] for snapshot in snapshots] == ["reported:0", "reported:1"]


def test_publish_rejects_a_stale_preview_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    event = make_event()
    policy_claim = make_claim(event.id)
    claim = Claim(
        id=policy_claim.id,
        event_id=event.id,
        text="An incident occurred.",
        claim_state=ClaimState.CONFIRMED,
        quantity_json=[],
        proposed_by="analyst:one",
        reviewed_by="analyst:one",
        sensitivity_flags=[],
        first_seen_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
    )
    context = PublicationContext(
        event=event,
        event_snapshot={
            "id": str(event.id),
            "slug": event.slug,
            "event_type": event.event_type.value,
            "corridor": event.corridor.value,
            "status": event.status.value,
            "severity": event.severity,
            "geojson": None,
            "ports": [],
        },
        claim_rows=[claim],
        evidence_rows=[],
        policy_views=[policy_claim],
        latest_version=None,
        next_version_no=1,
    )
    monkeypatch.setattr("eastmed_api.publication._load_context", lambda *args, **kwargs: context)
    session = MagicMock(spec=Session)

    with pytest.raises(PublicationPolicyError, match="Preview is stale"):
        publish_event_version(
            session,
            event_id=event.id,
            payload=make_payload(policy_claim),
        )


def test_publish_service_requires_approval_record_for_high_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = make_event(severity=4)
    policy_claim = make_claim(event.id)
    claim = Claim(
        id=policy_claim.id,
        event_id=event.id,
        text="An incident occurred.",
        claim_state=ClaimState.CONFIRMED,
        quantity_json=[],
        proposed_by="analyst:one",
        reviewed_by="analyst:one",
        sensitivity_flags=[],
        first_seen_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
    )
    context = PublicationContext(
        event=event,
        event_snapshot={"id": str(event.id)},
        claim_rows=[claim],
        evidence_rows=[],
        policy_views=[policy_claim],
        latest_version=None,
        next_version_no=1,
    )
    monkeypatch.setattr("eastmed_api.publication._load_context", lambda *args, **kwargs: context)
    monkeypatch.setattr("eastmed_api.publication._hash_canonical", lambda _value: "0" * 64)
    session = MagicMock(spec=Session)

    with pytest.raises(PublicationPolicyError, match="content-bound approval record"):
        publish_event_version(
            session,
            event_id=event.id,
            payload=make_payload(policy_claim, signed_off_by="analyst:two"),
        )


def test_reliability_receipt_is_stable_and_binds_complete_release_record() -> None:
    version = EventVersion(
        id=uuid4(),
        event_id=uuid4(),
        version_no=3,
        title="Authority notice verified",
        summary_confirmed="The authority issued a revised transit notice.",
        summary_reported="",
        summary_unknown="",
        whats_changed="The primary notice is now available.",
        sentence_claim_map={"confirmed:0": [str(uuid4())]},
        event_snapshot_json={"severity": 3, "slug": "authority-notice"},
        claim_snapshot_json=[{"id": str(uuid4()), "state": "confirmed"}],
        evidence_snapshot_json=[{"source_tier": "A", "directness": "primary"}],
        published_at=datetime(2026, 7, 21, 9, 15, tzinfo=UTC),
        published_by=f"desk:{uuid4()}",
        signed_off_by=f"desk:{uuid4()}",
        publication_approval_id=uuid4(),
        policy_version="publication-policy-v1",
        model_versions={"claim_extraction": "fingerprint-123"},
        content_hash="a" * 64,
    )

    first = reliability_receipt_for_version(version)
    second = reliability_receipt_for_version(version)

    assert first.receipt_hash == second.receipt_hash
    assert first.content_hash == version.content_hash
    assert first.publication_approval_id == version.publication_approval_id
    assert first.claim_snapshots == version.claim_snapshot_json
    version.content_hash = "b" * 64
    changed = reliability_receipt_for_version(version)
    assert changed.receipt_hash != first.receipt_hash
