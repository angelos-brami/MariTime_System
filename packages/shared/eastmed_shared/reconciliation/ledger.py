"""The expected-work ledger and its counting rules (blueprint 04 and 28).

The ledger is created from the standing service contract and fleet schedule *before*
reports arrive, so a scheduled but absent report is a job, not an invisible gap. The
denominator N is frozen from this ledger; amendments create effective-dated revisions
and cannot retroactively remove failed jobs from a benchmark cohort.

Headline completion is C/N (correct, on-time, zero human touches). Human-touch rate
is H/N. Arrived-record eligibility and eligible completion are diagnostics only, never
replacements for C/N. Each job carries exactly one outcome via an explicit precedence,
so one job is never counted as several failures.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from eastmed_schema.enums import AutomationStatus, BusinessStatus, JobOutcome, RecordKind

from eastmed_shared.reconciliation.contract import CONTRACT_VERSION

# BusinessStatus/AutomationStatus/JobOutcome are defined in eastmed_schema.enums and
# re-exported here so the ledger stays the single import site for counting callers.
__all__ = [
    "AutomationStatus",
    "BusinessStatus",
    "JobOutcome",
    "LedgerError",
    "ExpectedJob",
    "JobObservation",
    "CompletionSummary",
    "classify_outcome",
    "summarize_ledger",
]


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class ExpectedJob:
    """One unit of contracted analytical work, materialized before arrival."""

    expected_job_id: str
    tenant_id: str
    vessel_ref: str
    period_start: datetime
    period_end: datetime
    due_at: datetime
    contract_version: str = CONTRACT_VERSION
    result_contract_version: str = "result_contract_v1"
    required_record_kind: RecordKind = RecordKind.DAILY_REPORT


@dataclass(frozen=True)
class JobObservation:
    """The measured outcome of one expected job.

    ``human_intervention`` is an additional flag, not an outcome: any runtime human
    touch disqualifies unattended success even when the outcome is correct and on time.
    ``arrived`` records whether the required contracted record was received at all.
    """

    expected_job_id: str
    outcome: JobOutcome
    human_intervention: bool = False
    arrived: bool = False


def classify_outcome(
    *,
    supported: bool,
    arrived: bool,
    resolved: bool,
    correct: bool,
    on_time: bool,
    deadline_passed: bool,
) -> JobOutcome:
    """Map raw terminal signals to exactly one outcome under a fixed precedence.

    Precedence (first match wins) prevents a single job from being scored as several
    failures: unsupported > (missing | still-pending) > unresolved > incorrect >
    correct-late > correct-on-time.
    """
    if not supported:
        return JobOutcome.UNSUPPORTED
    if not arrived:
        return JobOutcome.MISSING if deadline_passed else JobOutcome.PENDING_BEFORE_DEADLINE
    if not resolved:
        return JobOutcome.UNRESOLVED if deadline_passed else JobOutcome.PENDING_BEFORE_DEADLINE
    if not correct:
        return JobOutcome.INCORRECT
    return JobOutcome.CORRECT_ON_TIME if on_time else JobOutcome.CORRECT_LATE


@dataclass(frozen=True)
class CompletionSummary:
    expected_jobs: int
    correct_on_time_zero_touch: int
    human_touch_jobs: int
    arrived_jobs: int
    out_of_contract_arrivals: int
    by_outcome: Mapping[JobOutcome, int]

    @property
    def completion_rate(self) -> float | None:
        """Headline C/N."""
        return self.correct_on_time_zero_touch / self.expected_jobs if self.expected_jobs else None

    @property
    def human_touch_rate(self) -> float | None:
        return self.human_touch_jobs / self.expected_jobs if self.expected_jobs else None

    @property
    def arrived_eligibility(self) -> float | None:
        """Diagnostic only: fraction of expected jobs whose record arrived."""
        return self.arrived_jobs / self.expected_jobs if self.expected_jobs else None

    @property
    def eligible_completion(self) -> float | None:
        """Diagnostic only: C among arrived jobs. Never a replacement for C/N."""
        return self.correct_on_time_zero_touch / self.arrived_jobs if self.arrived_jobs else None

    def render(self) -> dict[str, object]:
        return {
            "expected_jobs": self.expected_jobs,
            "correct_on_time_zero_touch": self.correct_on_time_zero_touch,
            "human_touch_jobs": self.human_touch_jobs,
            "out_of_contract_arrivals": self.out_of_contract_arrivals,
            "headline": {
                "completion_rate": self.completion_rate,
                "human_touch_rate": self.human_touch_rate,
            },
            "diagnostics": {
                "arrived_jobs": self.arrived_jobs,
                "arrived_eligibility": self.arrived_eligibility,
                "eligible_completion": self.eligible_completion,
            },
            "by_outcome": {outcome.value: count for outcome, count in self.by_outcome.items()},
        }


def summarize_ledger(
    expected_jobs: Sequence[ExpectedJob],
    observations: Sequence[JobObservation],
    *,
    out_of_contract_arrivals: int = 0,
) -> CompletionSummary:
    """Compute the frozen-cohort completion summary.

    Every expected job must have exactly one observation and every observation must
    reference an expected job: the cohort denominator is fully accounted, so nothing
    can quietly disappear from the success metric.
    """
    if out_of_contract_arrivals < 0:
        raise LedgerError("out_of_contract_arrivals cannot be negative")

    job_ids: set[str] = set()
    for job in expected_jobs:
        if job.expected_job_id in job_ids:
            raise LedgerError(f"duplicate expected_job_id {job.expected_job_id!r}")
        job_ids.add(job.expected_job_id)

    seen: set[str] = set()
    by_id: dict[str, JobObservation] = {}
    for observation in observations:
        if observation.expected_job_id not in job_ids:
            raise LedgerError(
                f"observation references unknown job {observation.expected_job_id!r}"
            )
        if observation.expected_job_id in seen:
            raise LedgerError(f"duplicate observation for {observation.expected_job_id!r}")
        seen.add(observation.expected_job_id)
        by_id[observation.expected_job_id] = observation

    unaccounted = job_ids - seen
    if unaccounted:
        raise LedgerError(f"expected jobs without an observation: {', '.join(sorted(unaccounted))}")

    counts: Counter[JobOutcome] = Counter()
    correct_zero_touch = 0
    human_touch = 0
    arrived = 0
    for job_id in job_ids:
        observation = by_id[job_id]
        counts[observation.outcome] += 1
        if observation.human_intervention:
            human_touch += 1
        if observation.arrived:
            arrived += 1
        if (
            observation.outcome is JobOutcome.CORRECT_ON_TIME
            and not observation.human_intervention
        ):
            correct_zero_touch += 1

    return CompletionSummary(
        expected_jobs=len(job_ids),
        correct_on_time_zero_touch=correct_zero_touch,
        human_touch_jobs=human_touch,
        arrived_jobs=arrived,
        out_of_contract_arrivals=out_of_contract_arrivals,
        by_outcome=dict(counts),
    )
