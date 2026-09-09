from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from eastmed_schema.enums import FuelGrade, QuantityUnit
from eastmed_shared.reconciliation.calculator import (
    CalcRefusal,
    CalcRefusalReason,
    FuelVarianceResult,
    Interval,
    PeriodReconcileResult,
    QuantityInput,
    VesselGradeResult,
    fleet_summary,
    fuel_variance,
    planned_actual_delta,
    report_period_reconcile,
)

PS = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)
PE = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)


def qty(
    value: str,
    *,
    grade: FuelGrade = FuelGrade.VLSFO,
    unit: QuantityUnit = QuantityUnit.TONNE,
    period_start: datetime = PS,
    period_end: datetime = PE,
    revision: str = "1",
) -> QuantityInput:
    return QuantityInput(
        input_id="obs",
        fuel_grade=grade,
        value=Decimal(value),
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        revision=revision,
    )


def variance(
    actual: QuantityInput, planned: QuantityInput | None, **kw: object
) -> FuelVarianceResult:
    result = fuel_variance(actual, planned, **kw)  # type: ignore[arg-type]
    assert isinstance(result, FuelVarianceResult)
    return result


def refusal(result: object) -> CalcRefusal:
    assert isinstance(result, CalcRefusal)
    return result


# --- fuel_variance: the blueprint's own worked numbers (sections 09, 20) -----------


def test_worked_example_matches_blueprint() -> None:
    result = variance(qty("24.600"), qty("23.000"))
    assert result.delta == Decimal("1.60")
    assert result.percent == Decimal("6.96")
    # Unrounded percentage is preserved in full for the receipt.
    assert result.percent_unrounded == Decimal("1.6") / Decimal("23.0") * Decimal("100")
    assert result.receipt.rounding == "ROUND_HALF_UP@2"


def test_correction_worked_example_matches_blueprint() -> None:
    result = variance(qty("23.600"), qty("23.000"))
    assert result.delta == Decimal("0.60")
    assert result.percent == Decimal("2.61")


def test_zero_plan_yields_absolute_variance_without_percent() -> None:
    result = variance(qty("10.000"), qty("0"))
    assert result.delta == Decimal("10.00")
    assert result.percent is None
    assert result.percent_reason == "ZERO_PLAN_BASE"


def test_half_up_rounding_at_two_decimals() -> None:
    # 1.125 - 1.000 = 0.125, which rounds half-up to 0.13.
    result = variance(qty("1.125"), qty("1.000"))
    assert result.delta_unrounded == Decimal("0.125")
    assert result.delta == Decimal("0.13")


def test_negative_delta_is_a_valid_favorable_variance() -> None:
    result = variance(qty("22.000"), qty("23.000"))
    assert result.delta == Decimal("-1.00")
    assert result.percent == Decimal("-4.35")  # -1/23*100 = -4.3478 -> -4.35


@pytest.mark.parametrize(
    ("actual", "planned", "reason", "kwargs"),
    [
        (qty("1"), None, CalcRefusalReason.MISSING_INPUT, {}),
        (
            qty("1"),
            qty("1"),
            CalcRefusalReason.STALE_PLAN,
            {"plan_superseded": True},
        ),
        (qty("1", grade=FuelGrade.MGO), qty("1"), CalcRefusalReason.GRADE_MISMATCH, {}),
        (
            qty("1"),
            qty("1", unit=QuantityUnit.CUBIC_METRE),
            CalcRefusalReason.INCOMPATIBLE_BASIS,
            {},
        ),
        (
            qty("1", period_end=PE + timedelta(days=1)),
            qty("1"),
            CalcRefusalReason.INTERVAL_MISMATCH,
            {},
        ),
        (qty("-1"), qty("1"), CalcRefusalReason.NEGATIVE_QUANTITY, {}),
    ],
)
def test_fuel_variance_refusals(
    actual: QuantityInput,
    planned: QuantityInput | None,
    reason: CalcRefusalReason,
    kwargs: dict[str, object],
) -> None:
    assert refusal(fuel_variance(actual, planned, **kwargs)).reason is reason  # type: ignore[arg-type]


def test_grades_are_never_summed_as_interchangeable() -> None:
    result = fleet_summary(
        [
            VesselGradeResult("V1", FuelGrade.VLSFO, QuantityUnit.TONNE, Decimal("10")),
            VesselGradeResult("V1", FuelGrade.MGO, QuantityUnit.TONNE, Decimal("2")),
        ],
        expected_vessels=1,
    )
    totals = {(g.fuel_grade, g.unit): g.total for g in result.grade_totals}
    assert totals[(FuelGrade.VLSFO, QuantityUnit.TONNE)] == Decimal("10")
    assert totals[(FuelGrade.MGO, QuantityUnit.TONNE)] == Decimal("2")


# --- fleet_summary: covered/expected honesty ---------------------------------------


def test_partial_fleet_is_not_labeled_complete() -> None:
    result = fleet_summary(
        [
            VesselGradeResult("V1", FuelGrade.VLSFO, QuantityUnit.TONNE, Decimal("10")),
            VesselGradeResult("V2", FuelGrade.VLSFO, QuantityUnit.TONNE, Decimal("5")),
        ],
        expected_vessels=3,
    )
    assert result.covered_vessels == 2
    assert result.expected_vessels == 3
    assert result.missing_vessels == 1
    assert result.is_complete is False
    assert result.grade_totals[0].total == Decimal("15")


def test_full_fleet_is_complete() -> None:
    result = fleet_summary(
        [
            VesselGradeResult("V1", FuelGrade.VLSFO, QuantityUnit.TONNE, Decimal("10")),
            VesselGradeResult("V2", FuelGrade.VLSFO, QuantityUnit.TONNE, Decimal("5")),
        ],
        expected_vessels=2,
    )
    assert result.is_complete is True


def test_mixed_units_for_a_grade_are_excluded_not_summed() -> None:
    result = fleet_summary(
        [
            VesselGradeResult("V1", FuelGrade.VLSFO, QuantityUnit.TONNE, Decimal("10")),
            VesselGradeResult("V2", FuelGrade.VLSFO, QuantityUnit.CUBIC_METRE, Decimal("5")),
        ],
        expected_vessels=2,
    )
    assert "VLSFO" in result.excluded_grade_unit_pairs
    assert result.grade_totals == ()
    assert result.is_complete is False


# --- report_period_reconcile -------------------------------------------------------


def test_non_overlapping_intervals_are_ordered() -> None:
    a = Interval(PS, PE)
    b = Interval(PE, PE + timedelta(days=1))
    result = report_period_reconcile([b, a])
    assert isinstance(result, PeriodReconcileResult)
    assert result.covered == (a, b)


def test_overlapping_intervals_are_refused() -> None:
    a = Interval(PS, PE + timedelta(days=1))
    b = Interval(PE, PE + timedelta(days=2))
    result = report_period_reconcile([a, b])
    assert refusal(result).reason is CalcRefusalReason.OVERLAPPING_INTERVALS


def test_empty_intervals_are_missing_input() -> None:
    assert refusal(report_period_reconcile([])).reason is CalcRefusalReason.MISSING_INPUT


# --- planned_actual_delta ----------------------------------------------------------


def test_planned_actual_delta_basis_mismatch() -> None:
    result = planned_actual_delta(
        qty("1"), qty("2"), planned_basis="eta", actual_basis="etd"
    )
    assert refusal(result).reason is CalcRefusalReason.EVENT_MEANING_DIFFERS


def test_planned_actual_delta_ok() -> None:
    result = planned_actual_delta(
        qty("2.000"), qty("3.500"), planned_basis="rob", actual_basis="rob"
    )
    assert not isinstance(result, CalcRefusal)
    assert result.delta == Decimal("1.50")
