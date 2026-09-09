"""Deterministic maritime calculations (blueprint work order 3, section 20).

A typed registry of named functions over already-verified inputs. No agent submits a
formula; callers select a registered function by name. Every function is pure and
deterministic: Decimal arithmetic from source strings, finite values only, explicit
units, ROUND_HALF_UP to two decimals for displayed tonnes and percentages, and the
unrounded result preserved in a reproducibility receipt.

The functions never convert between units or fuel grades, never sum grades as if
interchangeable, and refuse rather than guess when a required condition fails. A zero
plan still yields a valid absolute variance; the percentage is withheld with its
reason. These are differences, not proof of waste.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from eastmed_schema.enums import FuelGrade, QuantityUnit

CALCULATOR_VERSION = "recon_calculator_v1"
DISPLAY_QUANTUM = Decimal("0.01")
ROUNDING = "ROUND_HALF_UP@2"
ZERO_PLAN_BASE = "ZERO_PLAN_BASE"


class CalcRefusalReason(StrEnum):
    MISSING_INPUT = "missing_input"
    GRADE_MISMATCH = "grade_mismatch"
    INCOMPATIBLE_BASIS = "incompatible_basis"
    INTERVAL_MISMATCH = "interval_mismatch"
    STALE_PLAN = "stale_plan"
    NEGATIVE_QUANTITY = "negative_quantity"
    OVERLAPPING_INTERVALS = "overlapping_intervals"
    UNRESOLVED_REVISION = "unresolved_revision"
    EVENT_MEANING_DIFFERS = "event_meaning_differs"
    TIME_BASE_DIFFERS = "time_base_differs"


@dataclass(frozen=True)
class CalcRefusal:
    reason: CalcRefusalReason
    detail: str


@dataclass(frozen=True)
class QuantityInput:
    """A single verified quantity fact for a grade over an interval."""

    input_id: str
    fuel_grade: FuelGrade
    value: Decimal
    unit: QuantityUnit
    period_start: datetime
    period_end: datetime
    revision: str


@dataclass(frozen=True)
class CalculationReceipt:
    function: str
    formula_version: str
    rounding: str
    inputs: tuple[dict[str, str], ...]
    assumptions: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()


def _round2(value: Decimal) -> Decimal:
    return value.quantize(DISPLAY_QUANTUM, rounding=ROUND_HALF_UP)


def _input_ref(role: str, item: QuantityInput) -> dict[str, str]:
    return {
        "role": role,
        "input_id": item.input_id,
        "fuel_grade": item.fuel_grade.value,
        "unit": item.unit.value,
        "value": str(item.value),
        "revision": item.revision,
        "period_start": item.period_start.isoformat(),
        "period_end": item.period_end.isoformat(),
    }


@dataclass(frozen=True)
class FuelVarianceResult:
    fuel_grade: FuelGrade
    unit: QuantityUnit
    period_start: datetime
    period_end: datetime
    actual_unrounded: Decimal
    planned_unrounded: Decimal
    delta_unrounded: Decimal
    delta: Decimal
    percent_unrounded: Decimal | None
    percent: Decimal | None
    percent_reason: str | None
    receipt: CalculationReceipt

    def to_dict(self) -> dict[str, object]:
        return {
            "function": "fuel_variance",
            "fuel_grade": self.fuel_grade.value,
            "unit": self.unit.value,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "actual": str(self.actual_unrounded),
            "planned": str(self.planned_unrounded),
            "delta_unrounded": str(self.delta_unrounded),
            "delta": str(self.delta),
            "percent_unrounded": None if self.percent_unrounded is None
            else str(self.percent_unrounded),
            "percent": None if self.percent is None else str(self.percent),
            "percent_reason": self.percent_reason,
            "rounding": self.receipt.rounding,
            "formula_version": self.receipt.formula_version,
        }


def fuel_variance(
    actual: QuantityInput,
    planned: QuantityInput | None,
    *,
    plan_superseded: bool = False,
) -> FuelVarianceResult | CalcRefusal:
    """Actual minus planned fuel for the same grade and interval.

    A negative delta is a legitimate favorable variance; a negative *input* quantity
    is not physical and is refused. A zero plan yields a valid absolute delta with the
    percentage withheld (ZERO_PLAN_BASE)."""
    if planned is None:
        return CalcRefusal(CalcRefusalReason.MISSING_INPUT, "no planned quantity for this grade")
    if plan_superseded:
        return CalcRefusal(CalcRefusalReason.STALE_PLAN, "planned revision has been superseded")
    if actual.fuel_grade is not planned.fuel_grade:
        return CalcRefusal(
            CalcRefusalReason.GRADE_MISMATCH,
            f"actual {actual.fuel_grade.value} vs planned {planned.fuel_grade.value}",
        )
    if actual.unit is not planned.unit:
        return CalcRefusal(
            CalcRefusalReason.INCOMPATIBLE_BASIS,
            f"actual {actual.unit.value} vs planned {planned.unit.value}",
        )
    if (actual.period_start, actual.period_end) != (planned.period_start, planned.period_end):
        return CalcRefusal(
            CalcRefusalReason.INTERVAL_MISMATCH, "actual and planned intervals differ"
        )
    if actual.value < 0 or planned.value < 0:
        return CalcRefusal(CalcRefusalReason.NEGATIVE_QUANTITY, "fuel quantity cannot be negative")

    delta = actual.value - planned.value
    if planned.value == 0:
        percent_unrounded: Decimal | None = None
        percent: Decimal | None = None
        percent_reason: str | None = ZERO_PLAN_BASE
    else:
        percent_unrounded = delta / planned.value * Decimal("100")
        percent = _round2(percent_unrounded)
        percent_reason = None

    return FuelVarianceResult(
        fuel_grade=actual.fuel_grade,
        unit=actual.unit,
        period_start=actual.period_start,
        period_end=actual.period_end,
        actual_unrounded=actual.value,
        planned_unrounded=planned.value,
        delta_unrounded=delta,
        delta=_round2(delta),
        percent_unrounded=percent_unrounded,
        percent=percent,
        percent_reason=percent_reason,
        receipt=CalculationReceipt(
            function="fuel_variance",
            formula_version=CALCULATOR_VERSION,
            rounding=ROUNDING,
            inputs=(_input_ref("actual", actual), _input_ref("planned", planned)),
            assumptions=() if percent_reason is None else (ZERO_PLAN_BASE,),
        ),
    )


@dataclass(frozen=True)
class DeltaResult:
    basis: str
    unit: QuantityUnit
    delta_unrounded: Decimal
    delta: Decimal
    receipt: CalculationReceipt

    def to_dict(self) -> dict[str, object]:
        return {
            "function": "planned_actual_delta",
            "basis": self.basis,
            "unit": self.unit.value,
            "delta_unrounded": str(self.delta_unrounded),
            "delta": str(self.delta),
            "rounding": self.receipt.rounding,
            "formula_version": self.receipt.formula_version,
        }


def planned_actual_delta(
    planned: QuantityInput,
    actual: QuantityInput,
    *,
    planned_basis: str,
    actual_basis: str,
) -> DeltaResult | CalcRefusal:
    """Generic delta between an equivalent planned and observed quantity.

    Refuses when the events mean different things (basis differs) or are on different
    unit bases. Distinct from fuel_variance, which adds grade/interval rules."""
    if planned_basis != actual_basis:
        return CalcRefusal(
            CalcRefusalReason.EVENT_MEANING_DIFFERS,
            f"planned basis {planned_basis!r} vs actual basis {actual_basis!r}",
        )
    if planned.unit is not actual.unit:
        return CalcRefusal(
            CalcRefusalReason.TIME_BASE_DIFFERS,
            f"planned {planned.unit.value} vs actual {actual.unit.value}",
        )
    delta = actual.value - planned.value
    return DeltaResult(
        basis=planned_basis,
        unit=planned.unit,
        delta_unrounded=delta,
        delta=_round2(delta),
        receipt=CalculationReceipt(
            function="planned_actual_delta",
            formula_version=CALCULATOR_VERSION,
            rounding=ROUNDING,
            inputs=(_input_ref("planned", planned), _input_ref("actual", actual)),
        ),
    )


@dataclass(frozen=True)
class Interval:
    period_start: datetime
    period_end: datetime


@dataclass(frozen=True)
class PeriodReconcileResult:
    covered: tuple[Interval, ...]
    receipt: CalculationReceipt

    def to_dict(self) -> dict[str, object]:
        return {
            "function": "report_period_reconcile",
            "covered": [
                {"period_start": i.period_start.isoformat(), "period_end": i.period_end.isoformat()}
                for i in self.covered
            ],
            "formula_version": self.receipt.formula_version,
        }


def report_period_reconcile(
    intervals: Sequence[Interval],
) -> PeriodReconcileResult | CalcRefusal:
    """Order the reporting intervals and reject any overlap.

    A revision is never chosen by arrival time; overlaps must be resolved upstream by
    revision lineage before reconciliation. An empty set is a missing input."""
    if not intervals:
        return CalcRefusal(CalcRefusalReason.MISSING_INPUT, "no reporting intervals supplied")
    for interval in intervals:
        if interval.period_end <= interval.period_start:
            return CalcRefusal(CalcRefusalReason.UNRESOLVED_REVISION, "non-positive interval")
    ordered = sorted(intervals, key=lambda i: (i.period_start, i.period_end))
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if later.period_start < earlier.period_end:
            return CalcRefusal(
                CalcRefusalReason.OVERLAPPING_INTERVALS, "reporting intervals overlap"
            )
    return PeriodReconcileResult(
        covered=tuple(ordered),
        receipt=CalculationReceipt(
            function="report_period_reconcile",
            formula_version=CALCULATOR_VERSION,
            rounding=ROUNDING,
            inputs=tuple(
                {"period_start": i.period_start.isoformat(), "period_end": i.period_end.isoformat()}
                for i in ordered
            ),
        ),
    )


@dataclass(frozen=True)
class GradeTotal:
    fuel_grade: FuelGrade
    unit: QuantityUnit
    total: Decimal


@dataclass(frozen=True)
class FleetSummaryResult:
    expected_vessels: int
    covered_vessels: int
    grade_totals: tuple[GradeTotal, ...]
    excluded_grade_unit_pairs: tuple[str, ...]
    receipt: CalculationReceipt

    @property
    def is_complete(self) -> bool:
        return self.covered_vessels >= self.expected_vessels and not self.excluded_grade_unit_pairs

    @property
    def missing_vessels(self) -> int:
        return max(self.expected_vessels - self.covered_vessels, 0)

    def to_dict(self) -> dict[str, object]:
        return {
            "function": "fleet_summary",
            "expected_vessels": self.expected_vessels,
            "covered_vessels": self.covered_vessels,
            "missing_vessels": self.missing_vessels,
            "is_complete": self.is_complete,
            "grade_totals": [
                {"fuel_grade": g.fuel_grade.value, "unit": g.unit.value, "total": str(g.total)}
                for g in self.grade_totals
            ],
            "excluded_grade_unit_pairs": list(self.excluded_grade_unit_pairs),
            "formula_version": self.receipt.formula_version,
        }


@dataclass(frozen=True)
class VesselGradeResult:
    vessel_ref: str
    fuel_grade: FuelGrade
    unit: QuantityUnit
    value: Decimal


def fleet_summary(
    vessel_results: Sequence[VesselGradeResult],
    *,
    expected_vessels: int,
) -> FleetSummaryResult:
    """Sum verified vessel results per (grade, unit); never across grades.

    The result always exposes covered vs expected vessel counts and is only labeled
    complete when every expected vessel is covered. A mixed-unit grade is excluded and
    named rather than silently summed."""
    by_key: dict[tuple[FuelGrade, QuantityUnit], Decimal] = {}
    units_seen: dict[FuelGrade, set[QuantityUnit]] = {}
    for row in vessel_results:
        units_seen.setdefault(row.fuel_grade, set()).add(row.unit)
        key = (row.fuel_grade, row.unit)
        by_key[key] = by_key.get(key, Decimal("0")) + row.value

    excluded = tuple(
        sorted(f"{grade.value}" for grade, units in units_seen.items() if len(units) > 1)
    )
    totals = tuple(
        GradeTotal(fuel_grade=grade, unit=unit, total=total)
        for (grade, unit), total in sorted(
            by_key.items(), key=lambda kv: (kv[0][0].value, kv[0][1].value)
        )
        if grade.value not in excluded
    )
    covered = len({row.vessel_ref for row in vessel_results})
    return FleetSummaryResult(
        expected_vessels=expected_vessels,
        covered_vessels=covered,
        grade_totals=totals,
        excluded_grade_unit_pairs=excluded,
        receipt=CalculationReceipt(
            function="fleet_summary",
            formula_version=CALCULATOR_VERSION,
            rounding=ROUNDING,
            inputs=tuple(
                {
                    "vessel_ref": r.vessel_ref,
                    "fuel_grade": r.fuel_grade.value,
                    "unit": r.unit.value,
                    "value": str(r.value),
                }
                for r in vessel_results
            ),
            exclusions=excluded,
        ),
    )


# Typed registry. Callers select a function by name; formula code is never supplied by
# an agent. Signatures differ, so this maps to the validated callables for discovery
# and version reporting rather than a single uniform call shape.
REGISTRY: dict[str, Callable[..., object]] = {
    "report_period_reconcile": report_period_reconcile,
    "fuel_variance": fuel_variance,
    "planned_actual_delta": planned_actual_delta,
    "fleet_summary": fleet_summary,
}

__all__ = [
    "CALCULATOR_VERSION",
    "ROUNDING",
    "ZERO_PLAN_BASE",
    "CalcRefusal",
    "CalcRefusalReason",
    "QuantityInput",
    "CalculationReceipt",
    "FuelVarianceResult",
    "DeltaResult",
    "Interval",
    "PeriodReconcileResult",
    "GradeTotal",
    "VesselGradeResult",
    "FleetSummaryResult",
    "fuel_variance",
    "planned_actual_delta",
    "report_period_reconcile",
    "fleet_summary",
    "REGISTRY",
]
