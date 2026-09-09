"""The versioned ``fleet_reconciliation_v1`` data contract (blueprint 08 and 18).

One contract with CSV and equivalent JSON forms. The same semantic validators apply
to both encodings, so a record must not pass in one format and fail in the other:
``validate_record`` is format-neutral and consumes an already-decoded mapping. Thin
CSV/JSON adapters (a later work order) may only decode structure, never semantics.

Design commitments taken from the blueprint:
  * Decimal quantities arrive as strings to preserve precision; a numeric literal is
    rejected rather than silently converted.
  * An absent quantity is never zero.
  * Offset-bearing timestamps are normalized to UTC while the original text is kept;
    a timestamp without an offset is ambiguous and rejected.
  * Every rejection carries a deterministic machine reason, never a guess.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, NoReturn

from eastmed_schema.enums import (
    AuthorityRole,
    FuelGrade,
    QuantityUnit,
    RecordKind,
    ValueOrigin,
)

CONTRACT_VERSION = "fleet_reconciliation_v1"
NORMALIZATION_VERSION = "fuel_units_v1"

# Declared numeric bounds. Input outside these is rejected, never rounded into range.
MAX_DECIMAL_PLACES = 6
MAX_ABS_QUANTITY = Decimal("1000000000")

# Re-exported for callers that import the contract as the single reconciliation entry
# point; the canonical definitions live in eastmed_schema.enums (blueprint 15).
__all__ = [
    "AuthorityRole",
    "FuelGrade",
    "QuantityUnit",
    "RecordKind",
    "ValueOrigin",
    "RejectionReason",
    "NormalizedTimestamp",
    "NormalizedObservation",
    "NormalizedRecord",
    "Rejection",
    "validate_record",
    "CONTRACT_VERSION",
    "NORMALIZATION_VERSION",
    "MAX_DECIMAL_PLACES",
    "MAX_ABS_QUANTITY",
    "CANONICAL_FIELDS",
]


class RejectionReason(StrEnum):
    MALFORMED_RECORD = "malformed_record"
    UNKNOWN_SCHEMA_VERSION = "unknown_schema_version"
    UNKNOWN_RECORD_KIND = "unknown_record_kind"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_TIMESTAMP = "invalid_timestamp"
    AMBIGUOUS_TIMESTAMP = "ambiguous_timestamp"
    INTERVAL_ORDER_INVALID = "interval_order_invalid"
    EMPTY_ITEMS = "empty_items"
    DUPLICATE_ITEM_KEY = "duplicate_item_key"
    UNKNOWN_FUEL_GRADE = "unknown_fuel_grade"
    UNSUPPORTED_UNIT = "unsupported_unit"
    ABSENT_QUANTITY = "absent_quantity"
    INVALID_QUANTITY = "invalid_quantity"
    PRECISION_EXCEEDED = "precision_exceeded"
    RANGE_EXCEEDED = "range_exceeded"
    INVALID_AUTHORITY_ROLE = "invalid_authority_role"


CANONICAL_FIELDS = (
    "schema_version",
    "record_kind",
    "source_system",
    "external_ref",
    "source_revision",
    "vessel_ref",
    "recorded_at",
)


@dataclass(frozen=True)
class NormalizedTimestamp:
    utc: datetime
    original: str


@dataclass(frozen=True)
class NormalizedObservation:
    field: str
    item_key: str
    fuel_grade: FuelGrade
    value: Decimal
    unit: QuantityUnit
    origin: ValueOrigin
    period_start: NormalizedTimestamp
    period_end: NormalizedTimestamp
    source_pointer: str
    normalization_version: str = NORMALIZATION_VERSION


@dataclass(frozen=True)
class NormalizedRecord:
    schema_version: str
    record_kind: RecordKind
    source_system: str
    external_ref: str
    source_revision: str
    vessel_ref: str
    recorded_at: NormalizedTimestamp
    voyage_ref: str | None = None
    plan_revision: str | None = None
    authority_role: AuthorityRole | None = None
    interval_start: NormalizedTimestamp | None = None
    interval_end: NormalizedTimestamp | None = None
    observations: tuple[NormalizedObservation, ...] = ()


@dataclass(frozen=True)
class Rejection:
    reason: RejectionReason
    detail: str
    field: str | None = None


class _RejectionSignal(Exception):
    def __init__(self, rejection: Rejection) -> None:
        super().__init__(rejection.detail)
        self.rejection = rejection


def _reject(reason: RejectionReason, detail: str, field: str | None = None) -> NoReturn:
    raise _RejectionSignal(Rejection(reason=reason, detail=detail, field=field))


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        _reject(RejectionReason.MISSING_REQUIRED_FIELD, f"{key} is required", field=key)
    return value.strip()


def _timestamp(payload: Mapping[str, Any], key: str) -> NormalizedTimestamp:
    raw = payload.get(key)
    if not isinstance(raw, str) or not raw.strip():
        _reject(RejectionReason.MISSING_REQUIRED_FIELD, f"{key} is required", field=key)
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        _reject(RejectionReason.INVALID_TIMESTAMP, f"{key} is not RFC 3339", field=key)
    if parsed.tzinfo is None:
        _reject(
            RejectionReason.AMBIGUOUS_TIMESTAMP,
            f"{key} has no UTC offset",
            field=key,
        )
    return NormalizedTimestamp(utc=parsed.astimezone(UTC), original=text)


def _interval(
    start: NormalizedTimestamp, end: NormalizedTimestamp, field: str
) -> tuple[NormalizedTimestamp, NormalizedTimestamp]:
    if end.utc <= start.utc:
        _reject(
            RejectionReason.INTERVAL_ORDER_INVALID,
            f"{field} end must be after its start",
            field=field,
        )
    return start, end


def _enum_value(
    enum_cls: type[StrEnum],
    raw: Any,
    reason: RejectionReason,
    key: str,
) -> Any:
    if isinstance(raw, str):
        try:
            return enum_cls(raw)
        except ValueError:
            pass
    _reject(reason, f"unsupported {key}: {raw!r}", field=key)


def _quantity(raw: Any, pointer: str) -> Decimal:
    # An absent quantity is never zero: a missing/blank value is a distinct rejection.
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        _reject(RejectionReason.ABSENT_QUANTITY, f"{pointer} has no quantity", field=pointer)
    if not isinstance(raw, str):
        _reject(
            RejectionReason.INVALID_QUANTITY,
            f"{pointer} quantity must be a decimal string, not {type(raw).__name__}",
            field=pointer,
        )
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        _reject(RejectionReason.INVALID_QUANTITY, f"{pointer} is not a decimal", field=pointer)
    if not value.is_finite():
        _reject(RejectionReason.INVALID_QUANTITY, f"{pointer} is not finite", field=pointer)
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > MAX_DECIMAL_PLACES:
        _reject(
            RejectionReason.PRECISION_EXCEEDED,
            f"{pointer} exceeds {MAX_DECIMAL_PLACES} decimal places",
            field=pointer,
        )
    if abs(value) > MAX_ABS_QUANTITY:
        _reject(RejectionReason.RANGE_EXCEEDED, f"{pointer} is out of range", field=pointer)
    return value


def _observations(
    payload: Mapping[str, Any],
    *,
    field_name: str,
    report_period: tuple[NormalizedTimestamp, NormalizedTimestamp] | None,
) -> tuple[NormalizedObservation, ...]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        _reject(RejectionReason.EMPTY_ITEMS, "at least one item is required", field="items")
    observations: list[NormalizedObservation] = []
    seen_keys: set[str] = set()
    for index, item in enumerate(raw_items):
        pointer = f"/items/{index}"
        if not isinstance(item, Mapping):
            _reject(RejectionReason.MALFORMED_RECORD, f"{pointer} is not an object", field=pointer)
        item_key = _required_str(item, "item_key")
        if item_key in seen_keys:
            _reject(
                RejectionReason.DUPLICATE_ITEM_KEY,
                f"{pointer} repeats item_key {item_key!r}",
                field=pointer,
            )
        seen_keys.add(item_key)
        grade = _enum_value(
            FuelGrade, item.get("fuel_grade"), RejectionReason.UNKNOWN_FUEL_GRADE, "fuel_grade"
        )
        unit = _enum_value(
            QuantityUnit, item.get("unit"), RejectionReason.UNSUPPORTED_UNIT, "unit"
        )
        value = _quantity(item.get("quantity"), pointer)
        if report_period is not None:
            period = report_period
        else:
            period = _interval(
                _timestamp(item, "period_start"),
                _timestamp(item, "period_end"),
                f"{pointer}/period",
            )
        source_pointer = item.get("source_pointer")
        observations.append(
            NormalizedObservation(
                field=field_name,
                item_key=item_key,
                fuel_grade=grade,
                value=value,
                unit=unit,
                origin=ValueOrigin.REPORTED,
                period_start=period[0],
                period_end=period[1],
                source_pointer=source_pointer if isinstance(source_pointer, str) else pointer,
            )
        )
    return tuple(observations)


def _validate(payload: Mapping[str, Any]) -> NormalizedRecord:
    schema_version = _required_str(payload, "schema_version")
    if schema_version != CONTRACT_VERSION:
        _reject(
            RejectionReason.UNKNOWN_SCHEMA_VERSION,
            f"schema_version {schema_version!r} is not {CONTRACT_VERSION!r}",
            field="schema_version",
        )
    kind: RecordKind = _enum_value(
        RecordKind, payload.get("record_kind"), RejectionReason.UNKNOWN_RECORD_KIND, "record_kind"
    )
    source_system = _required_str(payload, "source_system")
    external_ref = _required_str(payload, "external_ref")
    source_revision = _required_str(payload, "source_revision")
    vessel_ref = _required_str(payload, "vessel_ref")
    recorded_at = _timestamp(payload, "recorded_at")

    if kind is RecordKind.FLEET_MEMBERSHIP:
        role: AuthorityRole = _enum_value(
            AuthorityRole,
            payload.get("authority_role"),
            RejectionReason.INVALID_AUTHORITY_ROLE,
            "authority_role",
        )
        start, end = _interval(
            _timestamp(payload, "valid_from"), _timestamp(payload, "valid_to"), "validity"
        )
        return NormalizedRecord(
            schema_version=schema_version,
            record_kind=kind,
            source_system=source_system,
            external_ref=external_ref,
            source_revision=source_revision,
            vessel_ref=vessel_ref,
            recorded_at=recorded_at,
            authority_role=role,
            interval_start=start,
            interval_end=end,
        )

    if kind is RecordKind.VOYAGE_PLAN:
        voyage_ref = _required_str(payload, "voyage_ref")
        plan_revision = _required_str(payload, "plan_revision")
        start, end = _interval(
            _timestamp(payload, "effective_from"), _timestamp(payload, "effective_to"), "effective"
        )
        plan_observations = _observations(payload, field_name="fuel_planned", report_period=None)
        return NormalizedRecord(
            schema_version=schema_version,
            record_kind=kind,
            source_system=source_system,
            external_ref=external_ref,
            source_revision=source_revision,
            vessel_ref=vessel_ref,
            recorded_at=recorded_at,
            voyage_ref=voyage_ref,
            plan_revision=plan_revision,
            interval_start=start,
            interval_end=end,
            observations=plan_observations,
        )

    # DAILY_REPORT
    daily_voyage_ref = _required_str(payload, "voyage_ref")
    period = _interval(
        _timestamp(payload, "period_start"), _timestamp(payload, "period_end"), "reporting_period"
    )
    report_observations = _observations(payload, field_name="fuel_consumed", report_period=period)
    return NormalizedRecord(
        schema_version=schema_version,
        record_kind=kind,
        source_system=source_system,
        external_ref=external_ref,
        source_revision=source_revision,
        vessel_ref=vessel_ref,
        recorded_at=recorded_at,
        voyage_ref=daily_voyage_ref,
        interval_start=period[0],
        interval_end=period[1],
        observations=report_observations,
    )


def validate_record(payload: Mapping[str, Any]) -> NormalizedRecord | Rejection:
    """Validate and normalize one contract record.

    Returns a ``NormalizedRecord`` when the record satisfies the contract, or a
    ``Rejection`` with a deterministic reason when it does not. Data problems never
    raise; only a non-mapping payload (a structural caller error) does.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("validate_record expects a decoded mapping")
    try:
        return _validate(payload)
    except _RejectionSignal as signal:
        return signal.rejection
