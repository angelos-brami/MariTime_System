from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from eastmed_shared.reconciliation.verifier import (
    CaseCompletionStatus,
    EvidenceFact,
    VerificationStatus,
    VerifyReason,
    finalize_case_status,
    verify_case_result,
)

PS = "2026-09-07T00:00:00+00:00"
PE = "2026-09-08T00:00:00+00:00"


def reconciled(
    *, delta: str = "1.60", percent: str | None = "6.96", actual: str = "24.600000",
    planned: str = "23.000000", grade: str = "VLSFO",
) -> dict[str, Any]:
    return {
        "status": "reconciled",
        "fuel_grade": grade,
        "period_start": PS,
        "period_end": PE,
        "actual": actual,
        "planned": planned,
        "delta": delta,
        "percent": percent,
    }


def fact(
    *, actual: str = "24.600000", planned: str | None = "23.000000", grade: str = "VLSFO",
    owner_ok: bool = True, is_current: bool = True,
) -> EvidenceFact:
    return EvidenceFact(
        fuel_grade=grade,
        period_start=PS,
        period_end=PE,
        actual=Decimal(actual),
        planned=None if planned is None else Decimal(planned),
        owner_ok=owner_ok,
        is_current=is_current,
    )


def test_correct_result_verifies() -> None:
    verdict = verify_case_result({"variances": [reconciled()]}, [fact()])
    assert verdict.status is VerificationStatus.VERIFIED
    assert verdict.checked == 1
    assert verdict.findings == ()


def test_tampered_delta_is_numeric_inconsistent() -> None:
    verdict = verify_case_result({"variances": [reconciled(delta="9.99")]}, [fact()])
    assert verdict.status is VerificationStatus.UNVERIFIABLE
    assert verdict.findings[0].reason is VerifyReason.NUMERIC_INCONSISTENT


def test_tampered_input_value_is_caught() -> None:
    # Result claims a planned that does not match the immutable observation.
    verdict = verify_case_result({"variances": [reconciled(planned="20.000000")]}, [fact()])
    assert verdict.status is VerificationStatus.UNVERIFIABLE
    assert verdict.findings[0].reason is VerifyReason.INPUT_VALUE_MISMATCH


def test_missing_evidence_is_unverifiable() -> None:
    verdict = verify_case_result({"variances": [reconciled(grade="MGO")]}, [fact()])
    assert verdict.findings[0].reason is VerifyReason.MISSING_EVIDENCE


def test_reference_not_owned_is_unverifiable() -> None:
    verdict = verify_case_result({"variances": [reconciled()]}, [fact(owner_ok=False)])
    assert verdict.findings[0].reason is VerifyReason.REFERENCE_NOT_OWNED


def test_superseded_evidence_is_source_version_mismatch() -> None:
    verdict = verify_case_result({"variances": [reconciled()]}, [fact(is_current=False)])
    assert verdict.findings[0].reason is VerifyReason.SOURCE_VERSION_MISMATCH


def test_zero_plan_percent_none_verifies() -> None:
    variance = reconciled(delta="10.00", percent=None, actual="10.000000", planned="0")
    verdict = verify_case_result({"variances": [variance]}, [fact(actual="10.000000", planned="0")])
    assert verdict.status is VerificationStatus.VERIFIED


def test_unresolved_variance_is_not_a_verification_failure() -> None:
    variances = [{"fuel_grade": "VLSFO", "status": "unresolved", "reason": "missing_input"}]
    verdict = verify_case_result({"variances": variances}, [])
    assert verdict.status is VerificationStatus.VERIFIED
    assert verdict.checked == 0


def test_finalize_completed_when_verified_and_resolved() -> None:
    verdict = verify_case_result({"variances": [reconciled()]}, [fact()])
    assert finalize_case_status(verdict, has_unresolved=False) is CaseCompletionStatus.COMPLETED


def test_finalize_unverifiable_when_unresolved_present() -> None:
    verdict = verify_case_result({"variances": [reconciled()]}, [fact()])
    assert finalize_case_status(verdict, has_unresolved=True) is CaseCompletionStatus.UNVERIFIABLE


def test_finalize_unverifiable_when_verification_fails() -> None:
    verdict = verify_case_result({"variances": [reconciled(delta="9.99")]}, [fact()])
    assert finalize_case_status(verdict, has_unresolved=False) is CaseCompletionStatus.UNVERIFIABLE


@pytest.mark.parametrize("variances", [[], [None], [{"status": "done"}]])
def test_empty_or_malformed_result_cannot_verify(variances: list[Any]) -> None:
    assert not verify_case_result({"variances": variances}, [fact()]).verified


def test_duplicate_result_cannot_count_as_complete() -> None:
    assert not verify_case_result({"variances": [reconciled(), reconciled()]}, [fact()]).verified


def test_omitted_required_grade_cannot_verify() -> None:
    assert not verify_case_result(
        {"variances": [reconciled()]}, [fact(), fact(grade="MGO")]
    ).verified
