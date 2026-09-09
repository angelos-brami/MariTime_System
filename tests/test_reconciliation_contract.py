from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from eastmed_shared.reconciliation.contract import (
    CONTRACT_VERSION,
    AuthorityRole,
    FuelGrade,
    NormalizedRecord,
    QuantityUnit,
    RecordKind,
    Rejection,
    RejectionReason,
    ValueOrigin,
    validate_record,
)


def daily_report(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": CONTRACT_VERSION,
        "record_kind": "daily_report",
        "source_system": "noon-connector",
        "external_ref": "R17",
        "source_revision": "1",
        "vessel_ref": "V3",
        "recorded_at": "2026-09-08T06:00:00Z",
        "voyage_ref": "P4",
        "period_start": "2026-09-07T00:00:00Z",
        "period_end": "2026-09-08T00:00:00Z",
        "items": [
            {"item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": "24.600", "unit": "tonne"}
        ],
    }
    payload.update(overrides)
    return payload


def as_rejection(payload: dict[str, Any]) -> Rejection:
    result = validate_record(payload)
    assert isinstance(result, Rejection), f"expected rejection, got {result!r}"
    return result


def as_record(payload: dict[str, Any]) -> NormalizedRecord:
    result = validate_record(payload)
    assert isinstance(result, NormalizedRecord), f"expected record, got {result!r}"
    return result


def test_valid_daily_report_normalizes() -> None:
    record = as_record(daily_report())
    assert record.record_kind is RecordKind.DAILY_REPORT
    assert record.recorded_at.utc == datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
    assert len(record.observations) == 1
    observation = record.observations[0]
    assert observation.field == "fuel_consumed"
    assert observation.origin is ValueOrigin.REPORTED
    assert observation.value == Decimal("24.600")
    assert observation.fuel_grade is FuelGrade.VLSFO
    assert observation.unit is QuantityUnit.TONNE
    # A daily report's items inherit the report period.
    assert observation.period_start.utc == datetime(2026, 9, 7, 0, 0, tzinfo=UTC)


def test_offset_timestamp_normalized_to_utc_keeps_original() -> None:
    record = as_record(daily_report(recorded_at="2026-09-08T09:00:00+03:00"))
    assert record.recorded_at.utc == datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
    assert record.recorded_at.original == "2026-09-08T09:00:00+03:00"


def test_valid_voyage_plan_uses_per_item_period() -> None:
    record = as_record(
        {
            "schema_version": CONTRACT_VERSION,
            "record_kind": "voyage_plan",
            "source_system": "planner",
            "external_ref": "P4",
            "source_revision": "1",
            "vessel_ref": "V3",
            "recorded_at": "2026-09-06T00:00:00Z",
            "voyage_ref": "P4",
            "plan_revision": "rev-1",
            "effective_from": "2026-09-07T00:00:00Z",
            "effective_to": "2026-09-14T00:00:00Z",
            "items": [
                {
                    "item_key": "vlsfo",
                    "fuel_grade": "VLSFO",
                    "quantity": "23.000",
                    "unit": "tonne",
                    "period_start": "2026-09-07T00:00:00Z",
                    "period_end": "2026-09-08T00:00:00Z",
                }
            ],
        }
    )
    assert record.record_kind is RecordKind.VOYAGE_PLAN
    assert record.plan_revision == "rev-1"
    assert record.observations[0].field == "fuel_planned"
    assert record.observations[0].period_end.utc == datetime(2026, 9, 8, 0, 0, tzinfo=UTC)


def test_valid_fleet_membership() -> None:
    record = as_record(
        {
            "schema_version": CONTRACT_VERSION,
            "record_kind": "fleet_membership",
            "source_system": "provisioning",
            "external_ref": "M1",
            "source_revision": "1",
            "vessel_ref": "V3",
            "recorded_at": "2026-09-01T00:00:00Z",
            "authority_role": "operator",
            "valid_from": "2026-09-01T00:00:00Z",
            "valid_to": "2027-09-01T00:00:00Z",
        }
    )
    assert record.authority_role is AuthorityRole.OPERATOR
    assert record.observations == ()


def _assemble_daily_from_csv_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Mimic a thin CSV adapter: flat string cells, one item per row, grouped.

    The real importer is a later work order; this exists to prove the *semantic*
    validator is format-neutral, so an all-strings CSV shape yields the same
    NormalizedRecord as the JSON object (blueprint 08).
    """
    head = rows[0]
    header_fields = (
        "schema_version",
        "record_kind",
        "source_system",
        "external_ref",
        "source_revision",
        "vessel_ref",
        "recorded_at",
        "voyage_ref",
        "period_start",
        "period_end",
    )
    record: dict[str, Any] = {name: head[name] for name in header_fields}
    record["items"] = [
        {
            "item_key": row["item_key"],
            "fuel_grade": row["fuel_grade"],
            "quantity": row["quantity"],
            "unit": row["unit"],
        }
        for row in rows
    ]
    return record


def test_csv_and_json_forms_validate_identically() -> None:
    json_form = daily_report(
        items=[
            {"item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": "24.600", "unit": "tonne"},
            {"item_key": "mgo", "fuel_grade": "MGO", "quantity": "1.250", "unit": "tonne"},
        ]
    )
    shared = {
        "schema_version": CONTRACT_VERSION,
        "record_kind": "daily_report",
        "source_system": "noon-connector",
        "external_ref": "R17",
        "source_revision": "1",
        "vessel_ref": "V3",
        "recorded_at": "2026-09-08T06:00:00Z",
        "voyage_ref": "P4",
        "period_start": "2026-09-07T00:00:00Z",
        "period_end": "2026-09-08T00:00:00Z",
    }
    csv_rows = [
        {**shared, "item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": "24.600",
         "unit": "tonne"},
        {**shared, "item_key": "mgo", "fuel_grade": "MGO", "quantity": "1.250", "unit": "tonne"},
    ]
    assert as_record(json_form) == as_record(_assemble_daily_from_csv_rows(csv_rows))


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"schema_version": "fleet_reconciliation_v2"}, RejectionReason.UNKNOWN_SCHEMA_VERSION),
        ({"record_kind": "email"}, RejectionReason.UNKNOWN_RECORD_KIND),
        ({"vessel_ref": ""}, RejectionReason.MISSING_REQUIRED_FIELD),
        ({"recorded_at": "2026-09-08T06:00:00"}, RejectionReason.AMBIGUOUS_TIMESTAMP),
        ({"recorded_at": "not-a-time"}, RejectionReason.INVALID_TIMESTAMP),
        (
            {"period_start": "2026-09-08T00:00:00Z", "period_end": "2026-09-07T00:00:00Z"},
            RejectionReason.INTERVAL_ORDER_INVALID,
        ),
    ],
)
def test_header_and_timestamp_rejections(
    overrides: dict[str, Any], reason: RejectionReason
) -> None:
    assert as_rejection(daily_report(**overrides)).reason is reason


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        (
            {"item_key": "x", "fuel_grade": "COAL", "quantity": "1.0", "unit": "tonne"},
            RejectionReason.UNKNOWN_FUEL_GRADE,
        ),
        (
            {"item_key": "x", "fuel_grade": "VLSFO", "quantity": "1.0", "unit": "barrel"},
            RejectionReason.UNSUPPORTED_UNIT,
        ),
        (
            {"item_key": "x", "fuel_grade": "VLSFO", "unit": "tonne"},
            RejectionReason.ABSENT_QUANTITY,
        ),
        (
            {"item_key": "x", "fuel_grade": "VLSFO", "quantity": "", "unit": "tonne"},
            RejectionReason.ABSENT_QUANTITY,
        ),
        (
            {"item_key": "x", "fuel_grade": "VLSFO", "quantity": 24.6, "unit": "tonne"},
            RejectionReason.INVALID_QUANTITY,
        ),
        (
            {"item_key": "x", "fuel_grade": "VLSFO", "quantity": "abc", "unit": "tonne"},
            RejectionReason.INVALID_QUANTITY,
        ),
        (
            {"item_key": "x", "fuel_grade": "VLSFO", "quantity": "1.2345678", "unit": "tonne"},
            RejectionReason.PRECISION_EXCEEDED,
        ),
        (
            {"item_key": "x", "fuel_grade": "VLSFO", "quantity": "1000000001", "unit": "tonne"},
            RejectionReason.RANGE_EXCEEDED,
        ),
    ],
)
def test_item_rejections(item: dict[str, Any], reason: RejectionReason) -> None:
    assert as_rejection(daily_report(items=[item])).reason is reason


def test_absent_quantity_is_not_zero() -> None:
    rejection = as_rejection(
        daily_report(items=[{"item_key": "x", "fuel_grade": "VLSFO", "unit": "tonne"}])
    )
    assert rejection.reason is RejectionReason.ABSENT_QUANTITY


def test_duplicate_item_key_rejected() -> None:
    rejection = as_rejection(
        daily_report(
            items=[
                {"item_key": "dup", "fuel_grade": "VLSFO", "quantity": "1.0", "unit": "tonne"},
                {"item_key": "dup", "fuel_grade": "MGO", "quantity": "2.0", "unit": "tonne"},
            ]
        )
    )
    assert rejection.reason is RejectionReason.DUPLICATE_ITEM_KEY


def test_empty_items_rejected() -> None:
    assert as_rejection(daily_report(items=[])).reason is RejectionReason.EMPTY_ITEMS


def test_invalid_authority_role_rejected() -> None:
    rejection = as_rejection(
        {
            "schema_version": CONTRACT_VERSION,
            "record_kind": "fleet_membership",
            "source_system": "provisioning",
            "external_ref": "M1",
            "source_revision": "1",
            "vessel_ref": "V3",
            "recorded_at": "2026-09-01T00:00:00Z",
            "authority_role": "captain",
            "valid_from": "2026-09-01T00:00:00Z",
            "valid_to": "2027-09-01T00:00:00Z",
        }
    )
    assert rejection.reason is RejectionReason.INVALID_AUTHORITY_ROLE


def test_non_mapping_payload_raises() -> None:
    with pytest.raises(TypeError):
        validate_record(["not", "a", "mapping"])  # type: ignore[arg-type]
