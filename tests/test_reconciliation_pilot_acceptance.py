from __future__ import annotations

from typing import Any

from eastmed_shared.reconciliation.pilot import (
    ACCEPTANCE_CAVEAT,
    DecisionRecord,
    GateStatus,
    GateTier,
    PilotObservations,
    PilotReadiness,
    evaluate_pilot,
)


def _obs(**kw: Any) -> PilotObservations:
    base: dict[str, Any] = dict(
        expected_jobs=10,
        correct_on_time=10,
        incorrect=0,
        unresolved=0,
        missing=0,
        human_interventions=0,
        arithmetic_fixtures_pass=True,
        control_integrity_pass=True,
        one_logical_effect=True,
        worker_recovery_ok=True,
        correction_propagation_ok=True,
        read_back_confirmed=True,
        p95_latency_seconds=60.0,
    )
    base.update(kw)
    return PilotObservations(**base)


def _decision(obs: PilotObservations) -> DecisionRecord:
    return DecisionRecord(
        expected_jobs=obs.expected_jobs,
        completion_rate=obs.completion_rate,
        human_touch_rate=obs.human_touch_rate,
        incorrect=obs.incorrect,
        unresolved=obs.unresolved,
        missing=obs.missing,
        p50_latency_seconds=50.0,
        p95_latency_seconds=obs.p95_latency_seconds,
        source_delay_p95_seconds=None,
        correction_propagation_ok=obs.correction_propagation_ok,
        cost_per_correct_result=None,
        setup_effort_hours=None,
        measured_benefit_multiple=obs.benefit_multiple,
    )


def test_single_run_is_technical_prereqs_met_not_qualified() -> None:
    obs = _obs()  # no field evidence supplied
    acceptance = evaluate_pilot(obs, _decision(obs))
    assert acceptance.readiness is PilotReadiness.TECHNICAL_PREREQS_MET
    assert acceptance.qualified is False
    # Every technical gate passes; every field gate is pending, never fabricated.
    assert all(g.status is GateStatus.PASS for g in acceptance.gates_by_tier(GateTier.TECHNICAL))
    assert all(
        g.status is GateStatus.PENDING_FIELD_EVIDENCE
        for g in acceptance.gates_by_tier(GateTier.FIELD)
    )
    assert acceptance.caveat == ACCEPTANCE_CAVEAT


def test_full_field_evidence_qualifies() -> None:
    obs = _obs(
        observed_consecutive_days=30,
        independent_precision_cases=300,
        precision_successes=300,
        benefit_multiple=3.5,
        continuing_paying_pilots=2,
    )
    acceptance = evaluate_pilot(obs, _decision(obs))
    assert acceptance.readiness is PilotReadiness.QUALIFIED
    assert acceptance.qualified is True
    assert acceptance.precision_lower_bound >= 0.99


def test_a_runtime_intervention_makes_it_not_ready() -> None:
    obs = _obs(human_interventions=1)
    acceptance = evaluate_pilot(obs, _decision(obs))
    assert acceptance.readiness is PilotReadiness.NOT_READY
    failing = {g.name for g in acceptance.gates if g.status is GateStatus.FAIL}
    assert "unattended_operation_over_run" in failing


def test_below_completion_gate_is_not_ready() -> None:
    obs = _obs(expected_jobs=10, correct_on_time=8, unresolved=2)
    acceptance = evaluate_pilot(obs, _decision(obs))
    assert acceptance.readiness is PilotReadiness.NOT_READY
    failing = {g.name for g in acceptance.gates if g.status is GateStatus.FAIL}
    assert "analytical_completion" in failing


def test_broken_control_integrity_is_not_ready() -> None:
    obs = _obs(worker_recovery_ok=False)
    acceptance = evaluate_pilot(obs, _decision(obs))
    assert acceptance.readiness is PilotReadiness.NOT_READY
    failing = {g.name for g in acceptance.gates if g.status is GateStatus.FAIL}
    assert "control_integrity" in failing


def test_measured_but_insufficient_field_evidence_stays_pending() -> None:
    # Fewer than 300 cases: precision cannot be established, so it stays pending (not fail).
    obs = _obs(independent_precision_cases=50, precision_successes=50)
    acceptance = evaluate_pilot(obs, _decision(obs))
    precision = next(g for g in acceptance.gates if g.name == "routine_result_precision")
    assert precision.status is GateStatus.PENDING_FIELD_EVIDENCE
    assert acceptance.readiness is PilotReadiness.TECHNICAL_PREREQS_MET
