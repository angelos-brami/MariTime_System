"""Local viewer for the private daily fleet reconciliation read model (dev tool).

This is NOT a shipped product UI. It seeds an in-memory SQLite database, drives a small
authorized fleet feed through the real pipeline (import -> reconcile -> calculate ->
verify -> publish -> read-back), then renders the customer-facing views produced by the
work-order-8 read model (`eastmed_pipeline.reconciliation_read`) as a high-end operations
console so you can see how the finished daily fleet report looks.

Beyond the static report it also drives the *real* backend live, on isolated tenants, so
nothing on the displayed report is ever mutated and no external effect is ever produced:

    /api/run    runs the whole work-order-10 pilot harness (`run_pilot`) end to end and
                returns the honest acceptance gates measured from the observed run.
    /api/drill  runs a real durable-runtime worker-recovery drill (a stale commit is
                fenced by lease epoch) and a real capability-stop (kill-switch) that makes
                an attestation DENY before any effect.

    python scripts/recon_viewer.py         # serve on http://127.0.0.1:8733
"""

from __future__ import annotations

import html
import json
import math
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from eastmed_pipeline.reconciliation_case import (
    commit_case_version,
    compute_case_result,
    record_verification_verdict,
)
from eastmed_pipeline.reconciliation_import import ImportStatus, import_record
from eastmed_pipeline.reconciliation_pilot import PilotJob, run_pilot
from eastmed_pipeline.reconciliation_publication import (
    propose_publication,
    publish_case,
    reconcile_publication,
)
from eastmed_pipeline.reconciliation_read import (
    get_case_timeline,
    get_case_view,
    get_daily_report,
    get_service_health,
)
from eastmed_pipeline.reconciliation_reliability import run_recover_expired_task
from eastmed_pipeline.reconciliation_runtime import (
    CommitStatus,
    claim_task,
    commit_stage,
    create_run,
    enqueue_task,
)
from eastmed_schema.base import Base
from eastmed_schema.enums import (
    AccountTier,
    AuthorityRole,
    AutomationStatus,
    BusinessStatus,
    RecordKind,
    WorkflowStage,
)
from eastmed_schema.models import (
    Account,
    ReconActionIntent,
    ReconExpectedJob,
    ReconObservation,
    ReconOperationalCase,
    ReconServicePrincipal,
    ReconSourceCapture,
    ReconStandingGrant,
    ReconVesselMembership,
)
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

PORT = 8733
CAP = "operations.private_publish"
FP = "fp1"
NOW = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
PS = datetime(2026, 9, 7, tzinfo=UTC)
PE = datetime(2026, 9, 8, tzinfo=UTC)
PERIOD_START = "2026-09-07T00:00:00Z"
PERIOD_END = "2026-09-08T00:00:00Z"
CONTRACT = "fleet_reconciliation_v1"

FLEET = [
    ("V1", "Atlantic Carrier", "24.600", "23.000"),
    ("V2", "Pacific Voyager", "30.000", "30.000"),
    ("V3", "Nordic Star", "18.400", "20.000"),
    ("V4", "Aegean Trader", "41.200", "39.500"),
    ("V5", "Baltic Pioneer", "27.300", None),  # no plan -> unresolved
]
NAMES = {ref: name for ref, name, *_ in FLEET}

# status -> (accent hex)
STATUS_ACCENT = {
    "complete": "#2ee6a6",
    "in_progress": "#5aa2ff",
    "unresolved": "#ffc24b",
    "awaiting_data": "#8a93a6",
    "degraded": "#ff9d4d",
    "disabled": "#ff5d73",
    "confirmed": "#2ee6a6",
    "pending": "#5aa2ff",
    "incomplete": "#ffc24b",
    "unavailable": "#ff5d73",
}


def _plan(vessel: str, planned: str) -> dict[str, Any]:
    return {
        "schema_version": CONTRACT, "record_kind": "voyage_plan", "source_system": "planner",
        "external_ref": f"PLAN-{vessel}", "source_revision": "1", "vessel_ref": vessel,
        "recorded_at": "2026-09-06T00:00:00Z", "voyage_ref": f"VOY-{vessel}",
        "plan_revision": "rev-1", "effective_from": PERIOD_START,
        "effective_to": "2026-09-14T00:00:00Z",
        "items": [{"item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": planned,
                   "unit": "tonne", "period_start": PERIOD_START, "period_end": PERIOD_END}],
    }


def _report(vessel: str, actual: str, source_revision: str = "1") -> dict[str, Any]:
    return {
        "schema_version": CONTRACT, "record_kind": "daily_report",
        "source_system": "noon-connector", "external_ref": f"REP-{vessel}",
        "source_revision": source_revision, "vessel_ref": vessel,
        "recorded_at": "2026-09-08T06:00:00Z",
        "voyage_ref": f"VOY-{vessel}", "period_start": PERIOD_START, "period_end": PERIOD_END,
        "items": [{"item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": actual,
                   "unit": "tonne"}],
    }


# --------------------------------------------------------------------------- environments


def _engine() -> Any:
    """A fresh in-memory database with the recon_* tables and foreign keys enforced."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(conn: Any, _: Any) -> None:
        conn.execute("PRAGMA foreign_keys=ON")

    names = [n for n in Base.metadata.tables if n.startswith("recon_")] + ["accounts"]
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[n] for n in names])
    return engine


def _add_account(session: Session, company: str) -> UUID:
    account = Account(company=company, tier=AccountTier.DATA,
                      contract_start=date(2026, 1, 1), contract_end=date(2027, 1, 1))
    session.add(account)
    session.flush()
    return account.id


def _grant_authority(session: Session, account_id: UUID) -> None:
    """Provision the service principal and standing grant exactly as controlled
    provisioning would (never minted at runtime) — work order 6."""
    session.add(ReconServicePrincipal(
        account_id=account_id, subject="svc-1", issuer="provisioning", audience="operations",
        allowed_scopes=[CAP], deployment_fingerprint=FP, active=True))
    session.add(ReconStandingGrant(
        account_id=account_id, policy_id="tenant-routine-operations-v1", policy_revision=1,
        capability=CAP, allowed_case_types=["daily_reconciliation"],
        allowed_destinations=["tenant_private_portal"], external_messages=False,
        financial_commitments=False,
        required_evidence=["required_inputs_complete", "accepted_facts"],
        required_artifact="reproducible_calculation_receipt", qualified_fingerprints=[FP],
        attestation_ttl_seconds=120, signing_key_id="key-1",
        valid_from=NOW - timedelta(hours=1), valid_to=NOW + timedelta(hours=1), active=True))
    session.flush()


def _new_env(company: str = "Design Partner Shipping") -> tuple[Session, UUID]:
    session = Session(_engine())
    account_id = _add_account(session, company)
    _grant_authority(session, account_id)
    return session, account_id


def _prepare_case(
    session: Session, account_id: UUID, vessel: str, actual: str, planned: str,
) -> tuple[ReconOperationalCase, Any] | tuple[None, None]:
    """Import a vessel's plan + report and commit its case version, stopping *before*
    publication (used by the fault-drill so there is a live case with no logical effect yet)."""
    session.add(ReconVesselMembership(
        account_id=account_id, vessel_ref=vessel, authority_role=AuthorityRole.OPERATOR,
        valid_from=PS - timedelta(days=365), valid_to=PE + timedelta(days=365)))
    job = ReconExpectedJob(
        account_id=account_id, vessel_ref=vessel, voyage_ref=f"VOY-{vessel}",
        period_start=PS, period_end=PE, due_at=NOW, contract_version=CONTRACT,
        result_contract_version="result_contract_v1",
        required_record_kind=RecordKind.DAILY_REPORT)
    session.add(job)
    session.flush()
    import_record(session, account_id=account_id, payload=_plan(vessel, planned), now=NOW)
    outcome = import_record(session, account_id=account_id, payload=_report(vessel, actual), now=NOW)
    if outcome.status is not ImportStatus.ACCEPTED or outcome.case_id is None:
        return None, None
    case = session.get(ReconOperationalCase, outcome.case_id)
    if case is None:
        return None, None
    result = compute_case_result(session, account_id=account_id, expected_job=job)
    version = commit_case_version(session, account_id=account_id, case=case, result=result)
    record_verification_verdict(
        session, account_id=account_id, expected_job=job, case=case,
        case_version=version, result=result,
    )
    return case, version


def _drive_fleet(session: Session, account_id: UUID) -> None:
    """Drive the whole FLEET feed through import -> ... -> publish -> read-back."""
    for vessel, _name, actual, planned in FLEET:
        session.add(ReconVesselMembership(
            account_id=account_id, vessel_ref=vessel, authority_role=AuthorityRole.OPERATOR,
            valid_from=PS - timedelta(days=365), valid_to=PE + timedelta(days=365)))
        job = ReconExpectedJob(
            account_id=account_id, vessel_ref=vessel, voyage_ref=f"VOY-{vessel}",
            period_start=PS, period_end=PE, due_at=NOW, contract_version=CONTRACT,
            result_contract_version="result_contract_v1",
            required_record_kind=RecordKind.DAILY_REPORT)
        session.add(job)
        session.flush()

        if planned is not None:
            import_record(session, account_id=account_id, payload=_plan(vessel, planned), now=NOW)
        outcome = import_record(session, account_id=account_id,
                                payload=_report(vessel, actual), now=NOW)
        if outcome.status is not ImportStatus.ACCEPTED or outcome.case_id is None:
            continue
        case = session.get(ReconOperationalCase, outcome.case_id)
        assert case is not None
        result = compute_case_result(session, account_id=account_id, expected_job=job)
        version = commit_case_version(session, account_id=account_id, case=case, result=result)
        verdict = record_verification_verdict(
            session, account_id=account_id, expected_job=job, case=case,
            case_version=version, result=result,
        )
        if not verdict.completes:
            case.business_status = BusinessStatus.UNRESOLVED
            case.automation_status = AutomationStatus.UNRESOLVED
            session.flush()
            continue
        proposed = propose_publication(
            session, account_id=account_id, case=case, case_version=version,
            principal_subject="svc-1", system_fingerprint=FP, now=NOW)
        intent = session.get(ReconActionIntent, proposed.intent_id) if proposed.intent_id else None
        if intent is not None:
            publish_case(session, account_id=account_id, intent=intent,
                         current_payload=dict(version.result_json), now=NOW)
            reconcile_publication(session, account_id=account_id, intent=intent, now=NOW)
    session.flush()


def _seed() -> tuple[Session, UUID]:
    session, account_id = _new_env()
    _drive_fleet(session, account_id)
    return session, account_id


# --------------------------------------------------------------------------- helpers


def _values(case: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    vessels = case.get("result", {}).get("vessels", [])
    if vessels:
        for mv in vessels[0].get("values", []):
            out[mv["name"]] = mv["value"]
    return out


def _resolve_name(session: Session, account_id: UUID, case_id: UUID) -> str:
    case = session.get(ReconOperationalCase, case_id)
    if case is None:
        return "Unknown vessel"
    job = session.get(ReconExpectedJob, case.expected_job_id)
    ref = job.vessel_ref if job is not None else ""
    return NAMES.get(ref, ref)


def _pill(status: str) -> str:
    accent = STATUS_ACCENT.get(status, "#8a93a6")
    label = status.replace("_", " ")
    return (f'<span class="pill" style="--c:{accent}"><i></i>{html.escape(label)}</span>')


def _f(value: str | None) -> float | None:
    try:
        return None if value in (None, "") else float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def _provenance(session: Session, account_id: UUID, case_id: UUID) -> list[dict[str, Any]]:
    """The immutable observations behind a case, with their source identity and pointer —
    the 'as reported / calculated / assumed' provenance the blueprint requires (section 09)."""
    case = session.get(ReconOperationalCase, case_id)
    if case is None:
        return []
    obs = session.scalars(
        select(ReconObservation).where(
            ReconObservation.account_id == account_id,
            ReconObservation.expected_job_id == case.expected_job_id,
        )
    ).all()
    rows: list[dict[str, Any]] = []
    for o in obs:
        cap = session.get(ReconSourceCapture, o.capture_id)
        rows.append({
            "field": o.field, "grade": o.fuel_grade.value, "value": str(o.value),
            "unit": o.unit.value, "origin": o.origin.value, "pointer": o.source_pointer,
            "source": cap.source_system if cap else "", "ref": cap.external_ref if cap else "",
            "revision": cap.source_revision if cap else "",
        })
    return rows


def case_detail(session: Session, account_id: UUID, case_id: UUID) -> dict[str, Any]:
    view = get_case_view(session, account_id=account_id, case_id=case_id)
    if view is None:
        return {}
    timeline = get_case_timeline(session, account_id=account_id, case_id=case_id)
    return {
        "name": _resolve_name(session, account_id, case_id),
        "view": view.render(),
        "timeline": timeline.render() if timeline else {"entries": []},
        "provenance": _provenance(session, account_id, case_id),
    }


def _authority(session: Session, account_id: UUID) -> dict[str, Any]:
    grant = session.scalars(
        select(ReconStandingGrant).where(
            ReconStandingGrant.account_id == account_id, ReconStandingGrant.active.is_(True)
        )
    ).first()
    principal = session.scalars(
        select(ReconServicePrincipal).where(ReconServicePrincipal.account_id == account_id)
    ).first()
    if grant is None or principal is None:
        return {}
    return {
        "subject": principal.subject, "fingerprint": principal.deployment_fingerprint,
        "capability": grant.capability, "policy": f"{grant.policy_id} · rev {grant.policy_revision}",
        "destinations": list(grant.allowed_destinations), "case_types": list(grant.allowed_case_types),
        "external": grant.external_messages, "financial": grant.financial_commitments,
        "ttl": grant.attestation_ttl_seconds, "signing_key": grant.signing_key_id,
        "valid_from": _iso(grant.valid_from), "valid_to": _iso(grant.valid_to),
        "artifact": grant.required_artifact,
    }


# ----------------------------------------------------------------- live backend actions


def _pilot_jobs() -> list[PilotJob]:
    """A clean, fully-plannable feed for the pilot harness. V1 receives a corrected report so
    the correction-propagation drill genuinely supersedes a prior version. The due time sits a
    little past the simulated processing window so on-time completion is genuine, not luck."""
    jobs: list[PilotJob] = []
    for vessel, _name, actual, planned in FLEET:
        if planned is None:
            continue
        corrected = (_report("V1", "23.600", source_revision="2") if vessel == "V1" else None)
        jobs.append(PilotJob(
            vessel_ref=vessel, plan_payload=_plan(vessel, planned),
            report_payload=_report(vessel, actual),
            period_start=PS, period_end=PE, due_at=NOW + timedelta(minutes=5),
            corrected_report_payload=corrected,
        ))
    return jobs


def run_pilot_report() -> dict[str, Any]:
    """Execute the entire work-order-10 pilot harness on an isolated tenant and return the
    honest, measured acceptance gates. Never touches the displayed session."""
    session, account_id = _new_env("Pilot Tenant (isolated)")
    foreign = _add_account(session, "Rival Freight Co")
    result = run_pilot(
        session, account_id=account_id, principal_subject="svc-1", system_fingerprint=FP,
        jobs=_pilot_jobs(), now=NOW, foreign_account_id=foreign,
    )
    acc = result.acceptance
    dr = acc.decision_record
    return {
        "readiness": acc.readiness.value,
        "caveat": "Synthetic replay only; timing is simulated. " + acc.caveat,
        "precision_lower_bound": acc.precision_lower_bound,
        "evidence_kind": dr.evidence_kind,
        "gates": [g.render() for g in acc.gates],
        "completion": {
            "expected": result.completion.expected_jobs,
            "correct_on_time": result.completion.correct_on_time_zero_touch,
            "rate": result.completion.completion_rate,
            "human_touch": result.completion.human_touch_jobs,
        },
        "verified_cases": result.verified_cases,
        "published_effects": result.published_effects,
        "p50": dr.p50_latency_seconds,
        "p95": dr.p95_latency_seconds,
    }


def fault_drill_report() -> dict[str, Any]:
    """Run two real durable-runtime drills on an isolated tenant:

    * worker crash + epoch fencing (WO5/WO9): a stale commit is rejected after recovery;
    * global stop / kill-switch (WO6): a proposal is DENIED (``capability_stopped``) before
      any effect, while the same proposal without the stop is allowed.
    """
    session, account_id = _new_env("Fault Tenant (isolated)")
    case_a, _ver_a = _prepare_case(session, account_id, "V1", "24.600", "23.000")
    case_b, ver_b = _prepare_case(session, account_id, "V2", "30.000", "30.000")

    recovery: dict[str, Any] = {"ok": False}
    if case_a is not None:
        run = create_run(
            session, account_id=account_id, case=case_a, workflow_version="wf-drill",
            system_fingerprint=FP, budget_units=100, deadline_at=NOW + timedelta(hours=1))
        enqueue_task(session, account_id=account_id, run=run, stage=WorkflowStage.VERIFY, now=NOW)
        crashed = claim_task(session, account_id=account_id, now=NOW)
        outcome, recovered = run_recover_expired_task(
            session, account_id=account_id, now=NOW + timedelta(minutes=10))
        fenced = None
        if crashed is not None:
            fenced = commit_stage(
                session, account_id=account_id, claim=crashed, now=NOW + timedelta(minutes=10))
        recovery = {
            "ok": bool(crashed and recovered and fenced),
            "task_id": (str(crashed.task_id)[:8] if crashed else None),
            "crashed_epoch": (crashed.lease_epoch if crashed else None),
            "recovered_epoch": (recovered.lease_epoch if recovered else None),
            "fenced": bool(fenced and fenced.status is CommitStatus.FENCED),
            "runbook": outcome.runbook_id,
            "detail": outcome.detail,
        }

    killswitch: dict[str, Any] = {}
    if case_b is not None and ver_b is not None:
        denied = propose_publication(
            session, account_id=account_id, case=case_b, case_version=ver_b,
            principal_subject="svc-1", system_fingerprint=FP, now=NOW, stop_active=True)
        allowed = propose_publication(
            session, account_id=account_id, case=case_b, case_version=ver_b,
            principal_subject="svc-1", system_fingerprint=FP, now=NOW, stop_active=False)
        killswitch = {
            "armed": {"ready": denied.ready, "decision": denied.decision.value,
                      "reason": denied.reason},
            "disarmed": {"ready": allowed.ready, "decision": allowed.decision.value,
                         "reason": allowed.reason},
        }

    session.close()
    return {"recovery": recovery, "killswitch": killswitch}


def _fleet_map(vessels: list[dict[str, Any]]) -> str:
    """A radial fleet constellation rendered from the real case statuses; each node opens
    the same case drawer on click."""
    w, h = 900, 470
    cx, cy = w / 2, h / 2 + 4
    n = len(vessels) or 1
    rx, ry = 328, 158
    links, nodes = [], []
    for i, v in enumerate(vessels):
        ang = (-90 + i * 360.0 / n) * math.pi / 180
        x = cx + rx * math.cos(ang)
        y = cy + ry * math.sin(ang)
        accent = STATUS_ACCENT.get(v["pres"], "#8a93a6")
        df = v["df"]
        mag = abs(df) if df else 0.0
        halo = 16 + min(mag, 3.0) * 8
        links.append(
            f'<line class="maplink" x1="{cx:.0f}" y1="{cy:.0f}" x2="{x:.0f}" y2="{y:.0f}"/>')
        nodes.append(
            f'<g class="node" data-id="{html.escape(v["case_id"])}">'
            f'<circle class="halo" cx="{x:.0f}" cy="{y:.0f}" r="{halo:.0f}" '
            f'fill="{accent}" opacity="0.16"/>'
            f'<circle cx="{x:.0f}" cy="{y:.0f}" r="7.5" fill="{accent}" '
            f'stroke="#05070f" stroke-width="2"/>'
            f'<text x="{x:.0f}" y="{y + 24:.0f}" text-anchor="middle">'
            f'{html.escape(v["name"])}</text></g>')
    hub = (
        f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="52" fill="url(#coreg)" opacity="0.10"/>'
        f'<circle class="corepulse" cx="{cx:.0f}" cy="{cy:.0f}" r="30" fill="none" '
        f'stroke="url(#coreg)" stroke-width="1.5" opacity="0.5"/>'
        f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="21" fill="url(#coreg)"/>'
        f'<text x="{cx:.0f}" y="{cy - 2:.0f}" text-anchor="middle" '
        f'style="fill:#04060c;font-family:var(--disp);font-size:11px;font-weight:700">CORE</text>'
        f'<text x="{cx:.0f}" y="{cy + 10:.0f}" text-anchor="middle" '
        f'style="fill:#04060c;font-family:var(--mono);font-size:7px">RECON</text>')
    return (
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet">'
        f'<defs><linearGradient id="coreg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="#2ee6c9"/><stop offset="1" stop-color="#5aa2ff"/>'
        f'</linearGradient></defs>'
        f'{"".join(links)}{hub}{"".join(nodes)}</svg>')


# --------------------------------------------------------------------------- styles

PAGE_CSS = """
*{box-sizing:border-box}
:root{
  --bg:#04060c; --ink:#e9eef7; --muted:#8791a6; --line:rgba(255,255,255,.08);
  --panel:rgba(255,255,255,.035); --glass:rgba(255,255,255,.05);
  --cyan:#2ee6c9; --blue:#5aa2ff; --grn:#2ee6a6; --amb:#ffc24b; --red:#ff5d73;
  --mono:ui-monospace,'JetBrains Mono','SF Mono',Menlo,Consolas,monospace;
  --sans:'Inter',system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  --disp:'Space Grotesk','Inter',system-ui,sans-serif;
}
html,body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  -webkit-font-smoothing:antialiased}
body{position:relative;overflow-x:hidden;min-height:100vh}
/* ambient background */
body::before{content:"";position:fixed;inset:-20%;z-index:-2;
  background:
   radial-gradient(60vw 60vw at 12% -8%,rgba(46,230,201,.14),transparent 60%),
   radial-gradient(55vw 55vw at 100% 8%,rgba(90,162,255,.16),transparent 60%),
   radial-gradient(70vw 70vw at 60% 120%,rgba(120,80,255,.10),transparent 60%);
  filter:saturate(1.1);animation:drift 22s ease-in-out infinite alternate}
body::after{content:"";position:fixed;inset:0;z-index:-1;opacity:.35;
  background-image:linear-gradient(rgba(255,255,255,.05) 1px,transparent 1px),
   linear-gradient(90deg,rgba(255,255,255,.05) 1px,transparent 1px);
  background-size:44px 44px;mask-image:radial-gradient(circle at 50% 20%,#000,transparent 85%);
  animation:grid 60s linear infinite}
@keyframes drift{to{transform:translate3d(2%,2%,0) scale(1.05)}}
@keyframes grid{to{background-position:44px 44px,44px 44px}}

.topbar{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;
  padding:16px 30px;backdrop-filter:blur(14px);
  background:linear-gradient(180deg,rgba(4,6,12,.85),rgba(4,6,12,.35));
  border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:12px;font-family:var(--disp);font-weight:600;
  letter-spacing:.14em;font-size:14px}
.brand .logo{display:grid;place-items:center;width:30px;height:30px;border-radius:9px;
  background:linear-gradient(135deg,var(--cyan),var(--blue));color:#04060c;font-size:15px;
  box-shadow:0 0 22px rgba(46,230,201,.5)}
.brand em{color:var(--muted);font-style:normal;font-weight:400;letter-spacing:.24em;font-size:11px}
.sysline{display:flex;align-items:center;gap:22px;font-family:var(--mono);font-size:12px}
.live{display:flex;align-items:center;gap:8px;color:var(--grn);letter-spacing:.18em}
.live i{width:8px;height:8px;border-radius:50%;background:var(--grn);
  box-shadow:0 0 0 0 rgba(46,230,166,.7);animation:pulse 2s infinite}
@keyframes pulse{70%{box-shadow:0 0 0 9px rgba(46,230,166,0)}100%{box-shadow:0 0 0 0 rgba(46,230,166,0)}}
.clock{color:var(--muted)}

.wrap{max-width:1280px;margin:0 auto;padding:30px 30px 110px}
.hero{display:grid;grid-template-columns:1.15fr .85fr;gap:22px;align-items:stretch;margin-bottom:20px}
h1{font-family:var(--disp);font-weight:600;font-size:34px;line-height:1.06;margin:6px 0 10px;
  letter-spacing:-.01em}
.crumbs{color:var(--muted);font-family:var(--mono);font-size:12px;letter-spacing:.03em}
.tag{display:inline-block;margin-top:16px;padding:6px 12px;border:1px solid var(--line);
  border-radius:999px;font-family:var(--mono);font-size:11px;color:var(--cyan);
  background:rgba(46,230,201,.06)}

.panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;
  backdrop-filter:blur(8px);box-shadow:0 20px 60px rgba(0,0,0,.35)}
.gauge{display:flex;align-items:center;gap:26px;padding:26px 30px}
.gauge .ringwrap{position:relative;width:150px;height:150px;flex:none}
.gauge svg{transform:rotate(-90deg)}
.gauge .val{position:absolute;inset:0;display:grid;place-content:center;text-align:center}
.gauge .val b{font-family:var(--disp);font-size:34px;display:block;line-height:1}
.gauge .val span{font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:.2em}
.gauge .legend h3{margin:0 0 4px;font-family:var(--disp);font-size:15px}
.gauge .legend p{margin:0 0 14px;color:var(--muted);font-size:13px;max-width:230px}
.lg{display:flex;gap:18px;font-family:var(--mono);font-size:12px;color:var(--muted)}
.lg b{color:var(--ink);font-size:18px;display:block;font-family:var(--disp)}

.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin:6px 0 22px}
.kpi{padding:18px 18px 16px;position:relative;overflow:hidden}
.kpi .k{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.14em;
  text-transform:uppercase}
.kpi .v{font-family:var(--disp);font-size:30px;margin-top:8px;line-height:1;
  font-variant-numeric:tabular-nums}
.kpi .u{font-size:13px;color:var(--muted);margin-left:4px}
.kpi::after{content:"";position:absolute;left:0;right:0;bottom:0;height:2px;
  background:linear-gradient(90deg,var(--a,var(--cyan)),transparent)}
.kpi .spark{position:absolute;right:14px;top:16px;opacity:.5}

.grid2{display:grid;grid-template-columns:1.5fr .9fr;gap:22px;align-items:start}
.phead{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;
  border-bottom:1px solid var(--line)}
.phead h2{margin:0;font-family:var(--disp);font-size:13px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--muted)}
.phead .meta{font-family:var(--mono);font-size:11px;color:var(--muted)}

table{width:100%;border-collapse:collapse}
th,td{padding:14px 18px;text-align:left;font-size:13.5px;border-bottom:1px solid var(--line)}
th{font-family:var(--mono);font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--muted);font-weight:500}
tbody tr{transition:background .2s}
tbody tr:hover{background:rgba(255,255,255,.03)}
tbody tr:last-child td{border-bottom:none}
.num{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}
.vn{font-weight:600}
.vn small{display:block;color:var(--muted);font-family:var(--mono);font-size:10.5px;
  letter-spacing:.08em;text-transform:uppercase;margin-top:3px;font-weight:400}
.up{color:var(--red)} .down{color:var(--grn)} .flat{color:var(--muted)}
.hash{font-family:var(--mono);font-size:11px;color:var(--muted)}
.hash b{color:var(--cyan);font-weight:500}

.pill{display:inline-flex;align-items:center;gap:7px;padding:5px 11px;border-radius:999px;
  font-size:11.5px;font-weight:600;text-transform:capitalize;color:var(--c);
  background:color-mix(in srgb,var(--c) 14%,transparent);
  border:1px solid color-mix(in srgb,var(--c) 35%,transparent)}
.pill i{width:6px;height:6px;border-radius:50%;background:var(--c);
  box-shadow:0 0 8px var(--c)}

.varrow{display:grid;grid-template-columns:120px 1fr 74px;gap:12px;align-items:center;
  padding:11px 20px;border-bottom:1px solid var(--line)}
.varrow:last-child{border-bottom:none}
.varrow .nm{font-size:13px}
.track{position:relative;height:12px;border-radius:6px;background:rgba(255,255,255,.04)}
.track .mid{position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:rgba(255,255,255,.18)}
.bar{position:absolute;top:0;height:12px;border-radius:6px;width:0;animation:grow .9s cubic-bezier(.2,.8,.2,1) forwards}
.bar.pos{left:50%;background:linear-gradient(90deg,rgba(255,93,115,.5),var(--red));
  box-shadow:0 0 14px rgba(255,93,115,.4)}
.bar.neg{right:50%;background:linear-gradient(90deg,var(--grn),rgba(46,230,166,.5));
  box-shadow:0 0 14px rgba(46,230,166,.35)}
@keyframes grow{to{width:var(--w)}}
.vv{text-align:right;font-family:var(--mono);font-size:12.5px}

.mini{display:flex;justify-content:space-between;padding:13px 20px;border-bottom:1px solid var(--line);
  font-size:13px}
.mini:last-child{border-bottom:none}
.mini .lab{color:var(--muted)}
.mini .val{font-family:var(--mono)}
.ok{color:var(--grn)} .off{color:var(--muted)}

.trust{display:flex;flex-wrap:wrap;gap:12px;margin:24px 0 8px}
.chip{display:flex;align-items:center;gap:9px;padding:11px 15px;border:1px solid var(--line);
  border-radius:13px;background:var(--panel);font-size:12.5px}
.chip b{font-family:var(--disp);font-weight:600}
.chip .dot{width:8px;height:8px;border-radius:50%;background:var(--grn);box-shadow:0 0 10px var(--grn)}
.chip.warn .dot{background:var(--amb);box-shadow:0 0 10px var(--amb)}
.chip code{font-family:var(--mono);color:var(--muted);font-size:11px}

.note{margin-top:26px;color:var(--muted);font-size:12px;line-height:1.7;border-top:1px solid var(--line);
  padding-top:18px;max-width:900px}
.note em{color:var(--ink);font-style:normal}
.banner{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin:2px 0 20px;
  background:rgba(255,194,75,.08);border:1px solid rgba(255,194,75,.28);color:var(--amb);
  font-size:13px;font-family:var(--mono)}

.reveal{opacity:0;transform:translateY(14px);animation:rise .7s cubic-bezier(.2,.8,.2,1) forwards}
@keyframes rise{to{opacity:1;transform:none}}
@media (max-width:960px){.hero{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(2,1fr)}
  .grid2{grid-template-columns:1fr}}
"""

PAGE_JS = """
const clock=()=>{const d=new Date();
  const p=n=>String(n).padStart(2,'0');
  document.getElementById('clock').textContent=
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())} UTC`;};
clock();setInterval(clock,1000);
const ease=t=>1-Math.pow(1-t,3);
function countUp(el){
  const to=parseFloat(el.dataset.to||'0'),dec=parseInt(el.dataset.dec||'0'),
    pre=el.dataset.pre||'',suf=el.dataset.suf||'',dur=1100;
  const sign=to<0?'-':'';const abs=Math.abs(to);let t0=null;
  const step=ts=>{if(!t0)t0=ts;const p=Math.min((ts-t0)/dur,1);
    const v=(abs*ease(p)).toFixed(dec);
    el.textContent=pre+sign+v+suf;if(p<1)requestAnimationFrame(step);};
  requestAnimationFrame(step);}
addEventListener('DOMContentLoaded',()=>{document.querySelectorAll('.count').forEach(countUp);
  const r=document.querySelector('.progress');
  if(r){const dash=r.dataset.dash;requestAnimationFrame(()=>{r.style.strokeDashoffset=dash;});}});
"""


PAGE_CSS_EXTRA = """
#stars{position:fixed;inset:0;z-index:-1;pointer-events:none}
tr.clickable{cursor:pointer}
tr.clickable td:first-child::after{content:"›";float:right;color:var(--muted);opacity:0;
  transition:opacity .2s,transform .2s;transform:translateX(-4px)}
tr.clickable:hover td:first-child::after{opacity:.8;transform:none}

/* governance / authority */
.gov{display:grid;grid-template-columns:340px 1fr;gap:0}
.killwrap{padding:26px;border-right:1px solid var(--line);display:flex;flex-direction:column;
  gap:16px}
.switch{display:flex;align-items:center;gap:14px}
.switch .knob{position:relative;width:64px;height:32px;border-radius:999px;flex:none;
  background:linear-gradient(90deg,rgba(46,230,166,.25),rgba(46,230,166,.5));
  border:1px solid rgba(46,230,166,.5);box-shadow:inset 0 0 14px rgba(46,230,166,.3)}
.switch .knob::after{content:"";position:absolute;top:3px;right:3px;width:24px;height:24px;
  border-radius:50%;background:var(--grn);box-shadow:0 0 14px var(--grn);animation:pulse 2.4s infinite}
.switch b{font-family:var(--disp);letter-spacing:.14em;font-size:13px;color:var(--grn)}
.killwrap .sub{color:var(--muted);font-size:12px;line-height:1.6}
.arm{margin-top:auto;font-family:var(--mono);font-size:11px;color:var(--muted);
  border:1px dashed var(--line);border-radius:10px;padding:10px 12px}
.arm span{color:var(--red)}
.grantgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:0;padding:8px 0}
.gv{padding:14px 24px;border-bottom:1px solid var(--line)}
.gv:nth-child(odd){border-right:1px solid var(--line)}
.gv .k{font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--muted)}
.gv .v{margin-top:6px;font-size:13.5px;font-family:var(--mono)}
.gv .v.no{color:var(--red)} .gv .v.yes{color:var(--grn)}

/* drawer */
.scrim{position:fixed;inset:0;z-index:20;background:rgba(3,5,10,.6);backdrop-filter:blur(3px);
  opacity:0;pointer-events:none;transition:opacity .3s}
.scrim.open{opacity:1;pointer-events:auto}
.drawer{position:fixed;top:0;right:0;bottom:0;width:min(560px,94vw);z-index:21;
  background:linear-gradient(180deg,#0a0f1b,#060810);border-left:1px solid var(--line);
  box-shadow:-40px 0 80px rgba(0,0,0,.6);transform:translateX(100%);
  transition:transform .38s cubic-bezier(.2,.8,.2,1);overflow-y:auto;padding:26px 28px 40px}
.drawer.open{transform:none}
.drawer .x{position:absolute;top:18px;right:20px;width:34px;height:34px;border-radius:10px;
  border:1px solid var(--line);background:var(--glass);color:var(--ink);cursor:pointer;font-size:16px}
.drawer h3{font-family:var(--disp);font-size:22px;margin:4px 0 2px}
.drawer .dsub{color:var(--muted);font-family:var(--mono);font-size:12px;margin-bottom:18px}
.dsec{font-family:var(--mono);font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--muted);margin:24px 0 12px;display:flex;align-items:center;gap:10px}
.dsec::after{content:"";flex:1;height:1px;background:var(--line)}
.vgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.vcell{border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:var(--panel)}
.vcell .k{font-size:11px;color:var(--muted);display:flex;justify-content:space-between}
.vcell .k .og{font-family:var(--mono);font-size:9px;letter-spacing:.1em;text-transform:uppercase;
  padding:1px 6px;border-radius:6px}
.og.reported{color:var(--blue);background:rgba(90,162,255,.14)}
.og.calculated{color:var(--cyan);background:rgba(46,230,201,.14)}
.vcell .n{font-family:var(--mono);font-size:19px;margin-top:6px;font-variant-numeric:tabular-nums}
.tl{position:relative;margin-left:8px;padding-left:22px}
.tl::before{content:"";position:absolute;left:4px;top:4px;bottom:4px;width:2px;
  background:linear-gradient(var(--cyan),var(--blue),transparent)}
.tli{position:relative;padding:0 0 18px}
.tli::before{content:"";position:absolute;left:-22px;top:2px;width:10px;height:10px;border-radius:50%;
  background:var(--cyan);box-shadow:0 0 10px var(--cyan);border:2px solid #060810}
.tli .kind{font-family:var(--disp);font-size:13.5px}
.tli .meta{color:var(--muted);font-family:var(--mono);font-size:11px;margin-top:2px}
.tli .rc{color:var(--cyan)}
.prov{border:1px solid var(--line);border-radius:12px;overflow:hidden}
.prov .pr{display:grid;grid-template-columns:60px 1fr auto;gap:10px;padding:11px 14px;
  border-bottom:1px solid var(--line);font-size:12.5px;align-items:center}
.prov .pr:last-child{border-bottom:none}
.prov .role{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted)}
.prov .src{font-family:var(--mono);font-size:11px;color:var(--muted)}
.prov .qty{font-family:var(--mono);text-align:right}
.rcpt{font-family:var(--mono);font-size:11.5px;word-break:break-all;color:var(--muted);
  border:1px solid var(--line);border-radius:12px;padding:14px;line-height:1.9;background:var(--panel)}
.rcpt b{color:var(--cyan)} .rcpt .yes{color:var(--grn)}
.dload{color:var(--muted);font-family:var(--mono);font-size:12px;padding:8px 0}
@media (max-width:720px){.gov{grid-template-columns:1fr}.killwrap{border-right:none;
  border-bottom:1px solid var(--line)}.vgrid{grid-template-columns:1fr}}
"""

PAGE_JS_EXTRA = """
/* constellation starfield */
(function(){const c=document.getElementById('stars');if(!c)return;const x=c.getContext('2d');
let w,h,pts;const N=64;
function size(){w=c.width=innerWidth;h=c.height=innerHeight;
  pts=Array.from({length:N},()=>({x:Math.random()*w,y:Math.random()*h,
    vx:(Math.random()-.5)*.22,vy:(Math.random()-.5)*.22,r:Math.random()*1.6+.4}));}
size();addEventListener('resize',size);
function tick(){x.clearRect(0,0,w,h);
  for(const p of pts){p.x+=p.vx;p.y+=p.vy;if(p.x<0||p.x>w)p.vx*=-1;if(p.y<0||p.y>h)p.vy*=-1;
    x.beginPath();x.arc(p.x,p.y,p.r,0,7);x.fillStyle='rgba(120,200,255,.55)';x.fill();}
  for(let i=0;i<N;i++)for(let j=i+1;j<N;j++){const a=pts[i],b=pts[j],
    d=Math.hypot(a.x-b.x,a.y-b.y);if(d<130){x.beginPath();x.moveTo(a.x,a.y);x.lineTo(b.x,b.y);
    x.strokeStyle='rgba(90,162,255,'+(0.12*(1-d/130))+')';x.lineWidth=1;x.stroke();}}
  requestAnimationFrame(tick);}
if(!matchMedia('(prefers-reduced-motion:reduce)').matches)tick();})();

/* case drawer */
const scrim=document.getElementById('scrim'),drawer=document.getElementById('drawer'),
  body=document.getElementById('dbody');
function closeDrawer(){scrim.classList.remove('open');drawer.classList.remove('open');}
scrim&&scrim.addEventListener('click',closeDrawer);
addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer();});
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
async function openCase(id){
  scrim.classList.add('open');drawer.classList.add('open');
  body.innerHTML='<div class="dload">◈ loading case telemetry…</div>';
  try{const r=await fetch('/api/case?id='+id);const d=await r.json();body.innerHTML=renderCase(d);}
  catch(e){body.innerHTML='<div class="dload">failed to load</div>';}}
function renderCase(d){
  const v=d.view||{};const rc=v.receipts||{};
  const vals=((v.result||{}).vessels||[]).flatMap(x=>x.values||[]);
  const cells=vals.map(mv=>`<div class="vcell"><div class="k">${esc(mv.name)}
     <span class="og ${mv.origin}">${esc(mv.origin)}</span></div>
     <div class="n">${esc(mv.value)}</div></div>`).join('')||'<div class="dload">no reconciled values</div>';
  const tl=((d.timeline||{}).entries||[]).map(e=>`<div class="tli">
     <div class="kind">${esc(e.kind).replace(/_/g,' ')}</div>
     <div class="meta">${e.revision!=null?'rev '+e.revision+' · ':''}${esc(e.at||'')}
       ${e.reason_code?'· <span class="rc">'+esc(e.reason_code)+'</span>':''}
       ${e.content_hash?'· '+esc(e.content_hash.slice(0,10))+'…':''}</div></div>`).join('')
     ||'<div class="dload">no timeline</div>';
  const prov=(d.provenance||[]).map(p=>`<div class="pr">
     <span class="role">${esc(p.origin)}</span>
     <span class="src">${esc(p.source)}·${esc(p.ref)} rev ${esc(p.revision)}
       <span style="opacity:.7">${esc(p.pointer)}</span></span>
     <span class="qty">${esc(p.value)} ${esc(p.unit)} · ${esc(p.grade)}</span></div>`).join('')
     ||'<div class="dload">no observations</div>';
  return `<button class="x" onclick="closeDrawer()">✕</button>
    <h3>${esc(d.name)}</h3>
    <div class="dsub">case ${esc((v.case_id||'').slice(0,8))} · rev ${esc(v.revision)} ·
       ${esc(v.presentation_status)} · evidence ${esc(v.evidence_status)}</div>
    <div class="dsec">Material values</div><div class="vgrid">${cells}</div>
    <div class="dsec">Provenance · immutable observations</div><div class="prov">${prov}</div>
    <div class="dsec">Workflow &amp; correction timeline</div><div class="tl">${tl}</div>
    <div class="dsec">Reproducibility receipt</div>
    <div class="rcpt">content_hash <b>${esc(rc.content_hash||'—')}</b><br>
       evidence_hash <b>${esc(rc.evidence_hash||'—')}</b><br>
       read-back <span class="yes">${rc.read_back_confirmed?'✓ verified — hash matched':'pending'}</span>
       · next step ${esc(v.next_machine_step||'—')}</div>`;}
document.addEventListener('click',e=>{const tr=e.target.closest('tr.clickable');
  if(tr&&tr.dataset.id)openCase(tr.dataset.id);});
"""


PAGE_CSS_EXTRA2 = """
/* ---------------- light theme ---------------- */
:root[data-theme="light"]{
  --bg:#eef1f8; --ink:#0c1222; --muted:#5a6478;
  --line:rgba(12,22,44,.10); --panel:rgba(255,255,255,.72); --glass:rgba(255,255,255,.6);
  --cyan:#0a9f88; --blue:#2f6bff; --grn:#0f9d6a; --amb:#b9760a; --red:#e23a55;
}
:root[data-theme="light"] body{background:var(--bg)}
:root[data-theme="light"] body::before{
  background:
   radial-gradient(60vw 60vw at 12% -8%,rgba(10,168,143,.12),transparent 60%),
   radial-gradient(55vw 55vw at 100% 8%,rgba(47,107,255,.12),transparent 60%),
   radial-gradient(70vw 70vw at 60% 120%,rgba(120,80,255,.07),transparent 60%)}
:root[data-theme="light"] body::after{opacity:.5;
  background-image:linear-gradient(rgba(12,22,44,.05) 1px,transparent 1px),
   linear-gradient(90deg,rgba(12,22,44,.05) 1px,transparent 1px)}
:root[data-theme="light"] .topbar{
  background:linear-gradient(180deg,rgba(238,241,248,.9),rgba(238,241,248,.5))}
:root[data-theme="light"] tbody tr:hover{background:rgba(12,22,44,.03)}
:root[data-theme="light"] .track{background:rgba(12,22,44,.06)}
:root[data-theme="light"] .track .mid{background:rgba(12,22,44,.2)}
:root[data-theme="light"] .panel{box-shadow:0 18px 50px rgba(20,40,80,.10)}
:root[data-theme="light"] .brand .logo{color:#fff}
:root[data-theme="light"] .dock{
  background:linear-gradient(180deg,rgba(255,255,255,.94),rgba(238,241,248,.92))}
:root[data-theme="light"] .ovcard,:root[data-theme="light"] .drawer{
  background:linear-gradient(180deg,#ffffff,#eef1f8)}
:root[data-theme="light"] .tli::before{border-color:#fff}
:root[data-theme="light"] #stars{opacity:.5}

/* ---------------- command dock ---------------- */
.dock{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:15;display:flex;
  align-items:center;gap:10px;padding:10px;border-radius:16px;border:1px solid var(--line);
  background:linear-gradient(180deg,rgba(10,15,27,.92),rgba(6,8,16,.92));
  backdrop-filter:blur(16px);box-shadow:0 20px 60px rgba(0,0,0,.5)}
.dock button{display:flex;align-items:center;gap:8px;padding:11px 16px;border-radius:11px;
  border:1px solid var(--line);background:var(--glass);color:var(--ink);cursor:pointer;
  font-family:var(--sans);font-size:13px;font-weight:500;transition:.2s;white-space:nowrap}
.dock button:hover{border-color:color-mix(in srgb,var(--cyan) 55%,transparent);
  box-shadow:0 0 18px rgba(46,230,201,.18);transform:translateY(-1px)}
.dock button .ic{font-size:14px;line-height:1}
.dock .primary{background:linear-gradient(135deg,rgba(46,230,201,.24),rgba(90,162,255,.24));
  border-color:rgba(46,230,201,.42)}
.dock .warnb{background:linear-gradient(135deg,rgba(255,157,77,.2),rgba(255,93,115,.2));
  border-color:rgba(255,93,115,.38)}
.dock .icon-only{padding:11px 13px}
.dock .sep{width:1px;height:26px;background:var(--line)}
@media (max-width:760px){.dock{flex-wrap:wrap;width:94vw;justify-content:center}
  .dock button .lbl{display:none}}

/* ---------------- fleet map ---------------- */
.mapwrap{padding:8px 10px 14px}
.mapwrap svg{display:block;width:100%;height:auto}
.node{cursor:pointer}
.node text{font-family:var(--mono);font-size:10px;fill:var(--muted);transition:fill .2s}
.node:hover text{fill:var(--ink)}
.node:hover circle:nth-child(2){filter:drop-shadow(0 0 8px currentColor)}
.node .halo{transform-box:fill-box;transform-origin:center;animation:halo 3.4s ease-in-out infinite}
@keyframes halo{50%{opacity:.4}}
.maplink{stroke:rgba(120,160,255,.14);stroke-width:1}
.corepulse{transform-box:fill-box;transform-origin:center;animation:corep 3s ease-out infinite}
@keyframes corep{0%{transform:scale(.7);opacity:.6}100%{transform:scale(1.9);opacity:0}}

/* ---------------- overlay (run + drill) ---------------- */
.ov{position:fixed;inset:0;z-index:40;display:none;place-items:center;padding:24px;
  background:rgba(2,4,9,.82);backdrop-filter:blur(9px)}
.ov.open{display:grid}
.ovcard{position:relative;width:min(780px,96vw);max-height:90vh;overflow:auto;border-radius:22px;
  border:1px solid var(--line);background:linear-gradient(180deg,#0a1120,#05070f);
  box-shadow:0 40px 120px rgba(0,0,0,.6);padding:30px 34px 34px}
.ovx{position:absolute;top:18px;right:20px;width:34px;height:34px;border-radius:10px;
  border:1px solid var(--line);background:var(--glass);color:var(--ink);cursor:pointer;font-size:15px}
.ovcard h2{font-family:var(--disp);font-size:24px;margin:0 4px 4px 0}
.ovcard .sub{color:var(--muted);font-family:var(--mono);font-size:12px;margin-bottom:22px}
.stages{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:22px}
.stage{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:10px;
  border:1px solid var(--line);font-family:var(--mono);font-size:11.5px;color:var(--muted);
  opacity:.35;transition:.35s}
.stage.on{opacity:1;color:var(--ink);border-color:rgba(46,230,201,.4);
  box-shadow:0 0 16px rgba(46,230,201,.12)}
.stage .sd{width:7px;height:7px;border-radius:50%;background:var(--muted);transition:.35s}
.stage.on .sd{background:var(--grn);box-shadow:0 0 10px var(--grn)}
.gates{display:grid;gap:8px}
.gate{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;
  border-radius:12px;border:1px solid var(--line);background:var(--panel);opacity:0;
  transform:translateY(8px);transition:.4s}
.gate.show{opacity:1;transform:none}
.gate .gl{font-size:13px;text-transform:capitalize}
.gate .gl small{display:block;color:var(--muted);font-family:var(--mono);font-size:10.5px;
  margin-top:3px;text-transform:none}
.gate .gs{font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;padding:5px 11px;
  border-radius:999px;flex:none}
.gs.pass{color:var(--grn);background:rgba(46,230,166,.14)}
.gs.pending{color:var(--amb);background:rgba(255,194,75,.14)}
.gs.fail{color:var(--red);background:rgba(255,93,115,.14)}
.tier{font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--muted);margin:18px 0 8px}
.verdict{margin-top:20px;padding:18px;border-radius:14px;border:1px solid rgba(46,230,201,.3);
  background:linear-gradient(135deg,rgba(46,230,201,.08),rgba(90,162,255,.06))}
.verdict b{font-family:var(--disp);font-size:19px;color:var(--cyan)}
.verdict .ek{display:inline-block;margin-left:10px;font-family:var(--mono);font-size:10px;
  letter-spacing:.06em;color:var(--amb);border:1px solid rgba(255,194,75,.3);padding:2px 8px;
  border-radius:999px;vertical-align:middle}
.verdict .stat{font-family:var(--mono);font-size:12px;color:var(--ink);margin-top:8px}
.verdict .caveat{color:var(--muted);font-size:11.5px;line-height:1.6;margin-top:12px}

/* fault theatre */
.act{margin-bottom:20px}
.acth{font-family:var(--disp);font-size:14px;margin-bottom:12px}
.flow{display:flex;align-items:stretch;gap:10px;flex-wrap:wrap}
.fbox{flex:1;min-width:150px;border:1px solid var(--line);border-radius:12px;padding:14px;
  background:var(--panel);display:flex;flex-direction:column;gap:4px}
.fbox b{font-family:var(--disp);font-size:13px}
.fbox span{font-family:var(--mono);font-size:11px;color:var(--muted)}
.fbox em{font-family:var(--mono);font-size:12px;font-style:normal;margin-top:4px}
.fbox.ok{border-color:rgba(46,230,166,.4)} .fbox.ok em{color:var(--grn)}
.fbox.fenced{border-color:rgba(255,93,115,.45)} .fbox.fenced em{color:var(--red);font-weight:600}
.fbox .dead{color:var(--red)}
.farrow{display:grid;place-items:center;color:var(--muted);font-size:20px}
.actnote{color:var(--muted);font-size:12px;line-height:1.6;margin-top:12px}
.ksrow{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.kscol{border:1px solid var(--line);border-radius:12px;padding:16px;background:var(--panel);
  text-align:center}
.kscol.armed{border-color:rgba(255,93,115,.4)}
.kh{font-family:var(--mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted)}
.kd{font-family:var(--disp);font-size:24px;margin:8px 0 4px}
.kd.no{color:var(--red)} .kd.yes{color:var(--grn)}
.kr{font-family:var(--mono);font-size:11px;color:var(--muted)}
@media (max-width:640px){.flow{flex-direction:column}.farrow{transform:rotate(90deg)}
  .ksrow{grid-template-columns:1fr}}
"""

PAGE_CSS_POLISH = """
/* ================= VISUAL POLISH — loaded last, elevates the base ================= */
:root{
  --ink:#eef3fe; --muted:#8b95ad;
  --line:rgba(150,180,255,.12);
  --panel:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.02));
  --cyan:#35ecd2; --blue:#6aa8ff; --grn:#3ff0ad; --amb:#ffcc5e; --red:#ff6a80;
  --accent:linear-gradient(120deg,#35ecd2 0%,#6aa8ff 55%,#a78bff 100%);
  --rim:linear-gradient(180deg,rgba(255,255,255,.30),rgba(255,255,255,0) 42%);
  --elev:0 34px 90px -28px rgba(0,0,0,.72), 0 10px 30px -18px rgba(0,0,0,.55);
}
/* deeper, more atmospheric ambient field */
body::before{filter:saturate(1.28) blur(2px)}

/* topbar — crisper glass with a luminous underline */
.topbar{padding:18px 34px;border-bottom:none;overflow:visible;
  background:linear-gradient(180deg,rgba(6,9,18,.92),rgba(6,9,18,.30))}
.topbar::after{content:"";position:absolute;left:0;right:0;bottom:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(53,236,210,.55),rgba(106,168,255,.55),transparent)}
.brand{letter-spacing:.2em}
.brand .logo{box-shadow:0 0 0 1px rgba(255,255,255,.16),0 0 28px rgba(53,236,210,.55)}

/* a more generous, confident canvas */
.wrap{max-width:1320px;padding:38px 34px 132px}
h1{font-size:44px;letter-spacing:-.025em;margin:10px 0 12px}
.crumbs{letter-spacing:.13em;text-transform:uppercase;font-size:11px}
.tag{padding:8px 14px;font-size:11.5px;border-color:rgba(53,236,210,.35);
  background:linear-gradient(135deg,rgba(53,236,210,.12),rgba(106,168,255,.07));
  box-shadow:0 0 26px rgba(53,236,210,.12)}

/* premium panels — gradient surface, luminous rim, deep shadow, lift on hover */
.panel{border-radius:20px;box-shadow:var(--elev);position:relative;
  backdrop-filter:blur(12px) saturate(1.25);
  transition:transform .3s cubic-bezier(.2,.8,.2,1),box-shadow .3s,border-color .3s}
.panel::before{content:"";position:absolute;inset:0;border-radius:inherit;padding:1px;
  background:var(--rim);pointer-events:none;
  -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);
  -webkit-mask-composite:xor;mask-composite:exclude}
.grid2 .panel:hover,.hero .panel:hover{transform:translateY(-3px);
  border-color:rgba(150,180,255,.22);
  box-shadow:0 46px 110px -30px rgba(0,0,0,.78),0 0 44px -22px rgba(53,236,210,.42)}

/* KPI — bigger numerals, a glowing accent underline */
.kpi .v{font-size:32px}
.kpi::after{height:3px;filter:saturate(1.35);box-shadow:0 0 16px var(--a,var(--cyan))}

/* completion ring — halo glow + gradient-clipped value */
.gauge .ringwrap::before{content:"";position:absolute;inset:-14%;border-radius:50%;z-index:-1;
  background:radial-gradient(circle,rgba(53,236,210,.24),transparent 68%);filter:blur(6px)}
.gauge .val b{font-size:38px;background:var(--accent);
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;
  color:transparent;filter:drop-shadow(0 0 20px rgba(53,236,210,.30))}

/* fleet ledger — header wash and a glowing hover edge */
thead th{background:linear-gradient(180deg,rgba(150,180,255,.06),transparent)}
tbody tr:hover{background:linear-gradient(90deg,rgba(53,236,210,.07),transparent)}
tbody tr:hover td:first-child{box-shadow:inset 2px 0 0 var(--cyan)}

/* command dock — signature accent primary */
.dock{border-radius:18px;
  box-shadow:0 24px 70px rgba(0,0,0,.6),0 0 44px -22px rgba(53,236,210,.5)}
.dock .primary{background:var(--accent);color:#05070e;border-color:transparent;font-weight:600;
  box-shadow:0 8px 24px -10px rgba(53,236,210,.6)}
.dock .primary:hover{filter:brightness(1.08);box-shadow:0 12px 32px -10px rgba(53,236,210,.72)}

/* one signature moment — a slow light sheen across the hero gauge */
.hero .gauge{overflow:hidden}
.hero .gauge::after{content:"";position:absolute;top:-60%;left:-40%;width:38%;height:220%;
  transform:rotate(18deg);pointer-events:none;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.10),transparent);
  animation:sheen 7s ease-in-out infinite}
@keyframes sheen{0%,55%{left:-40%}82%,100%{left:132%}}

/* trust chips lift a little */
.chip{border-radius:14px}
"""

PAGE_JS_EXTRA2 = """
/* theme toggle */
(function(){const KEY='mtime-theme';const root=document.documentElement;
  try{const s=localStorage.getItem(KEY);if(s)root.setAttribute('data-theme',s);}catch(e){}
  function icon(){const b=document.getElementById('themebtn');if(!b)return;
    const light=root.getAttribute('data-theme')==='light';
    b.querySelector('.ic').textContent=light?'☀':'☾';
    b.querySelector('.lbl').textContent=light?'Daylight':'Midnight';}
  window.toggleTheme=function(){const light=root.getAttribute('data-theme')==='light';
    root.setAttribute('data-theme',light?'dark':'light');
    try{localStorage.setItem(KEY,light?'dark':'light');}catch(e){}icon();};
  addEventListener('DOMContentLoaded',icon);})();

/* overlay shell */
const overlay=document.getElementById('overlay'),ovbody=document.getElementById('ovbody');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
function closeOverlay(){overlay.classList.remove('open');ovbody.innerHTML='';}
overlay&&overlay.addEventListener('click',e=>{if(e.target===overlay)closeOverlay();});
addEventListener('keydown',e=>{if(e.key==='Escape')closeOverlay();});
function openOverlay(h){ovbody.innerHTML=h;overlay.classList.add('open');ovbody.parentElement.scrollTop=0;}
const STAGES=['Lock expected-work ledger','Import authorized feed','Reconcile & calculate',
  'Independent verification','Authorized publication','Independent read-back',
  'Worker-recovery drill','Correction-propagation drill','Acceptance gates'];

/* run the real pilot harness */
async function runPilot(){
  openOverlay(`<button class="ovx" onclick="closeOverlay()">✕</button>
    <h2>Autonomous pilot run</h2>
    <div class="sub">synthetic input replay with a simulated clock · isolated tenant · no external effects</div>
    <div class="stages">`+STAGES.map(s=>`<div class="stage"><span class="sd"></span>${s}</div>`).join('')+`</div>
    <div id="runout"><div class="dload">◈ executing pipeline…</div></div>`);
  let d;try{d=await(await fetch('/api/run')).json();}catch(e){
    const o=document.getElementById('runout');if(o)o.innerHTML='<div class="dload">run failed</div>';return;}
  const stages=[...ovbody.querySelectorAll('.stage')];
  for(let i=0;i<stages.length;i++){await wait(230);stages[i].classList.add('on');}
  await wait(260);renderRun(d);
}
function gate(g){const st=g.status==='pass'?'pass':(g.status==='fail'?'fail':'pending');
  const lbl=g.status==='pass'?'PASS':(g.status==='fail'?'FAIL':'PENDING');
  return `<div class="gate"><div class="gl">${esc(g.name).replace(/_/g,' ')}
    <small>${esc(g.detail)}</small></div><div class="gs ${st}">${lbl}</div></div>`;}
function renderRun(d){const o=document.getElementById('runout');if(!o)return;
  const gs=d.gates||[];const tech=gs.filter(g=>g.tier==='technical'),fld=gs.filter(g=>g.tier==='field');
  const techPass=tech.filter(g=>g.status==='pass').length;
  const c=d.completion||{};const p95=d.p95==null?'—':(+d.p95).toFixed(0);
  const ready=d.readiness||'';
  const head=ready==='qualified'?'Qualified'
    :ready==='technical_prereqs_met'?'Technical prerequisites met'
    :'Runtime readiness not claimed';
  o.innerHTML=`<div class="tier">Technical checks · establishable in a replay</div>
    <div class="gates">${tech.map(gate).join('')}</div>
    <div class="tier">Field &amp; commercial gates · require a real trial</div>
    <div class="gates">${fld.map(gate).join('')}</div>
    <div class="verdict"><b>${esc(head)}</b>
      <span class="ek">${esc(d.evidence_kind||'')}</span>
      <div class="stat">${techPass}/${tech.length} technical checks pass ·
        completion ${((c.rate||0)*100).toFixed(1)}% · ${c.correct_on_time}/${c.expected} correct on-time ·
        ${d.verified_cases} read-backs · ${d.published_effects} logical effects ·
        simulated p95 ${p95}s · ${c.human_touch} human touches</div>
      <div class="caveat">${esc(d.caveat||'')}</div></div>`;
  [...o.querySelectorAll('.gate')].forEach((g,i)=>setTimeout(()=>g.classList.add('show'),i*80));
}

/* real durable-runtime fault drills */
async function faultDrill(){
  openOverlay(`<button class="ovx" onclick="closeOverlay()">✕</button>
    <h2>Fault-drill theatre</h2>
    <div class="sub">real durable-runtime drills on an isolated tenant · no external effects</div>
    <div id="drillout"><div class="dload">◈ injecting fault…</div></div>`);
  let d;try{d=await(await fetch('/api/drill')).json();}catch(e){
    const o=document.getElementById('drillout');if(o)o.innerHTML='<div class="dload">drill failed</div>';return;}
  const o=document.getElementById('drillout');if(!o)return;
  const r=d.recovery||{},ks=d.killswitch||{},arm=ks.armed||{},dis=ks.disarmed||{};
  o.innerHTML=`
    <div class="act"><div class="acth">Act 1 · Worker crash &amp; epoch fencing</div>
      <div class="flow">
        <div class="fbox"><b>Worker A</b><span>task ${esc(r.task_id||'—')} · leased</span>
          <em class="dead">crashed · epoch ${esc(r.crashed_epoch)}</em></div>
        <div class="farrow">→</div>
        <div class="fbox ok"><b>Scheduler</b><span>${esc(r.runbook||'recover')}</span>
          <em>recovered · epoch ${esc(r.recovered_epoch)}</em></div>
        <div class="farrow">→</div>
        <div class="fbox ${r.fenced?'fenced':''}"><b>Worker A · late commit</b>
          <span>stale epoch ${esc(r.crashed_epoch)}</span>
          <em>${r.fenced?'FENCED ✕':'—'}</em></div>
      </div>
      <div class="actnote">${esc(r.detail||'')} — the crashed worker's late write is rejected by
        lease epoch, so exactly one logical effect can survive.</div></div>
    <div class="act"><div class="acth">Act 2 · Global stop (kill-switch)</div>
      <div class="ksrow">
        <div class="kscol armed"><div class="kh">STOP armed</div>
          <div class="kd ${arm.ready?'yes':'no'}">${esc((arm.decision||'').toUpperCase())}</div>
          <div class="kr">${esc(arm.reason||'')}</div></div>
        <div class="kscol"><div class="kh">STOP disarmed</div>
          <div class="kd ${dis.ready?'yes':'no'}">${esc((dis.decision||'').toUpperCase())}</div>
          <div class="kr">${esc(dis.reason||'')}</div></div></div>
      <div class="actnote">A capability stop makes every pending attestation
        <b style="color:var(--red)">DENY</b> before it can reach the executor — the same
        proposal is only authorized once the stop is lifted.</div></div>`;
}

/* fleet-map nodes open the case drawer */
document.addEventListener('click',e=>{const n=e.target.closest('.node[data-id]');
  if(n)openCase(n.getAttribute('data-id'));});
"""


# --------------------------------------------------------------------------- render


def _ring(pct: float) -> str:
    r = 66
    circ = 2 * math.pi * r
    dash = circ * (1 - max(0.0, min(1.0, pct)))
    return f"""
    <div class="ringwrap">
      <svg width="150" height="150" viewBox="0 0 150 150">
        <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#2ee6c9"/><stop offset="1" stop-color="#5aa2ff"/>
        </linearGradient></defs>
        <circle cx="75" cy="75" r="{r}" fill="none" stroke="rgba(255,255,255,.08)"
          stroke-width="10"/>
        <circle class="progress" cx="75" cy="75" r="{r}" fill="none" stroke="url(#g)"
          stroke-width="10" stroke-linecap="round"
          stroke-dasharray="{circ:.1f}" stroke-dashoffset="{circ:.1f}" data-dash="{dash:.1f}"
          style="transition:stroke-dashoffset 1.3s cubic-bezier(.2,.8,.2,1)"/>
      </svg>
      <div class="val"><b class="count" data-to="{pct*100:.0f}" data-suf="%"></b>
        <span>C / N</span></div>
    </div>"""


def render_page(session: Session, account_id: UUID) -> str:
    report = get_daily_report(
        session, account_id=account_id,
        period_start=PS - timedelta(days=1), period_end=PE + timedelta(days=1), now=NOW,
    ).render()
    health = get_service_health(session, account_id=account_id, now=NOW).render()
    counts: dict[str, int] = report["counts"]  # type: ignore[assignment]
    fleet_total: dict[str, Any] = report["fleet_total"]  # type: ignore[assignment]
    expected = counts["expected"] or 1
    pct = counts["complete"] / expected

    vessels: list[dict[str, Any]] = []
    for summary in report["cases"]:
        cv = get_case_view(session, account_id=account_id, case_id=UUID(summary["case_id"]))
        v = cv.render() if cv is not None else {}
        vals = _values(v)
        vessels.append({
            "case_id": summary["case_id"],
            "name": _resolve_name(session, account_id, UUID(summary["case_id"])),
            "pres": summary["presentation_status"],
            "automation": summary.get("automation_status", ""),
            "actual": vals.get("actual"), "planned": vals.get("planned"),
            "delta": vals.get("delta"), "df": _f(vals.get("delta")),
            "percent": vals.get("percent"),
            "evidence": v.get("evidence_status", ""),
            "next": v.get("next_machine_step"),
            "hash": (v.get("receipts", {}) or {}).get("content_hash"),
        })
    maxabs = max((abs(x["df"]) for x in vessels if x["df"] is not None), default=1.0) or 1.0
    verified = sum(1 for x in vessels if x["evidence"] == "confirmed")
    net = fleet_total["groups"][0]["total_delta"] if fleet_total["groups"] else "0"

    # ---- table rows
    rows = []
    for x in vessels:
        df = x["df"]
        cls = "flat" if not df else ("up" if df > 0 else "down")
        arrow = "▲" if df and df > 0 else ("▼" if df and df < 0 else "•")
        pcttxt = "" if x["percent"] is None else f'{arrow} {html.escape(x["percent"])}%'
        actual = "&mdash;" if x["actual"] is None else html.escape(x["actual"]) + " t"
        planned = "&mdash;" if x["planned"] is None else html.escape(x["planned"]) + " t"
        delta = "&mdash;" if x["delta"] is None else f'{arrow} {html.escape(x["delta"])} t'
        hsh = x["hash"]
        rows.append(f"""
        <tr class="clickable" data-id="{html.escape(x["case_id"])}">
          <td class="vn">{html.escape(x["name"])}<small>{html.escape(x["automation"])}</small></td>
          <td>{_pill(x["pres"])}</td>
          <td class="num">{actual}</td>
          <td class="num">{planned}</td>
          <td class="num {cls}">{delta}</td>
          <td class="num {cls}">{pcttxt}</td>
          <td>{_pill(x["evidence"]) if x["evidence"] else "&mdash;"}</td>
          <td class="hash">{'&mdash;' if not hsh else '<b>'+html.escape(hsh[:8])+'</b>…'+html.escape(hsh[-4:])}</td>
        </tr>""")

    # ---- variance chart
    vrows = []
    for x in vessels:
        if x["df"] is None:
            continue
        df = x["df"]
        w = abs(df) / maxabs * 50.0
        side = "pos" if df > 0 else "neg"
        vrows.append(f"""
        <div class="varrow">
          <div class="nm">{html.escape(x["name"])}</div>
          <div class="track"><div class="mid"></div>
            <div class="bar {side}" style="--w:{w:.1f}%"></div></div>
          <div class="vv {('up' if df>0 else 'down')}">{'+' if df>0 else ''}{df:.2f} t</div>
        </div>""")

    ft_badge = ('<span class="pill" style="--c:var(--grn)"><i></i>complete</span>'
                if fleet_total["complete"]
                else '<span class="pill" style="--c:var(--amb)"><i></i>partial</span>')
    ft_lines = "".join(
        f'<div class="mini"><span class="lab">{html.escape(g["fuel_grade"])} '
        f'<span class="hash">· {g["vessel_count"]} vessels</span></span>'
        f'<span class="val {("up" if _f(g["total_delta"]) and _f(g["total_delta"])>0 else "down")}">'
        f'{"+" if _f(g["total_delta"]) and _f(g["total_delta"])>0 else ""}'
        f'{html.escape(g["total_delta"])} {html.escape(g["unit"])}</span></div>'
        for g in fleet_total["groups"]
    ) or '<div class="mini"><span class="lab">no reconciled quantities</span></div>'

    cap_lines = "".join(
        f'<div class="mini"><span class="lab">{html.escape(c["capability"])}</span>'
        f'<span class="val {"ok" if c["available"] else "off"}">'
        f'{"● available" if c["available"] else "○ " + html.escape(str(c["disabled_reason"]))}'
        f'</span></div>'
        for c in health["capabilities"]
    )

    empty = report["empty_state"]
    banner = "" if empty == "none" else (
        f'<div class="banner reveal">⚠ empty-state · {html.escape(empty.replace("_"," "))}</div>')

    net_f = _f(net) or 0.0
    map_svg = _fleet_map(vessels)

    auth = _authority(session, account_id)
    gov_html = ""
    if auth:
        ext = ('<div class="v yes">enabled</div>' if auth["external"]
               else '<div class="v no">DISABLED</div>')
        fin = ('<div class="v yes">enabled</div>' if auth["financial"]
               else '<div class="v no">DISABLED</div>')
        dest = ", ".join(auth["destinations"])
        gov_html = f"""
  <section class="panel reveal" style="margin-top:22px">
    <div class="phead"><h2>Machine authority · governance</h2>
      <span class="meta">work order 6 · attested &amp; re-checked at dispatch</span></div>
    <div class="gov">
      <div class="killwrap">
        <div class="switch"><div class="knob"></div><b>ARMED</b></div>
        <div class="sub">Capability <code>{html.escape(auth["capability"])}</code> is provisioned
          and live. Grants come only from controlled provisioning — never minted at runtime.</div>
        <div class="arm">GLOBAL&nbsp;STOP available · a capability stop makes every pending
          attestation <span>DENY</span> before it can reach the executor.
          Prove it live → <b>Inject fault drill</b>.</div>
      </div>
      <div class="grantgrid">
        <div class="gv"><div class="k">Service principal</div>
          <div class="v">{html.escape(auth["subject"])}</div></div>
        <div class="gv"><div class="k">Deployment fingerprint</div>
          <div class="v">{html.escape(auth["fingerprint"])}</div></div>
        <div class="gv"><div class="k">Policy</div>
          <div class="v">{html.escape(auth["policy"])}</div></div>
        <div class="gv"><div class="k">Attestation TTL</div>
          <div class="v">{auth["ttl"]}s</div></div>
        <div class="gv"><div class="k">Allowed destination</div>
          <div class="v">{html.escape(dest)}</div></div>
        <div class="gv"><div class="k">Signing key</div>
          <div class="v">{html.escape(auth["signing_key"])}</div></div>
        <div class="gv"><div class="k">External messages</div>{ext}</div>
        <div class="gv"><div class="k">Financial commitments</div>{fin}</div>
      </div>
    </div>
  </section>"""

    body = f"""
<canvas id="stars"></canvas>
<header class="topbar">
  <div class="brand"><span class="logo">◈</span> MARITIME
     <em>AUTONOMOUS&nbsp;RECONCILIATION</em></div>
  <div class="sysline">
    <span class="hash">v0016 · zero-touch</span>
    <span class="live"><i></i> LOCAL SIMULATION</span>
    <span class="clock" id="clock">--:--:-- UTC</span>
  </div>
</header>
<div class="wrap">
  <section class="hero">
    <div class="reveal">
      <div class="crumbs">DESIGN PARTNER SHIPPING · FLEET RECONCILIATION</div>
      <h1>Private Daily<br>Fleet Report</h1>
      <div class="crumbs">Interval 2026-09-07 → 2026-09-08 · fictional fleet data · no external effects</div>
      <span class="tag">✓ authorized · committed · read-back verified</span>
    </div>
    <div class="panel gauge reveal">
      {_ring(pct)}
      <div class="legend">
        <h3>Analytical completion</h3>
        <p>Correct &amp; on-time cases over the frozen expected-work ledger.</p>
        <div class="lg">
          <div><b class="count" data-to="{verified}"></b>verified</div>
          <div><b class="count" data-to="{counts['unresolved']}"></b>unresolved</div>
          <div><b class="count" data-to="0"></b>human&nbsp;touches</div>
        </div>
      </div>
    </div>
  </section>

  <section class="kpis">
    <div class="panel kpi reveal" style="--a:var(--cyan)"><div class="k">Expected jobs</div>
      <div class="v count" data-to="{counts['expected']}"></div></div>
    <div class="panel kpi reveal" style="--a:var(--grn)"><div class="k">Complete</div>
      <div class="v count" data-to="{counts['complete']}"></div></div>
    <div class="panel kpi reveal" style="--a:var(--amb)"><div class="k">Unresolved</div>
      <div class="v count" data-to="{counts['unresolved']}"></div></div>
    <div class="panel kpi reveal" style="--a:var(--blue)"><div class="k">Awaiting feed</div>
      <div class="v count" data-to="{counts['awaiting']}"></div></div>
    <div class="panel kpi reveal" style="--a:var(--red)"><div class="k">Net fuel variance</div>
      <div class="v"><span class="count" data-to="{net_f:.2f}" data-dec="2"
        data-pre="{'+' if net_f>0 else ''}"></span><span class="u">t VLSFO</span></div></div>
  </section>

  {banner}

  <section class="grid2">
    <div class="panel reveal">
      <div class="phead"><h2>Fleet ledger</h2>
        <span class="meta">{len(vessels)} cases · deterministic · ROUND_HALF_UP@2</span></div>
      <table>
        <thead><tr><th>Vessel</th><th>Status</th><th class="num">Reported</th>
          <th class="num">Planned</th><th class="num">Variance</th><th class="num">Δ%</th>
          <th>Evidence</th><th>Receipt</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    <div>
      <div class="panel reveal" style="margin-bottom:22px">
        <div class="phead"><h2>Variance · plan vs reported</h2>
          <span class="meta">tonnes, diverging</span></div>
        {''.join(vrows)}
      </div>
      <div class="panel reveal" style="margin-bottom:22px">
        <div class="phead"><h2>Fleet total {ft_badge}</h2>
          <span class="meta">compatible only</span></div>
        {ft_lines}
      </div>
      <div class="panel reveal">
        <div class="phead"><h2>Service health</h2><span class="meta">per capability</span></div>
        {cap_lines}
      </div>
    </div>
  </section>

  <section class="panel reveal" style="margin-top:22px">
    <div class="phead"><h2>Fleet constellation</h2>
      <span class="meta">live status · click a vessel</span></div>
    <div class="mapwrap">{map_svg}</div>
  </section>

  {gov_html}

  <section class="trust">
    <div class="chip reveal"><span class="dot"></span><b>{verified}</b>&nbsp;private read-backs
       matched content hash</div>
    <div class="chip reveal"><span class="dot"></span>one logical effect per case
       &nbsp;<code>action_key bound</code></div>
    <div class="chip reveal"><span class="dot"></span>zero runtime human interventions</div>
    <div class="chip reveal warn"><span class="dot"></span>no approve / reject &mdash;
       customer sees the outcome</div>
  </section>

  <div class="note reveal">
    Every value is labelled <em>reported</em> (noon connector) or <em>calculated</em>
    (deterministic variance). A case reads <em>complete</em> only after its private version is
    authorized, committed and independently read back to the same content hash; a safe abstention
    reads <em>unresolved</em> and is never disguised as green. The command dock runs the real
    backend on isolated tenants — <em>Run synthetic replay</em> drives the whole work-order-10
    pilot harness and shows its simulated replay gates; real latency remains unmeasured;
    <em>Inject fault drill</em>
    fences a stale worker commit by lease epoch and denies a proposal under a capability stop.
    Rendered live from the work-order-8 read model — a developer viewer of the backend, not a
    shipped product UI.
  </div>
</div>

<nav class="dock">
  <button class="primary" onclick="runPilot()"><span class="ic">▶</span>
    <span class="lbl">Run synthetic replay</span></button>
  <button class="warnb" onclick="faultDrill()"><span class="ic">⚠</span>
    <span class="lbl">Inject fault drill</span></button>
  <span class="sep"></span>
  <button id="themebtn" class="icon-only" onclick="toggleTheme()"><span class="ic">☾</span>
    <span class="lbl">Midnight</span></button>
</nav>

<div class="ov" id="overlay"><div class="ovcard"><div id="ovbody"></div></div></div>
<div class="scrim" id="scrim"></div>
<aside class="drawer" id="drawer"><div id="dbody"></div></aside>"""

    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>MariTime · Fleet Reconciliation Console</title>"
            "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
            "<link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>"
            "<link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&"
            "family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;500&"
            "display=swap\" rel=\"stylesheet\">"
            f"<style>{PAGE_CSS}{PAGE_CSS_EXTRA}{PAGE_CSS_EXTRA2}{PAGE_CSS_POLISH}</style>"
            f"</head><body>{body}"
            f"<script>{PAGE_JS}{PAGE_JS_EXTRA}{PAGE_JS_EXTRA2}</script></body></html>")


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, obj: dict[str, Any]) -> None:
        self._send(json.dumps(obj).encode("utf-8"), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/case":
            ids = parse_qs(parsed.query).get("id", [])
            try:
                detail = case_detail(SESSION, ACCOUNT_ID, UUID(ids[0])) if ids else {}
            except (ValueError, IndexError):
                detail = {}
            self._json(detail)
            return
        if parsed.path == "/api/run":
            try:
                self._json(run_pilot_report())
            except Exception as exc:  # noqa: BLE001 - dev tool: surface the message
                self._json({"error": str(exc)})
            return
        if parsed.path == "/api/drill":
            try:
                self._json(fault_drill_report())
            except Exception as exc:  # noqa: BLE001 - dev tool: surface the message
                self._json({"error": str(exc)})
            return
        if parsed.path in ("/", "/index.html"):
            self._send(render_page(SESSION, ACCOUNT_ID).encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_: Any) -> None:
        return


SESSION, ACCOUNT_ID = _seed()

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"MariTime recon console at http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    server.serve_forever()
