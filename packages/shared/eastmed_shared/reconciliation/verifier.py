"""Independent result verification (blueprint work order 4, section 19).

A separate path that reconstructs the required facts and recalculates the result from
immutable inputs, then checks the emitted case against them. It deliberately does NOT
import the work-order-3 calculator: reusing the same helper on both sides is not
independent verification (blueprint 19). The arithmetic below is a second, standalone
implementation of the same declared contract.

VerifyResult performs independent checks only — no writes, no authority decisions
(blueprint 24). A case whose required facts or calculations fail verification is
UNVERIFIABLE (or another precise non-success outcome), never COMPLETED. A verified
deterministic report still completes when the optional narrative is withheld.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

_QUANTUM = Decimal("0.01")

# Identifies this independent verifier's contract. Persisted with each verdict so a
# publication gate can tell which verifier implementation produced the completion.
VERIFIER_VERSION = "recon_verifier_v1"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIABLE = "unverifiable"


class VerifyReason(StrEnum):
    MISSING_EVIDENCE = "missing_evidence"
    REFERENCE_NOT_OWNED = "reference_not_owned"
    SOURCE_VERSION_MISMATCH = "source_version_mismatch"
    INPUT_VALUE_MISMATCH = "input_value_mismatch"
    NUMERIC_INCONSISTENT = "numeric_inconsistent"
    UNEXPECTED_RESULT_SHAPE = "unexpected_result_shape"


class CaseCompletionStatus(StrEnum):
    COMPLETED = "completed"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class EvidenceFact:
    """One immutable actual/planned pair reconstructed for a (grade, interval)."""

    fuel_grade: str
    period_start: str
    period_end: str
    actual: Decimal
    planned: Decimal | None
    owner_ok: bool
    is_current: bool


@dataclass(frozen=True)
class VerificationFinding:
    fuel_grade: str
    reason: VerifyReason


@dataclass(frozen=True)
class VerificationVerdict:
    status: VerificationStatus
    checked: int
    findings: tuple[VerificationFinding, ...]

    @property
    def verified(self) -> bool:
        return self.status is VerificationStatus.VERIFIED


def _round2(value: Decimal) -> Decimal:
    return value.quantize(_QUANTUM, rounding=ROUND_HALF_UP)


def _independent_delta_percent(
    actual: Decimal, planned: Decimal
) -> tuple[Decimal, Decimal | None]:
    delta = actual - planned
    if planned == 0:
        return _round2(delta), None
    return _round2(delta), _round2(delta / planned * Decimal("100"))


def _match_evidence(
    variance: dict[str, Any], evidence: Sequence[EvidenceFact]
) -> EvidenceFact | None:
    for fact in evidence:
        if (
            fact.fuel_grade == variance.get("fuel_grade")
            and fact.period_start == variance.get("period_start")
            and fact.period_end == variance.get("period_end")
        ):
            return fact
    return None


def _verify_reconciled(
    variance: dict[str, Any], evidence: Sequence[EvidenceFact]
) -> VerifyReason | None:
    fact = _match_evidence(variance, evidence)
    if fact is None:
        return VerifyReason.MISSING_EVIDENCE
    if not fact.owner_ok:
        return VerifyReason.REFERENCE_NOT_OWNED
    if not fact.is_current:
        return VerifyReason.SOURCE_VERSION_MISMATCH
    if fact.planned is None:
        return VerifyReason.MISSING_EVIDENCE

    try:
        stated_actual = Decimal(str(variance["actual"]))
        stated_planned = Decimal(str(variance["planned"]))
    except (KeyError, ArithmeticError, ValueError):
        return VerifyReason.UNEXPECTED_RESULT_SHAPE
    if stated_actual != fact.actual or stated_planned != fact.planned:
        return VerifyReason.INPUT_VALUE_MISMATCH

    delta, percent = _independent_delta_percent(fact.actual, fact.planned)
    if str(delta) != str(variance.get("delta")):
        return VerifyReason.NUMERIC_INCONSISTENT
    stated_percent = variance.get("percent")
    expected_percent = None if percent is None else str(percent)
    if stated_percent != expected_percent:
        return VerifyReason.NUMERIC_INCONSISTENT
    return None


def verify_case_result(
    result_json: dict[str, Any], evidence: Sequence[EvidenceFact]
) -> VerificationVerdict:
    """Independently confirm every reconciled variance in ``result_json``.

    An ``unresolved`` variance is not a verification failure — it is an honest
    non-success the deterministic path already declared, and it is left untouched.
    Only reconciled variances are recomputed and matched against the evidence."""
    variances = result_json.get("variances")
    if not isinstance(variances, list) or not variances:
        return VerificationVerdict(
            status=VerificationStatus.UNVERIFIABLE,
            checked=0,
            findings=(VerificationFinding("", VerifyReason.UNEXPECTED_RESULT_SHAPE),),
        )

    findings: list[VerificationFinding] = []
    checked = 0
    seen: set[tuple[str, str, str]] = set()
    for variance in variances:
        if not isinstance(variance, dict):
            findings.append(VerificationFinding("", VerifyReason.UNEXPECTED_RESULT_SHAPE))
            continue
        if variance.get("status") == "unresolved":
            continue
        if variance.get("status") != "reconciled":
            findings.append(VerificationFinding("", VerifyReason.UNEXPECTED_RESULT_SHAPE))
            continue
        key = tuple(str(variance.get(k, "")) for k in (
            "fuel_grade", "period_start", "period_end"
        ))
        if key in seen:
            findings.append(VerificationFinding(key[0], VerifyReason.UNEXPECTED_RESULT_SHAPE))
        seen.add((key[0], key[1], key[2]))
        checked += 1
        reason = _verify_reconciled(variance, evidence)
        if reason is not None:
            findings.append(VerificationFinding(str(variance.get("fuel_grade", "")), reason))

    for fact in evidence:
        if fact.planned is not None and (
            fact.fuel_grade, fact.period_start, fact.period_end
        ) not in seen:
            findings.append(VerificationFinding(fact.fuel_grade, VerifyReason.MISSING_EVIDENCE))

    status = (
        VerificationStatus.VERIFIED if not findings else VerificationStatus.UNVERIFIABLE
    )
    return VerificationVerdict(status=status, checked=checked, findings=tuple(findings))


def finalize_case_status(
    verdict: VerificationVerdict, *, has_unresolved: bool
) -> CaseCompletionStatus:
    """A case completes only when verification passes and nothing is unresolved.

    Withholding the optional narrative never blocks completion (blueprint 19); an
    unresolved required fact or a failed verification does."""
    if not verdict.verified or verdict.checked == 0 or has_unresolved:
        return CaseCompletionStatus.UNVERIFIABLE
    return CaseCompletionStatus.COMPLETED
