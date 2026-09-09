from __future__ import annotations

from eastmed_shared.reconciliation.telemetry import (
    TelemetrySnapshot,
    TraceContext,
    build_metrics,
)


def test_trace_links_the_four_identities() -> None:
    trace = TraceContext(case_id="c1", run_id="r1", task_attempt_id="a1", action_key="k1")
    assert set(trace.render()) == {"case_id", "run_id", "task_attempt_id", "action_key"}


def test_build_metrics_aggregates_low_cardinality() -> None:
    snapshot = TelemetrySnapshot(
        connector_lag_seconds=[10.0, 20.0, 30.0],
        task_ages_seconds=[5.0, 60.0],
        lease_epochs=[1, 2, 3],  # churn = 0 + 1 + 2
        expected_jobs=10,
        completed_cases=9,
        unresolved_cases=1,
        prep_latencies_seconds=[100.0, 200.0],
        provider_effect_lag_seconds=[1.0, 2.0],
        correction_propagation_seconds=[3600.0],
        cost_reservations=4,
        disabled_capabilities=["operations.reconcile"],
        manual_touch_events=0,
    )
    metrics = build_metrics(snapshot)
    assert metrics.lease_churn == 3
    assert metrics.max_task_age == 60.0
    assert metrics.completion_rate == 0.9
    assert metrics.disabled_capabilities == 1
    assert metrics.connector_lag_p95 == 30.0
    rendered = metrics.render()
    # Low-cardinality: aggregate scalars only, no tenant/event identifiers.
    assert "tenant" not in rendered and "case_id" not in rendered


def test_build_metrics_handles_empty_snapshot() -> None:
    metrics = build_metrics(TelemetrySnapshot())
    assert metrics.completion_rate is None
    assert metrics.connector_lag_p95 is None
    assert metrics.max_task_age is None
    assert metrics.lease_churn == 0
