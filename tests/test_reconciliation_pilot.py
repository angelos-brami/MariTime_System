from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from eastmed_pipeline.reconciliation_pilot import PilotJob, run_pilot
from eastmed_schema.base import Base
from eastmed_schema.enums import AccountTier
from eastmed_schema.models import Account, ReconServicePrincipal, ReconStandingGrant
from eastmed_shared.reconciliation.pilot import GateStatus, GateTier, PilotReadiness
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

NOW = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
CAP = "operations.private_publish"
FP = "fp1"
PERIOD_START = "2026-09-07T00:00:00Z"
PERIOD_END = "2026-09-08T00:00:00Z"
PS = datetime(2026, 9, 7, tzinfo=UTC)
PE = datetime(2026, 9, 8, tzinfo=UTC)
CONTRACT = "fleet_reconciliation_v1"

# Every recon table (the pilot exercises the whole stack).
TABLES = [name for name in Base.metadata.tables if name.startswith("recon_")] + ["accounts"]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection: Any, _: Any) -> None:  # pragma: no cover - setup
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[Base.metadata.tables[n] for n in TABLES])
    with Session(engine) as active:
        yield active


def make_account(session: Session, *, with_authority: bool = True) -> UUID:
    account = Account(
        company="Design Partner", tier=AccountTier.DATA,
        contract_start=date(2026, 1, 1), contract_end=date(2027, 1, 1),
    )
    session.add(account)
    session.flush()
    if with_authority:
        session.add(
            ReconServicePrincipal(
                account_id=account.id, subject="svc-1", issuer="provisioning",
                audience="operations", allowed_scopes=[CAP], deployment_fingerprint=FP, active=True,
            )
        )
        session.add(
            ReconStandingGrant(
                account_id=account.id, policy_id="p1", policy_revision=1, capability=CAP,
                allowed_case_types=["daily_reconciliation"],
                allowed_destinations=["tenant_private_portal"],
                external_messages=False, financial_commitments=False,
                required_evidence=["required_inputs_complete", "accepted_facts"],
                required_artifact="reproducible_calculation_receipt",
                qualified_fingerprints=[FP], attestation_ttl_seconds=120, signing_key_id="key-1",
                valid_from=NOW - timedelta(hours=1), valid_to=NOW + timedelta(hours=1), active=True,
            )
        )
        session.flush()
    return account.id


def plan(vessel: str) -> dict[str, Any]:
    return {
        "schema_version": CONTRACT,
        "record_kind": "voyage_plan",
        "source_system": "planner",
        "external_ref": f"PLAN-{vessel}",
        "source_revision": "1",
        "vessel_ref": vessel,
        "recorded_at": "2026-09-06T00:00:00Z",
        "voyage_ref": f"VOY-{vessel}",
        "plan_revision": "rev-1",
        "effective_from": PERIOD_START,
        "effective_to": "2026-09-14T00:00:00Z",
        "items": [
            {"item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": "23.000", "unit": "tonne",
             "period_start": PERIOD_START, "period_end": PERIOD_END}
        ],
    }


def report(vessel: str, *, actual: str = "24.600", source_revision: str = "1") -> dict[str, Any]:
    return {
        "schema_version": CONTRACT,
        "record_kind": "daily_report",
        "source_system": "noon-connector",
        "external_ref": f"REP-{vessel}",
        "source_revision": source_revision,
        "vessel_ref": vessel,
        "recorded_at": "2026-09-08T06:00:00Z",
        "voyage_ref": f"VOY-{vessel}",
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "items": [
            {"item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": actual, "unit": "tonne"}
        ],
    }


def _feed() -> list[PilotJob]:
    jobs: list[PilotJob] = []
    for vessel in ("V1", "V2", "V3"):
        corrected = report(vessel, actual="23.600", source_revision="2") if vessel == "V1" else None
        jobs.append(
            PilotJob(
                vessel_ref=vessel,
                plan_payload=plan(vessel),
                report_payload=report(vessel),
                period_start=PS,
                period_end=PE,
                due_at=NOW + timedelta(minutes=5),
                corrected_report_payload=corrected,
            )
        )
    return jobs


def test_replay_exercises_components_without_claiming_runtime_readiness(session: Session) -> None:
    account_id = make_account(session)
    foreign = make_account(session, with_authority=False)

    result = run_pilot(
        session, account_id=account_id, principal_subject="svc-1", system_fingerprint=FP,
        jobs=_feed(), now=NOW, foreign_account_id=foreign,
    )

    # Every contracted job completed end-to-end with a confirmed read-back and one effect.
    assert result.verified_cases == 3
    assert result.published_effects == 3
    assert result.completion.expected_jobs == 3
    assert result.completion.correct_on_time_zero_touch == 3
    assert result.completion.completion_rate == 1.0

    acceptance = result.acceptance
    # The fixture clock proves ordering only, not the real latency prerequisite.
    assert acceptance.readiness is PilotReadiness.NOT_READY
    assert acceptance.qualified is False
    technical = {g.name: g for g in acceptance.gates_by_tier(GateTier.TECHNICAL)}
    assert technical["preparation_latency"].status is GateStatus.FAIL
    assert all(g.status is GateStatus.PASS for name, g in technical.items()
               if name != "preparation_latency")
    # The drills and control checks are backed by persisted records.
    assert technical["control_integrity"].status is GateStatus.PASS
    assert technical["correction_propagation"].status is GateStatus.PASS
    # Field/commercial gates remain honestly pending.
    assert all(
        g.status is GateStatus.PENDING_FIELD_EVIDENCE
        for g in acceptance.gates_by_tier(GateTier.FIELD)
    )
    # Simulated durations stay explicitly labeled and cannot pass the latency gate.
    assert acceptance.decision_record.evidence_kind == "synthetic_replay_simulated_clock"
    assert acceptance.decision_record.p95_latency_seconds is not None
    assert acceptance.decision_record.p95_latency_seconds < 300.0


def test_pilot_zero_runtime_interventions(session: Session) -> None:
    account_id = make_account(session)
    result = run_pilot(
        session, account_id=account_id, principal_subject="svc-1", system_fingerprint=FP,
        jobs=_feed(), now=NOW,
    )
    assert result.completion.human_touch_jobs == 0
    unattended = next(
        g for g in result.acceptance.gates if g.name == "unattended_operation_over_run"
    )
    assert unattended.status is GateStatus.PASS


def test_pilot_missing_report_lowers_completion(session: Session) -> None:
    account_id = make_account(session)
    jobs = _feed()
    # Drop V2's report: an expected job with no arrival stays counted as missing.
    jobs[1] = PilotJob(
        vessel_ref="V2", plan_payload=plan("V2"),
        report_payload={"schema_version": CONTRACT, "record_kind": "daily_report",
                        "source_system": "noon-connector", "external_ref": "REP-V2",
                        "source_revision": "1", "vessel_ref": "V2",
                        "recorded_at": "2026-09-08T06:00:00Z", "voyage_ref": "VOY-V2",
                        "period_start": PERIOD_START, "period_end": PERIOD_END,
                        "items": []},  # empty items -> rejected, no case
        period_start=PS, period_end=PE, due_at=NOW,
    )
    result = run_pilot(
        session, account_id=account_id, principal_subject="svc-1", system_fingerprint=FP,
        jobs=jobs, now=NOW,
    )
    assert result.completion.expected_jobs == 3
    assert result.completion.completion_rate < 1.0
    assert result.acceptance.readiness is PilotReadiness.NOT_READY


def test_denied_publication_is_not_counted_as_completed(session: Session) -> None:
    account_id = make_account(session, with_authority=False)
    result = run_pilot(
        session, account_id=account_id, principal_subject="svc-1", system_fingerprint=FP,
        jobs=_feed(), now=NOW,
    )
    assert result.published_effects == 0
    assert result.completion.correct_on_time_zero_touch == 0


def test_past_deadline_result_is_not_counted_as_on_time(session: Session) -> None:
    account_id = make_account(session)
    result = run_pilot(
        session, account_id=account_id, principal_subject="svc-1", system_fingerprint=FP,
        jobs=_feed(), now=NOW + timedelta(minutes=10),
    )
    assert result.completion.correct_on_time_zero_touch == 0
