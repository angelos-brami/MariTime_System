from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypeGuard

SPACE_PATTERN = re.compile(r"\s+")
SUPPORTED_LANGUAGES = frozenset({"en", "el", "tr", "ar"})
APPROVED_REVIEW_STATUS = "approved-two-human"


class GoldEvaluationError(ValueError):
    pass


@dataclass(frozen=True)
class MetricCounts:
    expected: int
    predicted: int
    true_positive: int
    false_positive: int
    false_negative: int

    @classmethod
    def zero(cls) -> MetricCounts:
        return cls(0, 0, 0, 0, 0)

    def __add__(self, other: MetricCounts) -> MetricCounts:
        return MetricCounts(
            expected=self.expected + other.expected,
            predicted=self.predicted + other.predicted,
            true_positive=self.true_positive + other.true_positive,
            false_positive=self.false_positive + other.false_positive,
            false_negative=self.false_negative + other.false_negative,
        )

    @property
    def precision(self) -> float | None:
        return self.true_positive / self.predicted if self.predicted else None

    @property
    def recall(self) -> float | None:
        return self.true_positive / self.expected if self.expected else None

    @property
    def hallucination_rate(self) -> float | None:
        return self.false_positive / self.predicted if self.predicted else None

    @property
    def missed_claim_rate(self) -> float | None:
        return self.false_negative / self.expected if self.expected else None

    def render(self) -> dict[str, int | float | None]:
        return {
            **asdict(self),
            "precision": self.precision,
            "recall": self.recall,
            "hallucination_rate": self.hallucination_rate,
            "missed_claim_rate": self.missed_claim_rate,
        }


def normalize_quote(value: str) -> str:
    return SPACE_PATTERN.sub(" ", value.strip()).casefold()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise GoldEvaluationError(f"{path}:{line_number} is not valid JSON") from exc
        if not isinstance(value, dict):
            raise GoldEvaluationError(f"{path}:{line_number} must contain a JSON object")
        records.append(value)
    return records


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _claim_span(claim: dict[str, Any], record_id: object) -> tuple[int, int] | None:
    """Return an explicit (char_start, char_end) span, or None when the claim has none.

    A claim either declares both integer offsets or declares neither. A single or
    non-integer offset is a structural error: an unvalidated offset must never be
    treated as if the claim were unanchored.
    """
    has_start = "char_start" in claim
    has_end = "char_end" in claim
    if not has_start and not has_end:
        return None
    start = claim.get("char_start")
    end = claim.get("char_end")
    if not _is_int(start) or not _is_int(end):
        raise GoldEvaluationError(
            f"record {record_id!r} has a claim with malformed char_start/char_end"
        )
    return start, end


def _span_resolves(document: str, start: int, end: int, normalized_quote: str) -> bool:
    """A span resolves only when it lies inside its own document and the substring
    it points at normalizes to the claimed quote. Matching a quote elsewhere in the
    batch is not evidence: a predicted span must ground to *this* document."""
    if not 0 <= start < end <= len(document):
        return False
    return normalize_quote(document[start:end]) == normalized_quote


def _extract(
    record: dict[str, Any],
    key: str,
    *,
    document: str,
    is_gold: bool,
) -> tuple[Counter[str], int]:
    """Extract the multiset of normalized quotes that ground to ``document``.

    Returns ``(grounded, ungrounded)``. ``ungrounded`` counts claims whose declared
    span does not resolve to the document. For gold, an unresolved span invalidates
    the reference cohort (raises); a gold record must not carry a mislabeled span.
    For predictions, an unresolved span is a false positive: the model pointed at a
    source location that does not contain the quote it claimed.
    """
    claims = record.get(key, [])
    if not isinstance(claims, list):
        raise GoldEvaluationError(f"record {record.get('id')!r} has a non-list {key}")
    grounded: Counter[str] = Counter()
    ungrounded = 0
    for claim in claims:
        if not isinstance(claim, dict):
            raise GoldEvaluationError(f"record {record.get('id')!r} contains a malformed claim")
        quote = claim.get("source_sentence_quote", claim.get("quote"))
        if not isinstance(quote, str) or not quote.strip():
            raise GoldEvaluationError(f"record {record.get('id')!r} has a claim without a quote")
        normalized = normalize_quote(quote)
        span = _claim_span(claim, record.get("id"))
        if span is not None and not _span_resolves(document, span[0], span[1], normalized):
            if is_gold:
                raise GoldEvaluationError(
                    f"gold record {record.get('id')!r} has a span that does not "
                    "resolve to its quote"
                )
            ungrounded += 1
            continue
        grounded[normalized] += 1
    return grounded, ungrounded


def _validate_gold(record: dict[str, Any], *, include_unreviewed: bool) -> bool:
    record_id = record.get("id")
    language = record.get("language")
    if not isinstance(record_id, str) or not record_id:
        raise GoldEvaluationError("every gold record requires a non-empty id")
    if language not in SUPPORTED_LANGUAGES:
        raise GoldEvaluationError(
            f"gold record {record_id!r} has unsupported language {language!r}"
        )
    if not isinstance(record.get("document"), str) or not record["document"].strip():
        raise GoldEvaluationError(f"gold record {record_id!r} has no document")
    # Structural well-formedness of gold claims; span grounding is verified once the
    # record is scored against its own document in evaluate_records.
    _extract(record, "expected_claims", document=str(record["document"]), is_gold=True)
    status = record.get("review_status")
    if status == APPROVED_REVIEW_STATUS:
        reviewers = record.get("reviewers")
        if not isinstance(reviewers, list) or len(set(reviewers)) < 2:
            raise GoldEvaluationError(
                f"approved gold record {record_id!r} requires two distinct reviewer IDs"
            )
        return True
    return include_unreviewed


def _document_counts(
    expected: Counter[str],
    predicted_grounded: Counter[str],
    ungrounded_predicted: int,
) -> MetricCounts:
    """Score one document in isolation.

    ``expected`` and ``predicted_grounded`` both belong to the *same* record, so the
    multiset intersection only credits a prediction that appears in that record's own
    gold. This is the correction for the earlier defect, where quotes were pooled by
    language across documents and a prediction copied from another document scored as
    a true positive. Ungrounded predictions inflate the predicted count as false
    positives and can never become true positives.
    """
    true_positive = sum((expected & predicted_grounded).values())
    expected_count = sum(expected.values())
    predicted_count = sum(predicted_grounded.values()) + ungrounded_predicted
    return MetricCounts(
        expected=expected_count,
        predicted=predicted_count,
        true_positive=true_positive,
        false_positive=predicted_count - true_positive,
        false_negative=expected_count - true_positive,
    )


def evaluate_records(
    gold_records: Iterable[dict[str, Any]],
    prediction_records: Iterable[dict[str, Any]],
    *,
    include_unreviewed: bool = False,
) -> dict[str, Any]:
    eligible: dict[str, dict[str, Any]] = {}
    excluded = 0
    for record in gold_records:
        if _validate_gold(record, include_unreviewed=include_unreviewed):
            record_id = str(record["id"])
            if record_id in eligible:
                raise GoldEvaluationError(f"duplicate gold id {record_id!r}")
            eligible[record_id] = record
        else:
            excluded += 1
    if not eligible:
        raise GoldEvaluationError("no dual-human-approved gold records are eligible")

    predictions: dict[str, dict[str, Any]] = {}
    for record in prediction_records:
        prediction_id = record.get("id")
        if not isinstance(prediction_id, str) or prediction_id not in eligible:
            raise GoldEvaluationError(
                f"prediction id {prediction_id!r} is not in the eligible set"
            )
        if prediction_id in predictions:
            raise GoldEvaluationError(f"duplicate prediction id {prediction_id!r}")
        predictions[prediction_id] = record
    missing = set(eligible) - set(predictions)
    if missing:
        raise GoldEvaluationError(f"missing predictions for: {', '.join(sorted(missing))}")

    # Score each document against its own gold, then aggregate. Counters are never
    # pooled across records, so a quote only earns credit inside the document that
    # both expected and predicted it.
    per_language_counts: dict[str, MetricCounts] = {}
    overall_counts = MetricCounts.zero()
    for record_id, gold in eligible.items():
        language = str(gold["language"])
        document = str(gold["document"])
        expected, _ = _extract(gold, "expected_claims", document=document, is_gold=True)
        predicted, ungrounded = _extract(
            predictions[record_id], "claims", document=document, is_gold=False
        )
        counts = _document_counts(expected, predicted, ungrounded)
        per_language_counts[language] = (
            per_language_counts.get(language, MetricCounts.zero()) + counts
        )
        overall_counts = overall_counts + counts

    per_language = {
        language: per_language_counts[language].render()
        for language in sorted(per_language_counts)
    }

    return {
        "schema_version": "1.1",
        "release_evidence": not include_unreviewed,
        "eligible_records": len(eligible),
        "excluded_unreviewed_records": excluded,
        "matching_method": "document-bound-normalized-source-quote",
        "scoring_scope": "per-document",
        "overall": overall_counts.render(),
        "per_language": per_language,
    }


def evaluate_files(
    gold_path: Path,
    predictions_path: Path,
    *,
    include_unreviewed: bool = False,
) -> dict[str, Any]:
    return evaluate_records(
        load_jsonl(gold_path),
        load_jsonl(predictions_path),
        include_unreviewed=include_unreviewed,
    )
