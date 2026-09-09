"""Bounded recovery selection (blueprint work order 4, sections 12 and 24).

RecoverTask maps a typed failure plus its attempt history to a single allowed recovery
operation drawn from a fixed runbook allowlist, or to a terminal stop. It selects only
tested operations; it never invents a repair, never retries a refusal with the same
payload, and stops at the attempt limit. Unknown integrity failures disable the
affected capability rather than looping. The runbook engine that executes these
decisions is a later work order; this is the contract that gates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskFailureKind(StrEnum):
    TRANSIENT_PROVIDER = "transient_provider"
    MALFORMED_OUTPUT = "malformed_output"
    PROVIDER_OUTAGE = "provider_outage"
    REFUSAL = "refusal"
    MISSING_MACHINE_DATA = "missing_machine_data"
    INTEGRITY_FAILURE = "integrity_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"


class RecoveryAction(StrEnum):
    RETRY = "retry"
    FAILOVER_ALTERNATE = "failover_alternate"
    WAIT_FOR_MACHINE_DATA = "wait_for_machine_data"
    DISABLE_CAPABILITY = "disable_capability"
    STOP_UNRESOLVED = "stop_unresolved"


# Every action a recovery decision may name is a tested runbook on this allowlist. A
# stop is terminal and names no runbook. The controller rejects anything not here.
RUNBOOK_ALLOWLIST: frozenset[str] = frozenset(
    {
        "retry_same_stage",
        "failover_alternate_provider",
        "await_machine_data",
        "disable_capability",
    }
)

_ACTION_RUNBOOK: dict[RecoveryAction, str | None] = {
    RecoveryAction.RETRY: "retry_same_stage",
    RecoveryAction.FAILOVER_ALTERNATE: "failover_alternate_provider",
    RecoveryAction.WAIT_FOR_MACHINE_DATA: "await_machine_data",
    RecoveryAction.DISABLE_CAPABILITY: "disable_capability",
    RecoveryAction.STOP_UNRESOLVED: None,
}


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    runbook_id: str | None
    reason: str

    def __post_init__(self) -> None:
        if self.runbook_id is not None and self.runbook_id not in RUNBOOK_ALLOWLIST:
            raise ValueError(f"runbook {self.runbook_id!r} is not on the allowlist")


def _decision(action: RecoveryAction, reason: str) -> RecoveryDecision:
    return RecoveryDecision(action=action, runbook_id=_ACTION_RUNBOOK[action], reason=reason)


def select_recovery(
    failure: TaskFailureKind,
    *,
    attempts: int,
    max_attempts: int,
    alternate_available: bool = False,
    within_data_deadline: bool = True,
) -> RecoveryDecision:
    """Choose the one allowed next step for a typed failure.

    ``attempts`` is how many times this stage has already run. Transient and malformed
    failures retry until the attempt limit, then fail over if an alternate exists, else
    stop. A refusal or outage never retries the same payload. Missing machine data
    waits while inside its deadline and cannot be fabricated. An integrity failure
    disables the capability. Budget exhaustion stops."""
    if failure is TaskFailureKind.INTEGRITY_FAILURE:
        return _decision(RecoveryAction.DISABLE_CAPABILITY, "unknown integrity failure disables")
    if failure is TaskFailureKind.BUDGET_EXHAUSTED:
        return _decision(RecoveryAction.STOP_UNRESOLVED, "cost budget exhausted")
    if failure is TaskFailureKind.MISSING_MACHINE_DATA:
        if within_data_deadline:
            return _decision(RecoveryAction.WAIT_FOR_MACHINE_DATA, "await authorized machine data")
        return _decision(RecoveryAction.STOP_UNRESOLVED, "missing data past deadline")

    at_limit = attempts >= max_attempts

    if failure in (TaskFailureKind.TRANSIENT_PROVIDER, TaskFailureKind.MALFORMED_OUTPUT):
        if not at_limit:
            return _decision(RecoveryAction.RETRY, "retry transient/malformed within attempt limit")
        if alternate_available:
            return _decision(RecoveryAction.FAILOVER_ALTERNATE, "attempts exhausted; use alternate")
        return _decision(RecoveryAction.STOP_UNRESOLVED, "attempts exhausted; no alternate")

    # REFUSAL / PROVIDER_OUTAGE: never retry the same payload.
    if alternate_available and not at_limit:
        return _decision(RecoveryAction.FAILOVER_ALTERNATE, "select authorized alternate path")
    return _decision(RecoveryAction.STOP_UNRESOLVED, "no useful retry or alternate remains")
