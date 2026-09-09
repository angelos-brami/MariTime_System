from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from eastmed_api.contracts import ClaimCreate, ClaimUpdate, EvidenceLinkCreate
from eastmed_api.workspace import WorkspaceError, _version_sentences, update_claim
from eastmed_schema.enums import ClaimState, Directness
from eastmed_schema.models import AuditLog, Claim, EventVersion
from pydantic import ValidationError
from sqlalchemy.orm import Session


def test_manual_claim_and_evidence_contracts_reject_model_reviewers() -> None:
    with pytest.raises(ValidationError, match="model cannot approve"):
        ClaimCreate(
            text="A vessel reported damage.",
            claim_state=ClaimState.REPORTED,
            reviewer="model:claim-v1",
        )
    with pytest.raises(ValidationError, match="model cannot link"):
        EvidenceLinkCreate(
            source_record_id=uuid4(),
            directness=Directness.PRIMARY,
            reviewer="model:evidence-v1",
        )


def test_material_claim_edit_invalidates_prior_second_review() -> None:
    event_id = uuid4()
    claim = Claim(
        id=uuid4(),
        event_id=event_id,
        text="An initial casualty statement.",
        claimant="Authority",
        claim_state=ClaimState.REPORTED,
        quantity_json=[],
        proposed_by="analyst:one",
        reviewed_by="analyst:one",
        second_reviewed_by="analyst:two",
        second_review_approval_id=uuid4(),
        reviewed_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        sensitivity_flags=["casualties"],
        first_seen_at=datetime(2026, 7, 19, 11, 50, tzinfo=UTC),
    )
    session = MagicMock(spec=Session)
    session.get.return_value = claim

    updated = update_claim(
        session,
        event_id=event_id,
        claim_id=claim.id,
        payload=ClaimUpdate(
            text="The authority revised its casualty statement.",
            reviewer="analyst:three",
        ),
    )

    assert updated.second_reviewed_by is None
    assert updated.second_review_approval_id is None
    assert updated.reviewed_by == "analyst:three"
    added = [call.args[0] for call in session.add.call_args_list]
    assert any(isinstance(value, AuditLog) for value in added)
    session.commit.assert_called_once()


def test_second_review_requires_a_distinct_analyst() -> None:
    event_id = uuid4()
    claim = Claim(id=uuid4(), event_id=event_id)
    session = MagicMock(spec=Session)
    session.get.return_value = claim

    with pytest.raises(WorkspaceError, match="another analyst"):
        update_claim(
            session,
            event_id=event_id,
            claim_id=claim.id,
            payload=ClaimUpdate(
                reviewer="analyst:one",
                second_reviewed_by="analyst:one",
            ),
        )


def test_version_sentence_reconstruction_supports_indexed_and_legacy_maps() -> None:
    first = uuid4()
    second = uuid4()
    version = EventVersion(
        summary_confirmed="Traffic is suspended.",
        summary_reported="No reopening time was provided.",
        summary_unknown="",
        whats_changed="",
        sentence_claim_map={
            "confirmed:0": [str(first)],
            "No reopening time was provided.": [str(second)],
        },
    )

    sentences = _version_sentences(version)

    assert [sentence.section for sentence in sentences] == ["confirmed", "reported"]
    assert sentences[0].claim_ids == [first]
    assert sentences[1].claim_ids == [second]
