from __future__ import annotations

import pytest
from eastmed_pipeline.gold_evaluation import GoldEvaluationError, evaluate_records


def gold(record_id: str, language: str, quotes: list[str]) -> dict[str, object]:
    return {
        "id": record_id,
        "language": language,
        "document": "Synthetic maritime notice.",
        "expected_claims": [{"source_sentence_quote": quote} for quote in quotes],
        "review_status": "approved-two-human",
        "reviewers": ["reviewer-a", "reviewer-b"],
    }


def prediction(record_id: str, quotes: list[str]) -> dict[str, object]:
    return {
        "id": record_id,
        "claims": [{"source_sentence_quote": quote} for quote in quotes],
    }


def test_evaluation_reports_multilingual_misses_and_hallucinations() -> None:
    report = evaluate_records(
        [gold("en-1", "en", ["Port closed."]), gold("el-1", "el", ["Λιμένας κλειστός."])],
        [
            prediction("en-1", ["  PORT   CLOSED. ", "Unreported collision."]),
            prediction("el-1", []),
        ],
    )
    assert report["release_evidence"] is True
    assert report["overall"] == {
        "expected": 2,
        "predicted": 2,
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 1,
        "precision": 0.5,
        "recall": 0.5,
        "hallucination_rate": 0.5,
        "missed_claim_rate": 0.5,
    }
    assert report["per_language"]["en"]["precision"] == 0.5
    assert report["per_language"]["el"]["recall"] == 0.0


def test_unreviewed_records_cannot_be_release_evidence() -> None:
    record = gold("tr-1", "tr", ["Liman kapalıdır."])
    record["review_status"] = "pending-two-human-review"
    record["reviewers"] = []
    with pytest.raises(GoldEvaluationError, match="no dual-human-approved"):
        evaluate_records([record], [prediction("tr-1", ["Liman kapalıdır."])])

    report = evaluate_records(
        [record],
        [prediction("tr-1", ["Liman kapalıdır."])],
        include_unreviewed=True,
    )
    assert report["release_evidence"] is False


def test_approved_record_requires_two_distinct_reviewers() -> None:
    record = gold("ar-1", "ar", ["الميناء مغلق."])
    record["reviewers"] = ["same", "same"]
    with pytest.raises(GoldEvaluationError, match="two distinct reviewer"):
        evaluate_records([record], [prediction("ar-1", [])])


# --- Document-bound matching regressions (blueprint section 28) -------------------
#
# The earlier evaluator pooled quotes by language across every document, so a quote
# copied from another document scored as a true positive. These tests pin the
# document-bound behavior: a prediction is only credited inside the document that
# both expected and produced it.


def gold_doc(
    record_id: str,
    language: str,
    document: str,
    quotes: list[str],
    *,
    spans: list[tuple[int, int] | None] | None = None,
) -> dict[str, object]:
    return {
        "id": record_id,
        "language": language,
        "document": document,
        "expected_claims": _claims(quotes, spans),
        "review_status": "approved-two-human",
        "reviewers": ["reviewer-a", "reviewer-b"],
    }


def prediction_doc(
    record_id: str,
    quotes: list[str],
    *,
    spans: list[tuple[int, int] | None] | None = None,
) -> dict[str, object]:
    return {"id": record_id, "claims": _claims(quotes, spans)}


def _claims(
    quotes: list[str], spans: list[tuple[int, int] | None] | None
) -> list[dict[str, object]]:
    claims: list[dict[str, object]] = []
    for index, quote in enumerate(quotes):
        claim: dict[str, object] = {"source_sentence_quote": quote}
        span = spans[index] if spans is not None else None
        if span is not None:
            claim["char_start"], claim["char_end"] = span
        claims.append(claim)
    return claims


def test_swapped_document_evidence_scores_zero() -> None:
    # The exact blueprint reproduction: each prediction is assigned the OTHER
    # document's genuine quote. Document-bound, that is zero true positives.
    report = evaluate_records(
        [
            gold_doc("en-a", "en", "Port Alpha is closed.", ["Port Alpha is closed."]),
            gold_doc("en-b", "en", "Port Beta is open.", ["Port Beta is open."]),
        ],
        [
            prediction_doc("en-a", ["Port Beta is open."]),
            prediction_doc("en-b", ["Port Alpha is closed."]),
        ],
    )
    assert report["overall"]["true_positive"] == 0
    assert report["overall"]["false_positive"] == 2
    assert report["overall"]["false_negative"] == 2
    assert report["overall"]["precision"] == 0.0
    assert report["overall"]["recall"] == 0.0


def test_correctly_assigned_evidence_scores_one() -> None:
    report = evaluate_records(
        [
            gold_doc("en-a", "en", "Port Alpha is closed.", ["Port Alpha is closed."]),
            gold_doc("en-b", "en", "Port Beta is open.", ["Port Beta is open."]),
        ],
        [
            prediction_doc("en-a", ["Port Alpha is closed."]),
            prediction_doc("en-b", ["Port Beta is open."]),
        ],
    )
    assert report["overall"]["true_positive"] == 2
    assert report["overall"]["precision"] == 1.0
    assert report["overall"]["recall"] == 1.0


def test_duplicate_quote_in_wrong_document_is_not_credited() -> None:
    # The same sentence text appears in two documents. Only the document that
    # actually expected it may receive credit; a copy in the other document is a
    # false positive, not a pooled match.
    report = evaluate_records(
        [
            gold_doc("en-a", "en", "Port is closed.", ["Port is closed."]),
            gold_doc("en-b", "en", "Port is closed.", []),
        ],
        [
            prediction_doc("en-a", []),
            prediction_doc("en-b", ["Port is closed."]),
        ],
    )
    assert report["overall"]["true_positive"] == 0
    assert report["overall"]["false_positive"] == 1
    assert report["overall"]["false_negative"] == 1


def test_valid_prediction_offset_is_credited() -> None:
    document = "Port Alpha is closed."
    report = evaluate_records(
        [gold_doc("en-a", "en", document, ["Port Alpha is closed."])],
        [prediction_doc("en-a", ["Port Alpha is closed."], spans=[(0, len(document))])],
    )
    assert report["overall"]["true_positive"] == 1
    assert report["overall"]["precision"] == 1.0


def test_invalid_prediction_offset_is_not_credited() -> None:
    document = "Port Alpha is closed."
    # The offset points at "Port Alpha" but the claimed quote is the whole sentence,
    # so the span does not resolve to the source: an ungrounded false positive.
    report = evaluate_records(
        [gold_doc("en-a", "en", document, ["Port Alpha is closed."])],
        [prediction_doc("en-a", ["Port Alpha is closed."], spans=[(0, 10)])],
    )
    assert report["overall"]["true_positive"] == 0
    assert report["overall"]["false_positive"] == 1
    assert report["overall"]["false_negative"] == 1
    assert report["overall"]["precision"] == 0.0


def test_gold_span_that_does_not_resolve_invalidates_cohort() -> None:
    document = "Port Alpha is closed."
    with pytest.raises(GoldEvaluationError, match="does not resolve"):
        evaluate_records(
            [gold_doc("en-a", "en", document, ["Port Alpha is closed."], spans=[(0, 10)])],
            [prediction_doc("en-a", ["Port Alpha is closed."])],
        )


def test_out_of_range_prediction_offset_is_not_credited() -> None:
    document = "Port Alpha is closed."
    report = evaluate_records(
        [gold_doc("en-a", "en", document, ["Port Alpha is closed."])],
        [prediction_doc("en-a", ["Port Alpha is closed."], spans=[(0, len(document) + 5)])],
    )
    assert report["overall"]["true_positive"] == 0
    assert report["overall"]["false_positive"] == 1


def test_malformed_prediction_offset_is_rejected() -> None:
    document = "Port Alpha is closed."
    prediction = prediction_doc("en-a", ["Port Alpha is closed."])
    claim = prediction["claims"][0]  # type: ignore[index]
    claim["char_start"] = 0  # only one offset present -> structural error
    with pytest.raises(GoldEvaluationError, match="malformed char_start"):
        evaluate_records(
            [gold_doc("en-a", "en", document, ["Port Alpha is closed."])],
            [prediction],
        )


def test_extra_prediction_is_a_false_positive() -> None:
    report = evaluate_records(
        [gold_doc("en-a", "en", "Port Alpha is closed.", ["Port Alpha is closed."])],
        [prediction_doc("en-a", ["Port Alpha is closed.", "Unreported grounding."])],
    )
    assert report["overall"]["true_positive"] == 1
    assert report["overall"]["false_positive"] == 1
    assert report["overall"]["precision"] == 0.5


def test_unknown_prediction_id_is_rejected() -> None:
    with pytest.raises(GoldEvaluationError, match="not in the eligible set"):
        evaluate_records(
            [gold_doc("en-a", "en", "Port Alpha is closed.", ["Port Alpha is closed."])],
            [prediction_doc("en-unknown", ["Port Alpha is closed."])],
        )


def test_missing_prediction_is_rejected() -> None:
    with pytest.raises(GoldEvaluationError, match="missing predictions"):
        evaluate_records(
            [
                gold_doc("en-a", "en", "Port Alpha is closed.", ["Port Alpha is closed."]),
                gold_doc("en-b", "en", "Port Beta is open.", ["Port Beta is open."]),
            ],
            [prediction_doc("en-a", ["Port Alpha is closed."])],
        )
