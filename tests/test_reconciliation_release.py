from __future__ import annotations

import dataclasses

import pytest
from eastmed_shared.reconciliation.canary import (
    CanaryManifest,
    CanaryPrereq,
    ConfigPackage,
    action_permitted,
    canary_admission,
    canary_scope_ok,
    check_canary_regression,
)
from eastmed_shared.reconciliation.release import (
    ReleaseMetrics,
    SealedManifest,
    clopper_pearson_lower,
    evaluate_release,
    evaluate_sealed,
    percentile,
)


def _passing_metrics(**kw: object) -> ReleaseMetrics:
    base: dict[str, object] = dict(
        expected_jobs=100,
        correct_on_time=95,
        runtime_interventions=0,
        precision_successes=300,
        precision_cases=300,
        p95_latency_seconds=120.0,
        arithmetic_fixtures_pass=True,
        controls_intact=True,
        benefit_multiple=3.5,
        continuing_pilots=2,
    )
    base.update(kw)
    return ReleaseMetrics(**base)  # type: ignore[arg-type]


# --- Exact binomial lower bound -----------------------------------------------------


def test_clopper_pearson_matches_the_blueprint_closed_form() -> None:
    # 300 successes, no failures -> 0.05 ** (1/300) ~= 0.9901 (blueprint 28).
    bound = clopper_pearson_lower(300, 300, confidence=0.95)
    assert bound == pytest.approx(0.05 ** (1 / 300), abs=1e-6)
    assert bound == pytest.approx(0.9901, abs=1e-3)


def test_clopper_pearson_edges_and_monotonicity() -> None:
    assert clopper_pearson_lower(0, 10) == 0.0
    assert clopper_pearson_lower(299, 300) < clopper_pearson_lower(300, 300)
    # A known value: 8/10 lower bound ~ 0.4930 (Clopper-Pearson, 95% one-sided).
    assert clopper_pearson_lower(8, 10) == pytest.approx(0.4930, abs=1e-3)


def test_percentile() -> None:
    assert percentile([], 0.95) is None
    assert percentile([1.0], 0.95) == 1.0
    assert percentile([float(i) for i in range(1, 101)], 0.95) == 95.0


# --- Release thresholds -------------------------------------------------------------


def test_release_passes_when_all_gates_met() -> None:
    verdict = evaluate_release(_passing_metrics())
    assert verdict.passed is True
    assert verdict.precision_lower_bound >= 0.99


@pytest.mark.parametrize(
    ("override", "failed_check"),
    [
        (dict(correct_on_time=80), "completion_rate"),
        (dict(runtime_interventions=1), "zero_runtime_interventions"),
        (dict(precision_cases=299, precision_successes=299), "precision_sample_size"),
        (dict(precision_successes=280), "precision_lower_bound"),
        (dict(p95_latency_seconds=600.0), "p95_latency"),
        (dict(arithmetic_fixtures_pass=False), "arithmetic_fixtures"),
        (dict(controls_intact=False), "controls_intact"),
        (dict(benefit_multiple=2.0), "measured_benefit"),
        (dict(continuing_pilots=1), "continuing_pilots"),
    ],
)
def test_release_fails_when_any_gate_missed(override: dict[str, object], failed_check: str) -> None:
    verdict = evaluate_release(_passing_metrics(**override))
    assert verdict.passed is False
    failing = {c.name for c in verdict.checks if not c.passed}
    assert failed_check in failing


# --- Sealed evaluation --------------------------------------------------------------


def test_sealed_manifest_is_immutable() -> None:
    manifest = SealedManifest(
        manifest_id="ref-2026-day1-14",
        reference_case_ids=("c1", "c2"),
        decision_time_evidence_hash="h",
        expected_job_ids=("j1",),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        manifest.manifest_id = "tampered"  # type: ignore[misc]


def test_sealed_evaluation_matches_release_verdict() -> None:
    manifest = SealedManifest("m", ("c1",), "h", ("j1",))
    assert evaluate_sealed(manifest, _passing_metrics()).passed is True


# --- Canary policy ------------------------------------------------------------------


def _manifest() -> CanaryManifest:
    return CanaryManifest(
        tenants=frozenset({"t1"}),
        workflows=frozenset({"daily_reconciliation"}),
        sources=frozenset({"reporter"}),
        max_writes=5,
        qualified_manifest_id="manifest-1",
    )


def test_canary_admitted_only_after_all_prereqs() -> None:
    admitted = canary_admission(
        _manifest(), passed_prereqs=frozenset(CanaryPrereq), release_arithmetic_ok=True
    )
    assert admitted.admitted is True

    missing = canary_admission(
        _manifest(),
        passed_prereqs=frozenset(CanaryPrereq) - {CanaryPrereq.FAULT},
        release_arithmetic_ok=True,
    )
    assert missing.admitted is False
    assert "fault" in missing.missing_prereqs


def test_canary_scope_is_enforced() -> None:
    m = _manifest()
    assert canary_scope_ok(m, tenant="t1", workflow="daily_reconciliation", source="reporter",
                           writes_used=3) is True
    assert canary_scope_ok(m, tenant="other", workflow="daily_reconciliation", source="reporter",
                           writes_used=3) is False
    assert canary_scope_ok(m, tenant="t1", workflow="daily_reconciliation", source="reporter",
                           writes_used=6) is False


def test_regression_triggers_rollback_canary() -> None:
    ok = check_canary_regression(_passing_metrics())
    assert ok.rollback is False
    bad = check_canary_regression(_passing_metrics(correct_on_time=10))
    assert bad.rollback is True
    assert bad.runbook_id == "rollback_canary"


# --- Config package: feature availability is not authority --------------------------


def _config(**kw: object) -> ConfigPackage:
    base: dict[str, object] = dict(
        operations_enabled=True,
        operations_effects_enabled=False,
        qualified_manifest_id="m1",
        allowed_connector_ids=frozenset({"reporter"}),
        per_case_budget=10,
        concurrent_attempt_cap=2,
        analysis_deadline_seconds=300,
        data_wait_deadline_seconds=3600,
        outbox_lease_seconds=300,
        recovery_runbook_version="rb-1",
    )
    base.update(kw)
    return ConfigPackage(**base)  # type: ignore[arg-type]


def test_flag_alone_permits_no_action() -> None:
    # Feature disabled: nothing permitted even with authority.
    assert action_permitted(_config(operations_enabled=False), effect=False,
                            authority_granted=True) is False
    # Feature enabled but no authority: still not permitted.
    assert action_permitted(_config(), effect=False, authority_granted=False) is False
    # A private (non-effect) action needs the flag + authority.
    assert action_permitted(_config(), effect=False, authority_granted=True) is True
    # An external effect additionally needs the effects flag.
    assert action_permitted(_config(), effect=True, authority_granted=True) is False
    assert action_permitted(
        _config(operations_effects_enabled=True), effect=True, authority_granted=True
    ) is True
