from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from eastmed_shared.reconciliation.ledger import (
    ExpectedJob,
    JobObservation,
    JobOutcome,
    LedgerError,
    classify_outcome,
    summarize_ledger,
)

BASE = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)


def job(job_id: str) -> ExpectedJob:
    return ExpectedJob(
        expected_job_id=job_id,
        tenant_id="tenant-1",
        vessel_ref=f"V-{job_id}",
        period_start=BASE,
        period_end=BASE + timedelta(days=1),
        due_at=BASE + timedelta(days=1, hours=6),
    )


def test_classify_outcome_precedence() -> None:
    kw = dict(arrived=True, resolved=True, correct=True, on_time=True, deadline_passed=True)
    assert classify_outcome(**{**kw, "supported": False}) is JobOutcome.UNSUPPORTED
    assert (
        classify_outcome(**{**kw, "supported": True, "arrived": False})
        is JobOutcome.MISSING
    )
    assert (
        classify_outcome(
            **{**kw, "supported": True, "arrived": False, "deadline_passed": False}
        )
        is JobOutcome.PENDING_BEFORE_DEADLINE
    )
    assert (
        classify_outcome(**{**kw, "supported": True, "resolved": False})
        is JobOutcome.UNRESOLVED
    )
    assert (
        classify_outcome(
            **{**kw, "supported": True, "resolved": False, "deadline_passed": False}
        )
        is JobOutcome.PENDING_BEFORE_DEADLINE
    )
    assert (
        classify_outcome(**{**kw, "supported": True, "correct": False})
        is JobOutcome.INCORRECT
    )
    assert classify_outcome(**{**kw, "supported": True}) is JobOutcome.CORRECT_ON_TIME
    assert (
        classify_outcome(**{**kw, "supported": True, "on_time": False})
        is JobOutcome.CORRECT_LATE
    )


def test_summary_headline_and_diagnostics() -> None:
    jobs = [job(f"j{i}") for i in range(4)]
    observations = [
        JobObservation("j0", JobOutcome.CORRECT_ON_TIME, human_intervention=False, arrived=True),
        JobObservation("j1", JobOutcome.CORRECT_ON_TIME, human_intervention=True, arrived=True),
        JobObservation("j2", JobOutcome.INCORRECT, arrived=True),
        JobObservation("j3", JobOutcome.MISSING, arrived=False),
    ]
    summary = summarize_ledger(jobs, observations)
    assert summary.expected_jobs == 4
    # Only j0 is correct/on-time AND zero-touch; j1 is disqualified by a human touch.
    assert summary.correct_on_time_zero_touch == 1
    assert summary.human_touch_jobs == 1
    assert summary.arrived_jobs == 3
    assert summary.completion_rate == 0.25
    assert summary.human_touch_rate == 0.25
    assert summary.arrived_eligibility == 0.75
    assert summary.eligible_completion == pytest.approx(1 / 3)
    assert summary.by_outcome[JobOutcome.CORRECT_ON_TIME] == 2


def test_human_touch_disqualifies_all_correct_cohort() -> None:
    jobs = [job("j0"), job("j1")]
    observations = [
        JobObservation("j0", JobOutcome.CORRECT_ON_TIME, human_intervention=True, arrived=True),
        JobObservation("j1", JobOutcome.CORRECT_ON_TIME, human_intervention=True, arrived=True),
    ]
    summary = summarize_ledger(jobs, observations)
    assert summary.completion_rate == 0.0
    assert summary.human_touch_rate == 1.0


def test_out_of_contract_arrivals_do_not_change_denominator() -> None:
    jobs = [job("j0")]
    observations = [JobObservation("j0", JobOutcome.CORRECT_ON_TIME, arrived=True)]
    summary = summarize_ledger(jobs, observations, out_of_contract_arrivals=5)
    assert summary.expected_jobs == 1
    assert summary.completion_rate == 1.0
    assert summary.out_of_contract_arrivals == 5


def test_duplicate_expected_job_id_is_error() -> None:
    with pytest.raises(LedgerError, match="duplicate expected_job_id"):
        summarize_ledger(
            [job("j0"), job("j0")],
            [JobObservation("j0", JobOutcome.MISSING)],
        )


def test_observation_for_unknown_job_is_error() -> None:
    with pytest.raises(LedgerError, match="unknown job"):
        summarize_ledger(
            [job("j0")],
            [
                JobObservation("j0", JobOutcome.MISSING),
                JobObservation("ghost", JobOutcome.MISSING),
            ],
        )


def test_expected_job_without_observation_is_error() -> None:
    with pytest.raises(LedgerError, match="without an observation"):
        summarize_ledger([job("j0"), job("j1")], [JobObservation("j0", JobOutcome.MISSING)])


def test_duplicate_observation_is_error() -> None:
    with pytest.raises(LedgerError, match="duplicate observation"):
        summarize_ledger(
            [job("j0")],
            [
                JobObservation("j0", JobOutcome.CORRECT_ON_TIME),
                JobObservation("j0", JobOutcome.INCORRECT),
            ],
        )


def test_negative_out_of_contract_is_error() -> None:
    with pytest.raises(LedgerError, match="cannot be negative"):
        summarize_ledger(
            [job("j0")],
            [JobObservation("j0", JobOutcome.CORRECT_ON_TIME)],
            out_of_contract_arrivals=-1,
        )
