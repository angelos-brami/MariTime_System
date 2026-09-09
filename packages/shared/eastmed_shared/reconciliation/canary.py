"""Canary release policy and configuration package (work order 9, sections 28, 29).

A canary is admitted only after the schema, arithmetic, isolation, authority, durability
and fault prerequisites pass — it is not full-service qualification, and the 30-day and
commercial gates come later (blueprint 28). A canary has an explicit tenant set, workflow
set, source set, write limit and automatic rollback conditions; anything outside that
scope is refused. Objective regression against the release thresholds triggers the
``rollback_canary`` runbook.

The configuration package separates feature availability from authority: an enabled flag
alone permits no action (blueprint 29). ``action_permitted`` requires both the flag and a
real authority decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from eastmed_shared.reconciliation.release import (
    ReleaseMetrics,
    ReleaseThresholds,
    evaluate_release,
)


class CanaryPrereq(StrEnum):
    SCHEMA = "schema"
    ARITHMETIC = "arithmetic"
    ISOLATION = "isolation"
    AUTHORITY = "authority"
    DURABILITY = "durability"
    FAULT = "fault"


# All six must pass before a canary may be admitted (blueprint 28 deployment order).
REQUIRED_PREREQS: frozenset[CanaryPrereq] = frozenset(CanaryPrereq)

ROLLBACK_CANARY_RUNBOOK = "rollback_canary"


@dataclass(frozen=True)
class CanaryManifest:
    tenants: frozenset[str]
    workflows: frozenset[str]
    sources: frozenset[str]
    max_writes: int
    qualified_manifest_id: str
    rollback_runbook_id: str = ROLLBACK_CANARY_RUNBOOK


@dataclass(frozen=True)
class CanaryAdmission:
    admitted: bool
    reason: str
    missing_prereqs: tuple[str, ...] = ()


def canary_admission(
    manifest: CanaryManifest,
    *,
    passed_prereqs: frozenset[CanaryPrereq],
    release_arithmetic_ok: bool,
) -> CanaryAdmission:
    """Admit a canary only when every deployment prerequisite has passed. A canary is a
    tightly scoped trial, not full qualification (blueprint 28)."""
    missing = REQUIRED_PREREQS - passed_prereqs
    if missing:
        return CanaryAdmission(
            admitted=False,
            reason="deployment prerequisites incomplete",
            missing_prereqs=tuple(sorted(p.value for p in missing)),
        )
    if not release_arithmetic_ok:
        return CanaryAdmission(admitted=False, reason="independent arithmetic fixtures must pass")
    if manifest.max_writes < 0:
        return CanaryAdmission(admitted=False, reason="write limit must be non-negative")
    return CanaryAdmission(admitted=True, reason="canary admitted within its declared scope")


def canary_scope_ok(
    manifest: CanaryManifest,
    *,
    tenant: str,
    workflow: str,
    source: str,
    writes_used: int,
) -> bool:
    """A canary acts only within its explicit tenant/workflow/source set and write limit."""
    return (
        tenant in manifest.tenants
        and workflow in manifest.workflows
        and source in manifest.sources
        and 0 <= writes_used <= manifest.max_writes
    )


@dataclass(frozen=True)
class RollbackDecision:
    rollback: bool
    runbook_id: str | None
    reason: str


def check_canary_regression(
    metrics: ReleaseMetrics, thresholds: ReleaseThresholds | None = None
) -> RollbackDecision:
    """Objective regression against the release thresholds triggers rollback_canary."""
    verdict = evaluate_release(metrics, thresholds)
    if verdict.passed:
        return RollbackDecision(rollback=False, runbook_id=None, reason="within release thresholds")
    failed = ", ".join(c.name for c in verdict.checks if not c.passed)
    return RollbackDecision(
        rollback=True,
        runbook_id=ROLLBACK_CANARY_RUNBOOK,
        reason=f"objective regression: {failed}",
    )


@dataclass(frozen=True)
class ConfigPackage:
    operations_enabled: bool
    operations_effects_enabled: bool
    qualified_manifest_id: str
    allowed_connector_ids: frozenset[str]
    per_case_budget: int
    concurrent_attempt_cap: int
    analysis_deadline_seconds: int
    data_wait_deadline_seconds: int
    outbox_lease_seconds: int
    recovery_runbook_version: str


def action_permitted(config: ConfigPackage, *, effect: bool, authority_granted: bool) -> bool:
    """Feature availability is not authority: an enabled flag alone permits no action.

    The operations feature must be enabled, effects require the separate effects flag, and
    in every case an actual authority decision (work order 6) must have granted it."""
    if not config.operations_enabled:
        return False
    if effect and not config.operations_effects_enabled:
        return False
    return authority_granted


__all__ = [
    "REQUIRED_PREREQS",
    "ROLLBACK_CANARY_RUNBOOK",
    "CanaryAdmission",
    "CanaryManifest",
    "CanaryPrereq",
    "ConfigPackage",
    "RollbackDecision",
    "action_permitted",
    "canary_admission",
    "canary_scope_ok",
    "check_canary_regression",
]
