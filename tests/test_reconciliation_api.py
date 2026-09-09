"""HTTP integration tests for the account-scoped reconciliation customer API (blueprint 25).

Drives the real FastAPI app with a SQLite-backed session, a seeded DATA-tier account and API
key, and asserts: authenticated ingestion creates a case under the key's account; the owning
account can read its case; another account's case is reported as not found (non-disclosing);
scope and authentication are enforced.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from eastmed_api.database import get_db
from eastmed_api.main import app
from eastmed_api.security import account_api_key_hash
from eastmed_schema.base import Base
from eastmed_schema.enums import (
    AccountTier,
    AuthorityRole,
    AutomationStatus,
    BusinessStatus,
    RecordKind,
)
from eastmed_schema.models import (
    Account,
    AccountApiKey,
    ReconExpectedJob,
    ReconOperationalCase,
    ReconVesselMembership,
)
from eastmed_shared.reconciliation.contract import CONTRACT_VERSION
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

PERIOD_START = "2026-09-07T00:00:00Z"
PERIOD_END = "2026-09-08T00:00:00Z"

TABLES = [n for n in Base.metadata.tables if n.startswith("recon_")] + [
    "accounts",
    "account_api_keys",
]


@pytest.fixture
def client() -> Iterator[tuple[TestClient, Session, dict[str, str]]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection: Any, _: Any) -> None:  # pragma: no cover - setup
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[Base.metadata.tables[n] for n in TABLES])
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()

    tokens = _seed(session)

    def _override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app), session, tokens
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


def _issue_key(session: Session, account_id: UUID, *, prefix: str, scopes: list[str]) -> str:
    secret = "s" * 40
    token = f"em_live_{prefix}.{secret}"
    session.add(
        AccountApiKey(
            account_id=account_id,
            name=f"key-{prefix}",
            key_prefix=prefix,
            secret_hash=account_api_key_hash(token),
            scopes_json=scopes,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            created_by="test",
        )
    )
    session.flush()
    return token


def _account(session: Session, company: str) -> UUID:
    account = Account(
        company=company,
        tier=AccountTier.DATA,
        contract_start=date(2026, 1, 1),
        contract_end=date(2027, 1, 1),
    )
    session.add(account)
    session.flush()
    return account.id


def _seed(session: Session) -> dict[str, str]:
    account_a = _account(session, "Design Partner A")
    session.add(
        ReconVesselMembership(
            account_id=account_a, vessel_ref="V3", authority_role=AuthorityRole.OPERATOR,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC), valid_to=datetime(2027, 1, 1, tzinfo=UTC),
        )
    )
    session.add(
        ReconExpectedJob(
            account_id=account_a, vessel_ref="V3", voyage_ref="P4",
            period_start=datetime(2026, 9, 7, tzinfo=UTC),
            period_end=datetime(2026, 9, 8, tzinfo=UTC),
            due_at=datetime(2026, 9, 8, 6, tzinfo=UTC), contract_version=CONTRACT_VERSION,
            result_contract_version="result_contract_v1",
            required_record_kind=RecordKind.DAILY_REPORT,
        )
    )
    session.flush()

    # A second account with its own case, used to prove non-disclosure across tenants.
    account_b = _account(session, "Rival B")
    job_b = ReconExpectedJob(
        account_id=account_b, vessel_ref="ZZ9", voyage_ref="QQ",
        period_start=datetime(2026, 9, 7, tzinfo=UTC), period_end=datetime(2026, 9, 8, tzinfo=UTC),
        due_at=datetime(2026, 9, 8, 6, tzinfo=UTC), contract_version=CONTRACT_VERSION,
        result_contract_version="result_contract_v1", required_record_kind=RecordKind.DAILY_REPORT,
    )
    session.add(job_b)
    session.flush()
    case_b = ReconOperationalCase(
        account_id=account_b, expected_job_id=job_b.id,
        automation_status=AutomationStatus.VALIDATED, business_status=BusinessStatus.PENDING,
        revision=1, deadline_at=datetime(2026, 9, 8, 6, tzinfo=UTC),
    )
    session.add(case_b)
    session.flush()

    tokens = {
        "full": _issue_key(
            session, account_a, prefix="fullkeyAB1234", scopes=["recon:read", "recon:ingest"]
        ),
        "readonly": _issue_key(
            session, account_a, prefix="readonlyAB123", scopes=["recon:read"]
        ),
        "noscope": _issue_key(session, account_a, prefix="noscopeAB1234", scopes=["events:read"]),
        "account_b_case_id": str(case_b.id),
    }
    session.commit()
    return tokens


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _plan() -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_VERSION,
        "record_kind": "voyage_plan",
        "source_system": "planner",
        "external_ref": "P4", "source_revision": "1", "vessel_ref": "V3",
        "recorded_at": "2026-09-06T00:00:00Z", "voyage_ref": "P4", "plan_revision": "rev-1",
        "effective_from": "2026-09-07T00:00:00Z", "effective_to": "2026-09-14T00:00:00Z",
        "items": [{
            "item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": "23.000", "unit": "tonne",
            "period_start": PERIOD_START, "period_end": PERIOD_END,
        }],
    }


def _report() -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_VERSION, "record_kind": "daily_report",
        "source_system": "noon-connector", "external_ref": "R17", "source_revision": "1",
        "vessel_ref": "V3", "recorded_at": "2026-09-08T06:00:00Z", "voyage_ref": "P4",
        "period_start": PERIOD_START, "period_end": PERIOD_END,
        "items": [{
            "item_key": "vlsfo", "fuel_grade": "VLSFO", "quantity": "24.600", "unit": "tonne",
        }],
    }


# --- Authenticated ingestion + own-account reads ------------------------------------


def test_ingestion_creates_a_case_scoped_to_the_key_account(
    client: tuple[TestClient, Session, dict[str, str]],
) -> None:
    api, _session, tokens = client
    plan = api.post("/api/v1/recon/ingest", json=_plan(), headers=_auth(tokens["full"]))
    assert plan.status_code == 200
    report = api.post("/api/v1/recon/ingest", json=_report(), headers=_auth(tokens["full"]))
    assert report.status_code == 200
    body = report.json()
    assert body["status"] == "accepted"
    case_id = body["case_id"]
    assert case_id is not None

    listing = api.get("/api/v1/recon/cases", headers=_auth(tokens["full"]))
    assert listing.status_code == 200
    assert case_id in {item["case_id"] for item in listing.json()["items"]}

    view = api.get(f"/api/v1/recon/cases/{case_id}", headers=_auth(tokens["full"]))
    assert view.status_code == 200
    assert view.json()["case_id"] == case_id


def test_other_accounts_case_is_reported_not_found(
    client: tuple[TestClient, Session, dict[str, str]],
) -> None:
    api, _session, tokens = client
    other = tokens["account_b_case_id"]
    resp = api.get(f"/api/v1/recon/cases/{other}", headers=_auth(tokens["full"]))
    assert resp.status_code == 404  # non-disclosing: never confirmed to exist
    timeline = api.get(f"/api/v1/recon/cases/{other}/timeline", headers=_auth(tokens["full"]))
    assert timeline.status_code == 404


# --- Authentication and scope enforcement -------------------------------------------


def test_missing_credentials_are_unauthorized(
    client: tuple[TestClient, Session, dict[str, str]],
) -> None:
    api, _session, _tokens = client
    assert api.get("/api/v1/recon/cases").status_code == 401
    assert api.get("/api/v1/recon/cases", headers=_auth("em_live_bogus.xxx")).status_code == 401


def test_scope_is_enforced_per_route(
    client: tuple[TestClient, Session, dict[str, str]],
) -> None:
    api, _session, tokens = client
    # A key without any recon scope cannot read.
    assert api.get("/api/v1/recon/cases", headers=_auth(tokens["noscope"])).status_code == 403
    # A read-only key can read but cannot ingest.
    assert api.get("/api/v1/recon/cases", headers=_auth(tokens["readonly"])).status_code == 200
    ingest = api.post("/api/v1/recon/ingest", json=_report(), headers=_auth(tokens["readonly"]))
    assert ingest.status_code == 403


# --- Report and health views --------------------------------------------------------


def test_daily_report_and_health_are_account_scoped(
    client: tuple[TestClient, Session, dict[str, str]],
) -> None:
    api, _session, tokens = client
    health = api.get("/api/v1/recon/health", headers=_auth(tokens["full"]))
    assert health.status_code == 200
    assert "capabilities" in health.json()

    report = api.get(
        "/api/v1/recon/daily-report",
        params={"period_start": "2026-09-06T00:00:00Z", "period_end": "2026-09-09T00:00:00Z"},
        headers=_auth(tokens["full"]),
    )
    assert report.status_code == 200
    assert report.json()["counts"]["expected"] >= 1


def test_membership_denied_record_is_a_client_error(
    client: tuple[TestClient, Session, dict[str, str]],
) -> None:
    api, _session, tokens = client
    # A vessel this account does not own is rejected without persisting a case.
    stranger = {**_report(), "vessel_ref": "NOT-OWNED", "external_ref": "RX", "voyage_ref": "QX"}
    resp = api.post("/api/v1/recon/ingest", json=stranger, headers=_auth(tokens["full"]))
    assert resp.status_code == 403
