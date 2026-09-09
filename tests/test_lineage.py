from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from eastmed_api.contracts import LineageReviewCreate
from eastmed_pipeline.lineage import (
    LineageReviewError,
    build_text_features,
    lineage_component_graduates,
    minhash_signature,
    normalize_tokens,
    parse_credit_lines,
    review_lineage_proposal,
    score_near_duplicate,
)
from eastmed_schema.enums import LineageReviewDecision
from pydantic import ValidationError


def test_credit_line_parser_handles_english_and_greek_wires() -> None:
    assert parse_credit_lines("ATHENS (Reuters) — Vessels were advised to wait.") == ("Reuters",)
    assert parse_credit_lines("Πηγή: ΑΠΕ-ΜΠΕ — νέα ναυτιλιακή ανακοίνωση") == (
        "Athens-Macedonian News Agency",
    )


def test_minhash_is_deterministic_for_multilingual_tokens() -> None:
    tokens = normalize_tokens("Λιμένας Πειραιά port notice 17")
    assert "λιμένασ" in tokens
    assert minhash_signature(tokens) == minhash_signature(tokens)


def test_near_duplicate_requires_cosine_and_minhash_guard() -> None:
    base = " ".join(f"token{index} vessel navigation warning port authority" for index in range(20))
    changed = base.replace("token3", "revised3").replace("token17", "revised17")
    similar = score_near_duplicate(build_text_features(base), build_text_features(changed))
    distinct = score_near_duplicate(
        build_text_features(base),
        build_text_features("weather forecast gale rain sea state " * 30),
    )
    assert similar.qualifies is True
    assert distinct.qualifies is False


def test_lineage_graduation_requires_precision_sample_and_shadow_duration() -> None:
    assert lineage_component_graduates(precision=0.97, sample_size=100, shadow_days=21)
    assert not lineage_component_graduates(precision=0.969, sample_size=100, shadow_days=21)
    assert not lineage_component_graduates(precision=0.99, sample_size=99, shadow_days=21)
    assert not lineage_component_graduates(precision=0.99, sample_size=100, shadow_days=20.9)


def test_rejected_lineage_review_requires_reason() -> None:
    with pytest.raises(ValidationError, match="require a reason"):
        LineageReviewCreate(
            decision=LineageReviewDecision.REJECT,
            reviewer="analyst:one",
        )


def test_lineage_review_rejects_case_insensitive_model_actor() -> None:
    with pytest.raises(LineageReviewError, match="model cannot review"):
        review_lineage_proposal(
            MagicMock(),
            proposal_id=uuid4(),
            decision=LineageReviewDecision.ACCEPT,
            reviewer="MODEL:lineage-v1",
        )
