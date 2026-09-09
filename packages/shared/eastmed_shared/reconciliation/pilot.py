"""Autonomous pilot acceptance (work order 10, sections 05, 28, 32).

The final work order runs an authorized feed through the whole pipeline over a measured
window and evaluates the section-05 release gates against the *observed* results. It is
deliberately honest about what a single measured run can and cannot establish
(blueprint 05, 33): "A single successful demonstration does not qualify the service."

So the gates are split into two tiers:

* TECHNICAL gates are establishable within the measured run — zero runtime interventions
  over the run, C/N completion from the frozen expected-work ledger, independent
  calculation correctness, control integrity (one logical effect, worker recovery,
  read-back, no cross-tenant access), preparation latency, correction propagation and
  intact expected-job accounting.
* FIELD gates require real-world trial and commercial evidence — a completed 30 consecutive
  unattended days, at least 300 independent held-out cases at a 99% lower bound, at least
  3x measured benefit and two continuing paying pilots. A synthetic run cannot supply these,
  so they read ``PENDING_FIELD_EVIDENCE`` rather than being fabricated into a pass.

``evaluate_pilot`` therefore returns ``TECHNICAL_PREREQS_MET`` (the restricted-pilot-grant
state) when every technical gate passes but field evidence is still pending — never
``QUALIFIED`` from one run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from eastmed_shared.reconciliation.release import ReleaseThresholds, clopper_pearson_lower


class GateTier(StrEnum):
    TECHNICAL = "technical"
    FIELD = "field"


class GateStatus(StrEnum):
    PASS = "pass"  # noqa: S105 - a gate outcome, not a secret
    FAIL = "fail"
    PENDING_FIELD_EVIDENCE = "pending_field_evidence"


class PilotReadiness(StrEnum):
    QUALIFIED = "qualified"
    TECHNICAL_PREREQS_MET = "technical_prereqs_met"
    NOT_READY = "not_ready"


ACCEPTANCE_CAVEAT = (
    "A single successful demonstration does not qualify the service or establish market "
    "leadership. Field gates require a completed 30-day unattended trial, at least 300 "
    "independent held-out cases, at least 3x measured benefit and two continuing paying "
    "pilots (blueprint 05, 33)."
)


@dataclass(frozen=True)
class PilotObservations:
    # Measured within the run.
    expected_jobs: int
    correct_on_time: int
    incorrect: int
    unresolved: int
    missing: int
    human_interventions: int
    arithmetic_fixtures_pass: bool
    control_integrity_pass: bool
    one_logical_effect: bool
    worker_recovery_ok: bool
    correction_propagation_ok: bool
    read_back_confirmed: bool
    p95_latency_seconds: float
    # Field / commercial evidence (0 or below-threshold from a synthetic run).
    observed_consecutive_days: int = 0
    independent_precision_cases: int = 0
    precision_successes: int = 0
    benefit_multiple: float = 0.0
    continuing_paying_pilots: int = 0
    correct_late: int = 0
    unsupported: int = 0
    latency_measured: bool = True

    @property
    def accounted_jobs(self) -> int:
        return (
            self.correct_on_time + self.correct_late + self.incorrect
            + self.unresolved + self.missing + self.unsupported
        )

    @property
    def completion_rate(self) -> float:
        return self.correct_on_time / self.expected_jobs if self.expected_jobs else 0.0

    @property
    def human_touch_rate(self) -> float:
        return self.human_interventions / self.expected_jobs if self.expected_jobs else 0.0


@dataclass(frozen=True)
class DecisionRecord:
    expected_jobs: int
    completion_rate: float
    human_touch_rate: float
    incorrect: int
    unresolved: int
    missing: int
    p50_latency_seconds: float | None
    p95_latency_seconds: float | None
    source_delay_p95_seconds: float | None
    correction_propagation_ok: bool
    cost_per_correct_result: float | None
    setup_effort_hours: float | None
    measured_benefit_multiple: float
    evidence_kind: str = "observed"
    correct_late: int = 0
    unsupported: int = 0

    def render(self) -> dict[str, object]:
        return {
            "expected_jobs": self.expected_jobs,
            "completion_rate": self.completion_rate,
            "human_touch_rate": self.human_touch_rate,
            "incorrect": self.incorrect,
            "unresolved": self.unresolved,
            "missing": self.missing,
            "correct_late": self.correct_late,
            "unsupported": self.unsupported,
            "evidence_kind": self.evidence_kind,
            "latency": {
                "p50_seconds": self.p50_latency_seconds,
                "p95_seconds": self.p95_latency_seconds,
                "source_delay_p95_seconds": self.source_delay_p95_seconds,
            },
            "correction_propagation_ok": self.correction_propagation_ok,
            "cost_per_correct_result": self.cost_per_correct_result,
            "setup_effort_hours": self.setup_effort_hours,
            "measured_benefit_multiple": self.measured_benefit_multiple,
        }


@dataclass(frozen=True)
class PilotGate:
    name: str
    tier: GateTier
    status: GateStatus
    detail: str

    def render(self) -> dict[str, object]:
        return {
            "name": self.name,
            "tier": self.tier.value,
            "status": self.status.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PilotAcceptance:
    readiness: PilotReadiness
    gates: tuple[PilotGate, ...]
    decision_record: DecisionRecord
    precision_lower_bound: float
    caveat: str = ACCEPTANCE_CAVEAT

    @property
    def qualified(self) -> bool:
        return self.readiness is PilotReadiness.QUALIFIED

    def gates_by_tier(self, tier: GateTier) -> tuple[PilotGate, ...]:
        return tuple(g for g in self.gates if g.tier is tier)

    def render(self) -> dict[str, object]:
        return {
            "readiness": self.readiness.value,
            "precision_lower_bound": self.precision_lower_bound,
            "gates": [g.render() for g in self.gates],
            "decision_record": self.decision_record.render(),
            "caveat": self.caveat,
        }


def _tech(name: str, passed: bool, detail: str) -> PilotGate:
    return PilotGate(
        name=name,
        tier=GateTier.TECHNICAL,
        status=GateStatus.PASS if passed else GateStatus.FAIL,
        detail=detail,
    )


def _field(name: str, passed: bool, detail: str) -> PilotGate:
    # Absent or below-threshold field evidence is not a failure; it is simply not yet
    # established. Only a met threshold is a pass.
    return PilotGate(
        name=name,
        tier=GateTier.FIELD,
        status=GateStatus.PASS if passed else GateStatus.PENDING_FIELD_EVIDENCE,
        detail=detail,
    )


def evaluate_pilot(
    obs: PilotObservations,
    decision: DecisionRecord,
    thresholds: ReleaseThresholds | None = None,
) -> PilotAcceptance:
    """Evaluate the section-05 gates against the observed run, honestly separating the
    technical prerequisites from the field/commercial evidence."""
    t = thresholds or ReleaseThresholds()
    lower = (
        clopper_pearson_lower(obs.precision_successes, obs.independent_precision_cases)
        if obs.independent_precision_cases > 0
        else 0.0
    )

    gates: list[PilotGate] = [
        _tech(
            "unattended_operation_over_run",
            obs.human_interventions <= t.max_runtime_interventions,
            f"{obs.human_interventions} runtime interventions in the measured run",
        ),
        _tech(
            "analytical_completion",
            obs.completion_rate >= t.min_completion_rate,
            f"C/N={obs.completion_rate:.4f} vs >= {t.min_completion_rate}",
        ),
        _tech(
            "expected_job_accounting",
            obs.accounted_jobs == obs.expected_jobs,
            f"{obs.accounted_jobs} accounted of {obs.expected_jobs} expected",
        ),
        _tech(
            "calculation_correctness", obs.arithmetic_fixtures_pass, "independent fixtures match"
        ),
        _tech(
            "control_integrity",
            obs.control_integrity_pass
            and obs.one_logical_effect
            and obs.worker_recovery_ok
            and obs.read_back_confirmed,
            "tenant/authority/retry/crash scenarios, one logical effect, read-back",
        ),
        _tech(
            "preparation_latency",
            obs.latency_measured and obs.p95_latency_seconds < t.max_p95_latency_seconds,
            (f"p95={obs.p95_latency_seconds:.1f}s vs < {t.max_p95_latency_seconds}s"
             if obs.latency_measured else "real processing latency not measured"),
        ),
        _tech(
            "correction_propagation", obs.correction_propagation_ok, "supersession + invalidation"
        ),
        _field(
            "unattended_30_consecutive_days",
            obs.observed_consecutive_days >= 30,
            f"{obs.observed_consecutive_days} consecutive days observed vs >= 30",
        ),
        _field(
            "routine_result_precision",
            obs.independent_precision_cases >= t.min_precision_cases
            and lower >= t.min_precision_lower_bound,
            f"{obs.independent_precision_cases} independent cases, lower bound {lower:.4f}",
        ),
        _field(
            "commercial_benefit",
            obs.benefit_multiple >= t.min_benefit_multiple,
            f"{obs.benefit_multiple:.2f}x measured benefit vs >= {t.min_benefit_multiple}x",
        ),
        _field(
            "two_paying_pilots",
            obs.continuing_paying_pilots >= t.min_continuing_pilots,
            f"{obs.continuing_paying_pilots} continuing paying pilots vs >= "
            f"{t.min_continuing_pilots}",
        ),
    ]

    technical = [g for g in gates if g.tier is GateTier.TECHNICAL]
    field_gates = [g for g in gates if g.tier is GateTier.FIELD]
    if any(g.status is GateStatus.FAIL for g in technical):
        readiness = PilotReadiness.NOT_READY
    elif all(g.status is GateStatus.PASS for g in field_gates):
        readiness = PilotReadiness.QUALIFIED
    else:
        readiness = PilotReadiness.TECHNICAL_PREREQS_MET

    return PilotAcceptance(
        readiness=readiness,
        gates=tuple(gates),
        decision_record=decision,
        precision_lower_bound=lower,
    )


__all__ = [
    "ACCEPTANCE_CAVEAT",
    "DecisionRecord",
    "GateStatus",
    "GateTier",
    "PilotAcceptance",
    "PilotGate",
    "PilotObservations",
    "PilotReadiness",
    "evaluate_pilot",
]
