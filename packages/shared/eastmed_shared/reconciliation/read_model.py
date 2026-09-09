"""Customer-facing read model for the private fleet report (work order 8, sections 09, 25).

The product's main view is a finished daily fleet report with coverage and evidence — not
a chat window and not an approval queue. This module holds the *pure* presentation
contracts and rules: how a case's business/automation statuses roll up to a
customer-visible outcome, how the daily report chooses an empty state, how a fleet total
sums only compatible quantities, and the exact response shape each view renders.

Two invariants are load-bearing (blueprint 25):

* A proposed result is visibly different from a completed one. Only a case that reached
  ``verified_complete`` (authorized, committed and read back) is ``COMPLETE``; a published
  but not-yet-confirmed case is ``IN_PROGRESS``, and safe abstention is ``UNRESOLVED``.
  A green "complete" is never shown for either.
* The routine customer view carries no approve/reject/release task the system depends on.
  ``CUSTOMER_ACTIONS`` is empty by construction; recompute is a service-only interface.

Nothing here touches a database, a queue or a model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

READ_MODEL_VERSION = "recon_read_model_v1"

# The customer view is read-only; there is deliberately no approve/reject/release action
# the runtime depends on (blueprint 25). Kept as an explicit, testable constant.
CUSTOMER_ACTIONS: tuple[str, ...] = ()


class PresentationStatus(StrEnum):
    COMPLETE = "complete"
    IN_PROGRESS = "in_progress"
    AWAITING_DATA = "awaiting_data"
    UNRESOLVED = "unresolved"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class EvidenceStatus(StrEnum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    INCOMPLETE = "incomplete"
    UNAVAILABLE = "unavailable"


class EmptyState(StrEnum):
    NONE = "none"
    NO_EXPECTED_JOBS = "no_expected_jobs"
    NO_AUTHORIZED_FEED = "no_authorized_feed"
    FEED_DELAYED = "feed_delayed"
    NO_RELEVANT_DIFFERENCES = "no_relevant_differences"
    INVALID_INPUTS = "invalid_inputs"
    CALCULATIONS_UNAVAILABLE = "calculations_unavailable"
    DISABLED_SERVICE = "disabled_service"


_AWAITING = frozenset({"awaiting_arrival", "waiting_for_machine_data"})
_IN_PROGRESS = frozenset(
    {"received", "validated", "reconciled", "calculated", "publication_ready", "published"}
)

# The customer-facing next step for each automation state; a completed or disabled case
# has no pending machine step. Unknown states fall back to a machine-data wait rather than
# implying completion.
_NEXT_STEP: dict[str, str | None] = {
    "awaiting_arrival": "await_authorized_report",
    "received": "validate",
    "validated": "reconcile",
    "reconciled": "calculate",
    "calculated": "verify",
    "publication_ready": "publish",
    "published": "confirm_read_back",
    "verified_complete": None,
    "waiting_for_machine_data": "await_machine_data",
    "recovering": "recover",
    "unresolved": "await_authorized_correction",
    "disabled": None,
}


def presentation_status(*, automation_status: str, business_status: str) -> PresentationStatus:
    """Roll up the separate business/automation statuses to one customer-visible outcome.

    Only ``verified_complete`` is COMPLETE; everything else that is still moving is
    IN_PROGRESS, an abstention is UNRESOLVED, and a stalled/awaiting case is shown as such.
    The default is never COMPLETE (blueprint 25)."""
    if automation_status == "disabled":
        return PresentationStatus.DISABLED
    if business_status == "unresolved" or automation_status == "unresolved":
        return PresentationStatus.UNRESOLVED
    if automation_status == "recovering":
        return PresentationStatus.DEGRADED
    if automation_status in _AWAITING:
        return PresentationStatus.AWAITING_DATA
    if automation_status == "verified_complete":
        return PresentationStatus.COMPLETE
    if automation_status in _IN_PROGRESS:
        return PresentationStatus.IN_PROGRESS
    return PresentationStatus.IN_PROGRESS


def next_machine_step(automation_status: str) -> str | None:
    return _NEXT_STEP.get(automation_status, "await_machine_data")


def is_completed(status: PresentationStatus) -> bool:
    return status is PresentationStatus.COMPLETE


# --- Value-origin labelling and result shaping -------------------------------------
# Each material value is labelled reported / calculated / assumed (blueprint 09). The
# WO3 result payload carries reported inputs (actual, planned) and calculated outputs
# (delta, percent); nothing here is assumed, but the labeller supports it explicitly.

_ORIGIN_BY_FIELD = {
    "actual": "reported",
    "planned": "reported",
    "delta": "calculated",
    "delta_unrounded": "calculated",
    "percent": "calculated",
    "percent_unrounded": "calculated",
}


@dataclass(frozen=True)
class MaterialValue:
    name: str
    value: str
    origin: str

    def render(self) -> dict[str, str]:
        return {"name": self.name, "value": self.value, "origin": self.origin}


@dataclass(frozen=True)
class VesselResultView:
    fuel_grade: str
    unit: str | None
    period_start: str | None
    period_end: str | None
    status: str
    values: tuple[MaterialValue, ...]
    reason: str | None = None

    def render(self) -> dict[str, object]:
        return {
            "fuel_grade": self.fuel_grade,
            "unit": self.unit,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "status": self.status,
            "reason": self.reason,
            "values": [value.render() for value in self.values],
        }


def label_variances(
    variances: Sequence[dict[str, object]],
) -> tuple[tuple[VesselResultView, ...], tuple[str, ...]]:
    """Turn WO3 variance entries into labelled material values and the unresolved-field list.

    A reconciled entry exposes reported inputs and calculated outputs; an unresolved entry
    contributes a visible unresolved field and never a value pretending to be a result."""
    views: list[VesselResultView] = []
    unresolved: list[str] = []
    for entry in variances:
        grade = str(entry.get("fuel_grade", ""))
        status = str(entry.get("status", ""))
        if status != "reconciled":
            reason = entry.get("reason")
            unresolved.append(grade or "unknown")
            views.append(
                VesselResultView(
                    fuel_grade=grade,
                    unit=None,
                    period_start=None,
                    period_end=None,
                    status=status or "unresolved",
                    values=(),
                    reason=None if reason is None else str(reason),
                )
            )
            continue
        values: list[MaterialValue] = []
        for field_name, origin in _ORIGIN_BY_FIELD.items():
            if field_name in ("delta_unrounded", "percent_unrounded"):
                continue
            raw = entry.get(field_name)
            if raw is None:
                continue
            values.append(MaterialValue(name=field_name, value=str(raw), origin=origin))
        unit = entry.get("unit")
        start = entry.get("period_start")
        end = entry.get("period_end")
        views.append(
            VesselResultView(
                fuel_grade=grade,
                unit=None if unit is None else str(unit),
                period_start=None if start is None else str(start),
                period_end=None if end is None else str(end),
                status="reconciled",
                values=tuple(values),
            )
        )
    return tuple(views), tuple(unresolved)


# --- Fleet total (compatible quantities only) --------------------------------------


@dataclass(frozen=True)
class FleetTotalGroup:
    fuel_grade: str
    unit: str
    total_delta: str
    vessel_count: int

    def render(self) -> dict[str, object]:
        return {
            "fuel_grade": self.fuel_grade,
            "unit": self.unit,
            "total_delta": self.total_delta,
            "vessel_count": self.vessel_count,
        }


@dataclass(frozen=True)
class FleetTotal:
    groups: tuple[FleetTotalGroup, ...]
    excluded_unresolved: int
    missing_reports: int

    @property
    def complete(self) -> bool:
        """A fleet total is complete only when nothing was excluded or missing; an
        incomplete aggregate is never presented as a complete fleet result (blueprint 09)."""
        return self.excluded_unresolved == 0 and self.missing_reports == 0

    def render(self) -> dict[str, object]:
        return {
            "complete": self.complete,
            "groups": [group.render() for group in self.groups],
            "excluded_unresolved": self.excluded_unresolved,
            "missing_reports": self.missing_reports,
        }


def build_fleet_total(
    reconciled: Sequence[tuple[str, str, Decimal]],
    *,
    excluded_unresolved: int,
    missing_reports: int,
) -> FleetTotal:
    """Sum deltas only within a (fuel_grade, unit) group; grades/units are never mixed."""
    totals: dict[tuple[str, str], Decimal] = {}
    counts: dict[tuple[str, str], int] = {}
    for grade, unit, delta in reconciled:
        key = (grade, unit)
        totals[key] = totals.get(key, Decimal("0")) + delta
        counts[key] = counts.get(key, 0) + 1
    groups = tuple(
        FleetTotalGroup(
            fuel_grade=g, unit=u, total_delta=str(totals[(g, u)]), vessel_count=counts[(g, u)]
        )
        for (g, u) in sorted(totals)
    )
    return FleetTotal(
        groups=groups,
        excluded_unresolved=excluded_unresolved,
        missing_reports=missing_reports,
    )


def daily_report_empty_state(
    *,
    expected_jobs: int,
    feed_authorized: bool,
    feed_delayed: bool,
    service_disabled: bool,
    invalid_inputs: bool,
    calculations_unavailable: bool,
    arrived_jobs: int,
    complete_jobs: int,
    variance_count: int,
) -> EmptyState:
    """Pick the empty state for a period, keeping the reassuring message honest.

    "No reported variances" is only valid when every expected report arrived and every
    plan reconciled successfully with zero differences. An empty database, an unauthorised
    or delayed feed, invalid inputs or a disabled service each get their own state so an
    empty result is never mistaken for a clean one (blueprint 09, 25)."""
    if service_disabled:
        return EmptyState.DISABLED_SERVICE
    if not feed_authorized:
        return EmptyState.NO_AUTHORIZED_FEED
    if expected_jobs == 0:
        return EmptyState.NO_EXPECTED_JOBS
    if invalid_inputs:
        return EmptyState.INVALID_INPUTS
    if calculations_unavailable:
        return EmptyState.CALCULATIONS_UNAVAILABLE
    if feed_delayed or arrived_jobs < expected_jobs:
        return EmptyState.FEED_DELAYED
    if complete_jobs == expected_jobs and variance_count == 0:
        return EmptyState.NO_RELEVANT_DIFFERENCES
    return EmptyState.NONE


__all__ = [
    "CUSTOMER_ACTIONS",
    "READ_MODEL_VERSION",
    "EmptyState",
    "EvidenceStatus",
    "FleetTotal",
    "FleetTotalGroup",
    "MaterialValue",
    "PresentationStatus",
    "VesselResultView",
    "build_fleet_total",
    "daily_report_empty_state",
    "is_completed",
    "label_variances",
    "next_machine_step",
    "presentation_status",
]
