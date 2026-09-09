"""Release-threshold evaluation and sealed evaluation (work order 9, sections 26, 28).

The whole scheduled workflow is scored against a *sealed* reference cohort — frozen
authorized references, their decision-time evidence and a prospective expected-job
manifest — that lives outside the agent's write access (blueprint 26). ``evaluate_release``
then checks the predeclared gates (blueprint 28, 05): at least 90% correct-on-time C/N with
zero runtime interventions; a one-sided 95% exact binomial lower bound of at least 99% over
at least 300 independent held-out cases; p95 preparation latency under five minutes; intact
controls and independent arithmetic fixtures; measured benefit at least 3x price; and two
continuing paying pilots.

The binomial lower bound is the exact Clopper–Pearson bound (no external dependency); for
300 successes and no failures it returns ``0.05 ** (1/300)`` ≈ 0.9901, as the blueprint
states. Nothing here mutates the sealed manifest: it is a frozen value.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Numerical Recipes)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b): P(Beta(a, b) <= x)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(ln_beta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def clopper_pearson_lower(successes: int, n: int, *, confidence: float = 0.95) -> float:
    """One-sided lower bound of the true success rate (exact, Clopper–Pearson).

    Solves for the p at which P(X >= successes) = 1 - confidence, i.e. the alpha-quantile
    of Beta(successes, n - successes + 1). Zero successes give 0.0; all successes give
    ``alpha ** (1/n)``."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError("successes must be within [0, n]")
    alpha = 1.0 - confidence
    if successes == 0:
        return 0.0
    if successes == n:
        return float(alpha ** (1.0 / n))
    a = float(successes)
    b = float(n - successes + 1)
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        # P(X >= successes) = I_mid(successes, n - successes + 1)
        if regularized_incomplete_beta(a, b, mid) < alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile; None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[min(rank, len(ordered) - 1)]


@dataclass(frozen=True)
class ReleaseThresholds:
    min_completion_rate: float = 0.90
    max_runtime_interventions: int = 0
    min_precision_lower_bound: float = 0.99
    min_precision_cases: int = 300
    max_p95_latency_seconds: float = 300.0
    min_benefit_multiple: float = 3.0
    min_continuing_pilots: int = 2
    confidence: float = 0.95


@dataclass(frozen=True)
class ReleaseMetrics:
    expected_jobs: int
    correct_on_time: int
    runtime_interventions: int
    precision_successes: int
    precision_cases: int
    p95_latency_seconds: float
    arithmetic_fixtures_pass: bool
    controls_intact: bool
    benefit_multiple: float
    continuing_pilots: int

    @property
    def completion_rate(self) -> float:
        return self.correct_on_time / self.expected_jobs if self.expected_jobs else 0.0


@dataclass(frozen=True)
class ReleaseCheck:
    name: str
    passed: bool
    detail: str

    def render(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class ReleaseVerdict:
    passed: bool
    checks: tuple[ReleaseCheck, ...]
    precision_lower_bound: float

    def render(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "precision_lower_bound": self.precision_lower_bound,
            "checks": [c.render() for c in self.checks],
        }


def evaluate_release(
    metrics: ReleaseMetrics, thresholds: ReleaseThresholds | None = None
) -> ReleaseVerdict:
    """Evaluate every predeclared release gate; the release passes only if all do."""
    t = thresholds or ReleaseThresholds()
    checks: list[ReleaseCheck] = []

    rate = metrics.completion_rate
    checks.append(
        ReleaseCheck(
            "completion_rate",
            rate >= t.min_completion_rate,
            f"C/N={rate:.4f} vs >= {t.min_completion_rate}",
        )
    )
    checks.append(
        ReleaseCheck(
            "zero_runtime_interventions",
            metrics.runtime_interventions <= t.max_runtime_interventions,
            f"{metrics.runtime_interventions} interventions vs <= {t.max_runtime_interventions}",
        )
    )

    enough_cases = metrics.precision_cases >= t.min_precision_cases
    lower = (
        clopper_pearson_lower(
            metrics.precision_successes, metrics.precision_cases, confidence=t.confidence
        )
        if metrics.precision_cases > 0
        else 0.0
    )
    checks.append(
        ReleaseCheck(
            "precision_sample_size",
            enough_cases,
            f"{metrics.precision_cases} cases vs >= {t.min_precision_cases}",
        )
    )
    checks.append(
        ReleaseCheck(
            "precision_lower_bound",
            enough_cases and lower >= t.min_precision_lower_bound,
            f"lower={lower:.4f} vs >= {t.min_precision_lower_bound}",
        )
    )
    checks.append(
        ReleaseCheck(
            "p95_latency",
            metrics.p95_latency_seconds < t.max_p95_latency_seconds,
            f"p95={metrics.p95_latency_seconds:.1f}s vs < {t.max_p95_latency_seconds}s",
        )
    )
    checks.append(
        ReleaseCheck(
            "arithmetic_fixtures", metrics.arithmetic_fixtures_pass, "independent fixtures"
        )
    )
    checks.append(ReleaseCheck("controls_intact", metrics.controls_intact, "governance controls"))
    checks.append(
        ReleaseCheck(
            "measured_benefit",
            metrics.benefit_multiple >= t.min_benefit_multiple,
            f"{metrics.benefit_multiple:.2f}x vs >= {t.min_benefit_multiple}x",
        )
    )
    checks.append(
        ReleaseCheck(
            "continuing_pilots",
            metrics.continuing_pilots >= t.min_continuing_pilots,
            f"{metrics.continuing_pilots} pilots vs >= {t.min_continuing_pilots}",
        )
    )
    return ReleaseVerdict(
        passed=all(c.passed for c in checks), checks=tuple(checks), precision_lower_bound=lower
    )


@dataclass(frozen=True)
class SealedManifest:
    """A frozen reference cohort. Being a frozen dataclass of immutable members, a runtime
    agent cannot rewrite its own evaluation answer (blueprint 26)."""

    manifest_id: str
    reference_case_ids: tuple[str, ...]
    decision_time_evidence_hash: str
    expected_job_ids: tuple[str, ...]


def evaluate_sealed(
    manifest: SealedManifest,
    metrics: ReleaseMetrics,
    thresholds: ReleaseThresholds | None = None,
) -> ReleaseVerdict:
    """Score the sealed cohort against the release thresholds. The manifest is read-only
    input; this function never writes back to it."""
    return evaluate_release(metrics, thresholds)


__all__ = [
    "ReleaseCheck",
    "ReleaseMetrics",
    "ReleaseThresholds",
    "ReleaseVerdict",
    "SealedManifest",
    "clopper_pearson_lower",
    "evaluate_release",
    "evaluate_sealed",
    "percentile",
    "regularized_incomplete_beta",
]
