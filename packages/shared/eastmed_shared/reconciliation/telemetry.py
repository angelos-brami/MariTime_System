"""Operational telemetry aggregation (work order 9, section 27).

Emits low-cardinality aggregate metrics only — connector lag, task age, lease churn,
expected jobs, case completion and unresolved outcomes, p95 preparation latency, provider
acceptance/effect lag, correction propagation, cost reservations, disabled capabilities and
manual-touch events. Sensitive tenant/event detail stays in the authorized structured
records; it is never widened into a metric label. A ``TraceContext`` links one unit of work
across case_id, run_id, task_attempt_id and action_key (blueprint 27).

Pure aggregation: the pipeline gathers the snapshot from the database and calls
``build_metrics``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from eastmed_shared.reconciliation.release import percentile


@dataclass(frozen=True)
class TraceContext:
    case_id: str
    run_id: str
    task_attempt_id: str
    action_key: str

    def render(self) -> dict[str, str]:
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "task_attempt_id": self.task_attempt_id,
            "action_key": self.action_key,
        }


@dataclass(frozen=True)
class TelemetrySnapshot:
    connector_lag_seconds: Sequence[float] = field(default_factory=tuple)
    task_ages_seconds: Sequence[float] = field(default_factory=tuple)
    lease_epochs: Sequence[int] = field(default_factory=tuple)
    expected_jobs: int = 0
    completed_cases: int = 0
    unresolved_cases: int = 0
    prep_latencies_seconds: Sequence[float] = field(default_factory=tuple)
    provider_effect_lag_seconds: Sequence[float] = field(default_factory=tuple)
    correction_propagation_seconds: Sequence[float] = field(default_factory=tuple)
    cost_reservations: int = 0
    disabled_capabilities: Sequence[str] = field(default_factory=tuple)
    manual_touch_events: int | None = None


@dataclass(frozen=True)
class MetricSet:
    connector_lag_p95: float | None
    max_task_age: float | None
    lease_churn: int
    expected_jobs: int
    completed_cases: int
    unresolved_cases: int
    completion_rate: float | None
    prep_latency_p95: float | None
    provider_effect_lag_p95: float | None
    correction_propagation_p95: float | None
    cost_reservations: int
    disabled_capabilities: int
    manual_touch_events: int | None

    def render(self) -> dict[str, object]:
        return {
            "connector_lag_p95": self.connector_lag_p95,
            "max_task_age": self.max_task_age,
            "lease_churn": self.lease_churn,
            "expected_jobs": self.expected_jobs,
            "completed_cases": self.completed_cases,
            "unresolved_cases": self.unresolved_cases,
            "completion_rate": self.completion_rate,
            "prep_latency_p95": self.prep_latency_p95,
            "provider_effect_lag_p95": self.provider_effect_lag_p95,
            "correction_propagation_p95": self.correction_propagation_p95,
            "cost_reservations": self.cost_reservations,
            "disabled_capabilities": self.disabled_capabilities,
            "manual_touch_events": self.manual_touch_events,
        }


def _lease_churn(lease_epochs: Sequence[int]) -> int:
    """Total lease re-issuances: each epoch beyond the first for a task is one churn event."""
    return sum(max(0, epoch - 1) for epoch in lease_epochs)


def build_metrics(snapshot: TelemetrySnapshot) -> MetricSet:
    completion_rate = (
        snapshot.completed_cases / snapshot.expected_jobs if snapshot.expected_jobs else None
    )
    return MetricSet(
        connector_lag_p95=percentile(list(snapshot.connector_lag_seconds), 0.95),
        max_task_age=max(snapshot.task_ages_seconds) if snapshot.task_ages_seconds else None,
        lease_churn=_lease_churn(snapshot.lease_epochs),
        expected_jobs=snapshot.expected_jobs,
        completed_cases=snapshot.completed_cases,
        unresolved_cases=snapshot.unresolved_cases,
        completion_rate=completion_rate,
        prep_latency_p95=percentile(list(snapshot.prep_latencies_seconds), 0.95),
        provider_effect_lag_p95=percentile(list(snapshot.provider_effect_lag_seconds), 0.95),
        correction_propagation_p95=percentile(
            list(snapshot.correction_propagation_seconds), 0.95
        ),
        cost_reservations=snapshot.cost_reservations,
        disabled_capabilities=len(snapshot.disabled_capabilities),
        manual_touch_events=snapshot.manual_touch_events,
    )


__all__ = [
    "MetricSet",
    "TelemetrySnapshot",
    "TraceContext",
    "build_metrics",
]
